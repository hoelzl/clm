# Cassette Partial Quarantine

## Status: SKETCH

**Last Updated**: 2026-05-25
**Related problem**: Builds that time out or crash mid-execution lose
all HTTP recordings made during that session, forcing the next attempt
to re-record from zero. For decks that issue many slow LLM calls (e.g.
reasoning models with multi-minute response times), a single timeout
can waste an entire session's worth of API spend and wall-clock time.
The previous "merge partials into canonical" policy was deliberately
removed in PR #123 (issue #115) because partial chains poisoned the
canonical cassette permanently — first-seen-wins dedup meant an orphan
chain-opener whose closer was never recorded would survive every
subsequent build and break replay forever.
**Conversation that produced this sketch**: see ~2026-05-25 session
discussing alternatives to the discard policy.

## Goal

Recover the per-session work that the current policy discards, **without
ever risking poisoning the canonical cassette**. The user's original
intent ("two or three retries should finish a build") becomes
achievable; PR #123's safety guarantee stays intact.

## Non-goals

- Detecting "chains" in HTTP traffic. As discussed in the design
  conversation, reliable chain detection is provider-specific and
  brittle. This design avoids it entirely.
- Replacing `--refresh`. A genuinely-corrupted canonical still requires
  the existing refresh flow; this design only changes how partials are
  treated *before* they touch canonical.
- Changing the matcher chain, the bootstrap template, or vcrpy
  integration in `notebook_processor.py`. The kernel-side code is
  untouched.

## Current behavior (recap)

The discard policy lives in three places. Each is small; the design
below modifies each in a focused way.

1. `src/clm/workers/notebook/http_replay_cassette.py` —
   `merge_staging_into_canonical(..., sweep_orphans=True)` deletes
   markerless staging files (lines 244-250). Called from the pre-build
   sweep.
2. `src/clm/workers/notebook/http_replay_cassette.py` —
   `seed_staging_from_canonical` copies *only* canonical into the
   worker's staging file (line 94).
3. `src/clm/workers/notebook/notebook_processor.py` —
   `_persist_recorded_cassette` writes the completion marker only on
   the success path (line 1543). Unchanged in this design.

Pre-build sweep entry point: `Course._sweep_orphan_cassette_staging_files`
in `src/clm/core/course.py:349`, called from `process_all` (line 327)
and `process_file` (line 305) before any worker starts.

## Design

### File layout

A new sibling file class lives next to canonical:

```
slides.http-cassette.yaml                       # canonical (sacred)
slides.http-cassette.yaml.partial-<id>          # quarantined partials
slides.http-cassette.yaml.staging-<pid>-<uuid>  # active workers (unchanged)
slides.http-cassette.yaml.staging-<...>.completed  # success markers (unchanged)
slides.http-cassette.yaml.lock                  # merge lock (unchanged)
```

The `.partial-<id>` files are produced by the pre-build sweep when it
encounters a markerless staging from a previous build. They are read
(but never written to) by future workers. They are deleted by the next
successful merge.

Naming: `<id>` is the original staging's `<pid>-<uuid>` suffix, preserved
so debug logs can correlate partials back to the build that produced
them.

### Pre-build sweep (modified)

`merge_staging_into_canonical(..., sweep_orphans=True)` changes one
branch: instead of unlinking markerless staging files (lines 244-250),
**rename** them to `.partial-<id>`:

```python
if sweep_orphans:
    for staging_path in markerless:
        partial_path = canonical.parent / (
            staging_path.name.replace(_STAGING_SUFFIX, _PARTIAL_SUFFIX, 1)
        )
        logger.info(
            f"Quarantining partial staging cassette '{staging_path}' "
            f"as '{partial_path.name}' (no completion marker; will be "
            f"available to next workers but never folded into canonical "
            f"until a complete session succeeds)."
        )
        try:
            staging_path.rename(partial_path)
        except OSError as exc:
            logger.warning(
                f"Could not quarantine '{staging_path}' ({exc}); "
                f"discarding (legacy behavior)."
            )
            _delete_quietly(staging_path)
```

The markered-staging branch above is unchanged: marker-complete sessions
still fold into canonical and the staging is deleted as today.

### Worker seed (modified)

`seed_staging_from_canonical` is extended to layer partials on top of
canonical, in-memory, into the worker's staging file:

```python
def seed_staging_from_canonical(paths: CassettePaths) -> None:
    paths.staging.parent.mkdir(parents=True, exist_ok=True)

    # 1. Start from canonical if it exists.
    canonical_requests, canonical_responses = ([], [])
    if paths.canonical.exists():
        canonical_requests, canonical_responses = (
            FilesystemPersister.load_cassette(
                paths.canonical, serializer=yamlserializer
            )
        )

    # 2. Layer in every sibling .partial-* file, deduplicating against
    #    canonical and against each other (first-seen-wins, same as
    #    canonical merge).
    seen = {_dedup_key(r) for r in canonical_requests}
    reqs = list(canonical_requests)
    resps = list(canonical_responses)
    partial_glob = f"{paths.canonical.name}{_PARTIAL_SUFFIX}*"
    for partial in sorted(paths.canonical.parent.glob(partial_glob)):
        try:
            p_reqs, p_resps = FilesystemPersister.load_cassette(
                partial, serializer=yamlserializer
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Skipping unreadable partial '{partial}': {exc}")
            continue
        for req, resp in zip(p_reqs, p_resps, strict=False):
            key = _dedup_key(req)
            if key in seen:
                continue
            reqs.append(req); resps.append(resp); seen.add(key)

    # 3. Write the merged in-memory result to staging. Canonical is not
    #    touched on disk.
    payload = vcr_serialize(
        {"requests": reqs, "responses": resps}, yamlserializer,
    )
    _atomic_write_text(paths.staging, payload)
```

The kernel's vcrpy sees one cassette and doesn't care that its contents
came from two sources. Eager-append (already in the bootstrap template,
`notebook_processor.py:325-332`) keeps writing to the same staging file
as today, so a newly-crashed session produces a fresh markerless staging
that the *next* pre-build sweep will quarantine into yet another
`.partial-*`.

### Successful merge cleanup (modified)

When `merge_staging_into_canonical` folds a marker-complete staging into
canonical, it currently deletes only that staging and its marker. With
quarantine, it should *also* delete every `.partial-*` sibling: anything
the partial contained has already been replayed by the successful
worker (which seeded from canonical + partials), so vcrpy in
`new-episodes`/`once`/`refresh` mode would have re-recorded any
referenced interaction into the worker's staging, and the merge then
promotes it into canonical. Partials become redundant the moment a
complete session lands.

```python
if markered:
    # ... existing fold + atomic_write_text(canonical, ...) ...

    for staging_path in markered:
        _delete_quietly(staging_path)
        _delete_quietly(marker_path(staging_path))

    # NEW: a complete session has landed; partials are now subsumed.
    partial_glob = f"{canonical.name}{_PARTIAL_SUFFIX}*"
    for partial in canonical.parent.glob(partial_glob):
        _delete_quietly(partial)
```

This keeps the partial set bounded across long retry chains: each
successful session resets it to empty.

### Mode interactions

The design pairs cleanly with existing replay modes:

| Mode | Behavior on partial-replayed request | Behavior on cache miss |
|---|---|---|
| `replay` (CI default) | Replays from partial | Raises (loudly) |
| `new-episodes` (local default) | Replays from partial | Records new |
| `once` | Replays from partial | Records if cassette is new |
| `refresh` | Ignores partial (record mode `all`) | Records |
| `disabled` | N/A | N/A |

In `replay` mode (the CI strict path), a partial whose chain was
incomplete will cause a `CannotOverwriteExistingCassetteException` at
the missing-closer call — but this is exactly the current desired CI
behavior for stale cassettes. CI shouldn't be running partial recovery
in the first place; the canonical it tests against was committed by an
author who completed a session locally.

In `new-episodes` (the practical retry path), a missing closer is
re-recorded fresh. The LLM is re-called; the user pays for one call
instead of the whole session.

## Lifecycle walkthrough

### Scenario A: build crashes, no prior canonical

1. Build starts. `_sweep_orphan_cassette_staging_files` finds nothing.
2. Worker starts. `seed_staging_from_canonical` finds no canonical
   and no partials → empty staging.
3. Notebook executes, vcrpy records N interactions eagerly to staging.
4. Build crashes at interaction N.
5. `_persist_recorded_cassette(execution_succeeded=False)` runs:
   no marker written. Staging file remains on disk.
6. User reruns the build.
7. Pre-build sweep finds the markerless staging → **renames to
   `.partial-<id>`** (new behavior).
8. Next worker seeds from canonical (empty) + partial (N entries) →
   staging has N entries.
9. Notebook replays first N interactions from staging, then makes
   interactions N+1, N+2, … live; eager-append writes to staging.
10. If the build completes: marker written, merge folds staging into
    canonical, partial files deleted. Canonical now has all of session 1
    + 2's interactions.
11. If the build crashes again at N+M: another markerless staging,
    quarantined as `.partial-<id-2>` on the next pre-build sweep. Worker
    seed now layers canonical (still empty) + 2 partials.

### Scenario B: build completes successfully on first try

Identical to today. No partials exist, none are produced. Canonical is
written normally. Zero overhead.

### Scenario C: build runs against fully-recorded canonical

Identical to today. No partials exist; seed copies canonical →
staging; vcrpy replays. If mode is `new-episodes` and a request misses,
a new interaction is recorded; on success the merge updates canonical.

### Scenario D: poisoned partial breaks replay

A partial recording happens to contain an interaction whose response
references state (e.g. an Anthropic `tool_use_id`) that the next call
won't regenerate.

- `new-episodes`: vcrpy serves the bad response; downstream code may
  raise. The user inspects, runs `clm cassette purge-partials <topic>`
  (small new CLI, see "Open questions"), and retries. The canonical
  was never modified.
- `replay`: misses on the request that should have generated a new
  `tool_use_id`; raises `CannotOverwriteExistingCassetteException`.
  Same recovery path.

The key property: recovery is `rm <name>.partial-*` (or a CLI wrapper).
The canonical is untouched, so the worst case is "lose the partial-
session work, fall back to today's behavior."

## Code touch-points

Approximate line counts. All in existing files; no new modules.

| File | Change | LOC |
|---|---|---|
| `src/clm/workers/notebook/http_replay_cassette.py` | Add `_PARTIAL_SUFFIX`; rewrite sweep-orphans branch (rename, not delete); extend `seed_staging_from_canonical` to layer partials; extend successful-merge cleanup to delete partials | ~80 |
| `src/clm/infrastructure/utils/path_utils.py` | Add `.partial-*` to the ignored-output patterns (currently only `.staging-*` and `.completed` per line 89-90) | ~3 |
| `tests/workers/notebook/test_http_replay_cassette.py` | New tests for rename-on-sweep, layered seed, partial-cleanup-on-success, multi-partial layering, unreadable-partial-skipped | ~150 |
| `tests/core/test_course.py` (or wherever the sweep is tested) | Update assertions: markerless staging now becomes `.partial-*` instead of vanishing | ~30 |
| `src/clm/cli/info_topics/spec-files.md` and `commands.md` | Document partial files in cassette layout section; document the new behavior | ~20 |

Total: ~250-300 LOC + tests + docs. No CLI surface change in the core
proposal; see "Open questions" for the optional `clm cassette` admin
command.

## Open questions

1. **Bounded partial accumulation.** A pathological retry loop (10
   crashed sessions in a row) accumulates 10 partial files. Each worker
   seed reads all of them. Disk and seed time are both linear; nothing
   catastrophic, but worth bounding. Options:
   - Hard cap (e.g. keep most recent 5 partials, delete the rest on
     each sweep). Simple, opinionated.
   - Coalesce: pre-build sweep folds all existing partials into a
     single canonical-aligned `<name>.partial-merged.yaml` and deletes
     the per-session files. Saves disk but adds a merge that runs
     even when no successful build is in sight.
   - Do nothing. Trust that decks with 10 consecutive crashes have
     bigger problems than partial-file proliferation.
   - **Recommendation**: cap at 5, log when discarding the oldest.

2. **Admin CLI.** `clm cassette purge-partials [<topic>...]` would let
   users explicitly invalidate quarantined recordings without
   memorizing the file pattern. Strictly optional — `rm <pattern>`
   works. Worth adding if support burden warrants it; defer to a
   follow-up issue.

3. **CI behavior.** Should CI builds (`CI=true`) skip partial recovery
   entirely? Arguments for: CI is the authority on what canonical
   should produce; reading partials introduces a vector for a
   developer's local crash artifact to leak into a CI cassette (only if
   they commit it, which they shouldn't, but…). Arguments against:
   `.gitignore` already excludes `.staging-*` and the new
   `.partial-*` would be added there too, so the leak risk is small.
   - **Recommendation**: defer the decision; ship the default
     "everyone reads partials," add a `--no-partial-recovery` flag if
     CI burn surfaces a real problem.

4. **vcrpy's `_save` ordering on success.** The kernel's eager-append
   (`notebook_processor.py:325-332`) writes after every interaction.
   On successful kernel exit, vcrpy's `__exit__` also calls `_save`.
   With layered seed, the staging file already contains the partial
   contents on first write — confirm vcrpy doesn't truncate-then-write
   in a way that loses the seeded entries before any new ones land.
   Believed safe (vcrpy reads its own cassette state from memory on
   save, not from disk), but the test suite should cover the case of
   "start with seeded N entries, eager-append M new, verify N+M after
   `_save`."

5. **Concurrent workers (German+English) racing on partial creation.**
   The pre-build sweep is single-threaded by contract
   (`course.py:374-379`), so partial renaming is race-free. Worker
   seeds read partials but don't modify them, so concurrent reads are
   safe. Successful merge deletes partials under the file lock, so
   only one worker actually performs the delete. No additional
   locking needed.

6. **Test coverage of the chain-poisoning scenario.** The original
   issue #115 had a reproducer (chain-opener body depends on
   chain-closer response). Re-running that reproducer against this
   design should demonstrate that *canonical never receives the
   poisoned opener*, even though the partial would replay it. Worth
   adding as a regression test.

## Effort estimate

**Implementation**: 1-2 days for the core mechanics + tests, +0.5 day
for docs (info-topic updates, `.gitignore` adjustment, CHANGELOG).

**Risk**: Low. The change is additive in spirit (canonical handling is
unchanged on the success path; partials are a new file class with
narrow read/write paths). The failure mode is "behaves like today" —
worst case is partial-recovery silently doesn't happen and the user
re-records, same as the status quo.

**Validation**: Beyond unit tests, run the AZAV ML deck (the deck
that originally motivated this complaint) end-to-end through an
artificial mid-session abort and verify the next build picks up from
the recorded interactions.

## Decision

Pending review. See conversation 2026-05-25 for the alternatives this
sketch was chosen over (timeout-vs-crash distinction, opt-in flag,
periodic checkpoint marker, versioned canonical rollback).
