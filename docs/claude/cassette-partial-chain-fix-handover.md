# Handover: HTTP-Replay Cassette Partial-Chain Fix (Issue #115)

## 1. Feature Overview

Fix the structural bug in
`merge_staging_into_canonical` that lets an **aborted** recording session
permanently poison a canonical cassette by admitting a partial chain
(chain-opener recorded, chain-closer missing). The fix introduces a
**per-staging-file completion marker** so the merger can distinguish
"recording session ran to completion" from "kernel died / build aborted
/ cell raised mid-chain" and discards the latter's entries.

- **Issue**: [hoelzl/clm#115](https://github.com/hoelzl/clm/issues/115)
- **Filed as follow-up to**: #95 (closed) — §A `allow_playback_repeats`
  + §B spec-target snapshot fixes shipped clean; this is the remaining
  §C item from the PythonCourses redesign handover.
- **Branch**: not yet pushed; working tree on `master` in this worktree.
- **Worktree**: this one (`functional-forging-marble`).
- **Status**: Phases 1 (marker plumbing), 2 (discriminating merge),
  and 3 (end-to-end §C regression test) shipped to the worktree. PR
  not yet opened — the intent is to bundle all three phases into one
  PR.
- **Investigation environment**: CLM master at commit `45f1d5e`
  (PR #114 + Phase 7 v2 follow-up), Python 3.13 on Windows 11.

### What's broken today (concrete repro from the issue)

`slides_010_prompt_templates.py` in PythonCourses has a workshop cell
that defines `clarify_and_answer(vague)` — a function chaining two LLM
calls per loop iteration, where call-2's input depends on call-1's
response text. The cassette `slides_010_prompt_templates.http-cassette.yaml`
ends up with:

- Q1: clarify + tutor pair (intact chain).
- Q2: clarify + tutor pair (intact chain).
- Q3: **clarify only** — Q3's tutor request body (which embeds Q3's
  *currently stored* clarified text) is absent.

`--http-replay=replay` fails on Q3's tutor call:
`CannotOverwriteExistingCassetteException`. The cassette is internally
inconsistent — call-1's response cannot be reached without orphaning
call-2.

### Why the diagnosis in #115 is correct

Verified against the actual code:

1. `src/clm/workers/notebook/http_replay_cassette.py:141-162` —
   `merge_staging_into_canonical` is purely additive-by-`_dedup_key`
   (`(method, uri, body)`), with no awareness of inter-entry
   dependencies and no awareness of whether the staging file came
   from a completed session.

2. `src/clm/workers/notebook/notebook_processor.py:200-208` — the
   bootstrap's eager-save patch (`_clm_eager_append`) flushes the
   staging file on **every** successful `cassette.append`. By the
   time a kernel dies, partial recordings (chain-opener but no
   chain-closer) are already on disk.

3. `src/clm/workers/notebook/notebook_processor.py:1516-1528` —
   `_persist_recorded_cassette` runs in the `finally` block of
   `_create_using_nbconvert`, so the merge happens **regardless** of
   whether execution succeeded. The current behaviour is intentional
   — the comment on lines 1522-1527 explicitly says "skipping the
   merge would lose every interaction the kernel successfully
   recorded before the failure." The fix must keep that "don't lose
   recordings" goal in tension with "don't admit half-chains" — the
   marker approach threads that needle.

4. `src/clm/core/course.py:386` — the pre-build orphan sweep
   (`_sweep_orphan_cassette_staging_files`) inherits the same
   additive-only behaviour and folds in any orphan staging from a
   previously-killed worker without distinguishing complete from
   partial.

The poisoning mechanism described in #115 — "first-seen wins, partial
chain-opener becomes permanent, no subsequent completed run can
repair" — is exactly what the merge code does today.

### Scope caveats the issue itself flags

- **`--http-replay=refresh` is separately broken** for chain-poisoned
  cassettes by the same `_dedup_key` additive rule (canonical entry
  for the chain-opener body is never overwritten). Fixing refresh
  semantics is **out of scope** for this issue. The marker fix does
  not make refresh worse.
- **Conditional / try-except skipped cells in a successful run** can
  still produce partial chains (cell ran to completion, but a
  try/except internally swallowed the chain-closer). Out of scope;
  documented as a known limitation. Handled by the optional
  cassette-doctor follow-up.

## 2. Design Decisions

### Recommended approach: per-staging-file completion marker

Each worker writes a sentinel file
`<staging_path>.completed` **host-side**, after the kernel has
returned cleanly from `_execute_notebook_with_path`, **before** the
finally-block invokes `merge_staging_into_canonical`. The merge logic
becomes:

| Staging file | Marker present | Action |
|---|---|---|
| Own staging, this build | yes (host wrote on success path) | Fold entries into canonical |
| Own staging, this build | no (execution raised) | Leave on disk; pre-build sweep next time |
| Other worker's staging, mid-execution | no (worker still running) | **Leave alone** — don't merge, don't delete |
| Other worker's staging, completed | yes | Fold in, delete staging + marker (its merge crashed) |
| Orphan from previous build, completed | yes | Pre-build sweep folds in, deletes |
| Orphan from previous build, aborted | no | Pre-build sweep **discards entries**, deletes staging |

The discriminator between "leave markerless alone" (per-worker merge)
and "discard markerless" (pre-build sweep) is a new `sweep_orphans`
flag passed to `merge_staging_into_canonical`.

### Why host-side marker, not kernel-side `atexit`

The issue text suggests "register a final hook that writes the marker
after the last cell finishes." Inside the kernel, the natural
mechanism is `atexit`. **This is wrong** because:

- `atexit` fires on **graceful kernel shutdown**, including the
  graceful shutdown that follows a `CellExecutionError`. The cell
  that closes the chain didn't run, but `atexit` runs anyway → marker
  written → partial chain admitted. That's exactly the bug we're
  trying to fix.
- Only `TerminateProcess` / SIGKILL skips `atexit`. Most aborts in
  practice are graceful (cell raise, Ctrl+C).

Host-side marker writing is cleaner: the host writes the marker
**only on the success path** of `_execute_notebook_with_path`, and
that path is reached iff every cell executed without raising (or
under `skip_errors=True`, iff nbclient returned cleanly). The
finally block — which already handles partial-staging merge today —
gets a new precondition for folding-in: marker must be present.

### Marker location and format

- Path: `<paths.staging>.completed` (sibling of the staging file,
  same directory).
- Content: small JSON payload with `{"completed_at": "<ISO-8601>",
  "host_pid": <int>, "schema": 1}` for forensic debugging. **The
  content is not load-bearing** — the file's *existence* is the
  signal. A zero-byte file would work equally well.
- Atomic write via the same `_atomic_write_text` helper already in
  `http_replay_cassette.py`. Idempotent: re-writing the marker on a
  retry path is fine.

### Marker also added to ignore lists

`SKIP_OUTPUT_FILE_PATTERNS` and `SKIP_OUTPUT_FILE_GLOBS` in
`src/clm/infrastructure/utils/path_utils.py:83-94` must learn the new
`.completed` suffix so it never travels into worker payloads or
public/speaker output. The marker is a build-internal artifact, same
class as `.staging-*`.

### Concurrency: why "leave alone if no marker" is safe

The current per-worker merge globs **all** `*.staging-*` files in
the canonical's directory and folds+deletes them. Under today's code
this is a latent hazard: if Worker A's post-execution merge runs
while Worker B is still recording, A's merge folds B's partial
recording into canonical and `unlink`s B's staging — silently
corrupting B's run. The chain-poisoning in #115 is the visible
symptom of this hazard meeting the dedup-first-seen-wins rule.

The marker fix structurally eliminates this race:

1. **Marker write must precede merge lock acquisition.** A worker
   that successfully completes execution writes the marker first,
   then takes the canonical lock for the merge. Other workers'
   merges only see markers from workers that have already crossed
   that boundary.
2. **Markerless staging is treated as "not yet completed"** by the
   per-worker merge — could be a still-running concurrent worker, or
   could be a session that aborted. Either way, the right action
   from inside a merge is "leave alone." The other worker (if alive)
   will write its own marker and run its own merge; if it's dead,
   the next build's pre-build sweep will discard the entries.
3. **Pre-build sweep runs single-threaded before any worker starts**
   (this is the invariant from the issue-#86 fix —
   `Course._sweep_orphan_cassette_staging_files` is called from
   `process_all`/`process_file` *before* the worker pool kicks
   off). At that moment, every staging file present is from a
   previous build → no concurrency, so markerless = confirmed
   orphan = safe to discard.

The split between per-worker (conservative: leave alone) and
pre-build (decisive: discard markerless) is the load-bearing design
choice.

### Edge cases (worked through)

| # | Scenario | Behaviour |
|---|---|---|
| 1 | Brand-new deck, first recording session aborts before chain-closer | No marker → next build discards staging entirely → no poisoning. Slightly worse than today's "some entries persist" but today's persistence is exactly the bug. Re-recording is cheap. |
| 2 | New cell added to existing deck; first run aborts mid-new-chain | Same as #1, scoped to the new cell. New cell stays unrecorded; existing cells unaffected. |
| 3 | Cell ran but try/except swallowed the chain-closer LLM call | Marker is written (`_execute_notebook_with_path` returned cleanly). Partial chain still admitted. **Not addressed** by this fix; needs cassette-doctor. |
| 4 | Two workers, German and English, mixed outcomes — A succeeds, B's kernel dies | A writes marker, A's merge picks up its own entries. B's staging is markerless, A's merge leaves it alone. Next build's pre-build sweep discards B's staging. **Correct.** |
| 5 | Marker write fails (disk full, race with antivirus on Windows) | Marker absent → session treated as aborted → recordings discarded next build. Degradation, not corruption. Logged warning. |
| 6 | Worker process killed via `TerminateProcess` by build-level timeout | Worker process dies, no finally runs, no marker. Next build's pre-build sweep discards. **Correct.** |
| 7 | Marker present but staging is missing (file was deleted) | Marker is orphaned. Merge ignores it (no staging to fold). Pre-build sweep deletes the orphan marker. |
| 8 | `skip_errors=True` + cells raised mid-chain | nbclient returns cleanly → marker written → partial chain admitted. The user opted into "errors are OK" so this matches `skip_errors` semantics. **Documented as a known limitation**, same caveat as today. |
| 9 | `--http-replay=refresh` + chain poisoning already in canonical | Refresh records new entries on top, additive merge skips entries already in canonical → poisoning persists. **Pre-existing bug, out of scope.** Workaround: delete canonical, then `new-episodes`. |
| 10 | Two concurrent `clm build` invocations against the same source tree | Build 2's pre-build sweep would see Build 1's markerless staging and discard. **Not supported scenario**, but documented. |

### Alternative considered and rejected: rename staging on success

Instead of a companion marker, the host could rename
`<canonical>.staging-<id>` → `<canonical>.complete-<id>` on
success. The merge function only looks at `*.complete-*` files.

Trade-off:
- **Pro**: single file per session, no companion to keep in sync.
- **Con**: rename of a file the kernel still has an open vcrpy fd on
  is Windows-fragile (the kernel is dead by the time we rename, but
  ZMQ/fs ordering on Windows has burned us before — see the
  `_ReapingKernelManager` workaround).
- **Con**: rename races against a concurrent worker's directory scan
  more subtly than a marker write (atomicity of rename vs atomicity
  of touch).

The companion-marker approach is more explicit about
the "is it completed?" question and matches existing patterns
(`.lock` companions for the merge lock, `.tmp-<uuid>` companions for
`_atomic_write_text`). Going with the marker.

### Alternative considered and rejected: PID-based liveness

Each staging file embeds the worker PID
(`f"{os.getpid()}-{uuid.uuid4().hex}"`). The pre-build sweep could
parse the PID and discard staging from dead PIDs without needing a
marker.

Rejected because:
- PID recycling on Windows is fast — a dead worker's PID may be a
  live unrelated process by the time the sweep runs.
- Docker workers' PIDs are container-internal, not visible to the host.
- The marker subsumes liveness *and* completion-status, and is more
  explicit.

## 3. Phase Breakdown

Four phases, total estimated effort ~1–1.5 days for phases 1–3.
Phase 4 is optional and probably warrants its own issue.

### Phase 1 — Marker plumbing helpers [DONE]

Shipped to the worktree (no PR yet — bundled with later phases).

**Files modified**:

- `src/clm/workers/notebook/http_replay_cassette.py` — added
  `_COMPLETION_MARKER_SUFFIX = ".completed"`,
  `_COMPLETION_MARKER_SCHEMA = 1`, `marker_path(staging) -> Path`,
  `has_completion_marker(staging) -> bool`, and
  `write_completion_marker(paths) -> None`. The writer uses the
  existing `_atomic_write_text`, is idempotent, and downgrades on
  `OSError` (logs warning, does not raise — partial-marker semantics
  fail safely to "session aborted").
- `src/clm/infrastructure/utils/path_utils.py` —
  `SKIP_OUTPUT_FILE_PATTERNS` and `SKIP_OUTPUT_FILE_GLOBS` got an
  explicit `.completed` rule. (The existing `.staging-.*` rule
  already matches by accident; the explicit pattern documents intent
  and survives a future narrowing of the staging regex.)
- `tests/workers/notebook/test_notebook_processor.py` — new
  `class TestCompletionMarker` with 7 tests (split the original
  `creates_file` + `timestamp` test for clearer failure messages):
  - `test_marker_path_sits_beside_staging`
  - `test_has_completion_marker_false_when_absent`
  - `test_has_completion_marker_true_when_present`
  - `test_write_completion_marker_creates_file`
  - `test_write_completion_marker_payload_is_valid_json_with_iso_timestamp`
  - `test_write_completion_marker_is_idempotent`
  - `test_marker_filename_is_ignored_for_output`

**Verification**: 18 cassette-related tests pass (7 new + 11 existing
in `TestCassetteMerge` / `TestBootstrapDurability`); 58 path-utils
tests pass; `ruff check`, `ruff format`, and `mypy` all clean.

**Phase 2 prerequisite discovered**: the worktree venv needs
`uv sync --extra all` to get `vcrpy` and `filelock`. Without that,
`pytest.importorskip("vcr")` at the top of the existing cassette
tests silently skips them — make sure CI and any reviewer's local
env have `[all]` extras installed before claiming "all tests pass."

### Phase 2 — Discriminating merge [DONE]

Shipped to the worktree (no PR yet — bundled with Phase 3).

**Files modified**:

- `src/clm/workers/notebook/http_replay_cassette.py` —
  `merge_staging_into_canonical(paths, *, sweep_orphans=False)` rewrite
  with per-staging-file branching (marker → fold + delete; markerless
  + `sweep_orphans=True` → discard + delete; markerless +
  `sweep_orphans=False` → leave alone with DEBUG log). Marker-file
  paths matching the staging glob are filtered out of the staging set
  before discrimination, so `has_completion_marker` is the only
  signal. Canonical bytes are *not* rewritten when a sweep discards
  only markerless orphans (no atomic-write churn for "nothing to
  fold"). New `_delete_quietly` helper centralises the
  FileNotFoundError-ok / OSError-log delete pattern, used for both
  staging files and their markers. Return value is now the count of
  *markered* folds only — discards are not counted (callers can tell
  from disk state).
- `src/clm/workers/notebook/notebook_processor.py` —
  `_persist_recorded_cassette` gains `execution_succeeded` kw-only
  arg. On the success path it calls `write_completion_marker(paths)`
  before invoking the merge; on the failure path it logs and skips
  the marker write so the partial chain stays markerless and the
  next pre-build sweep discards it. `_create_using_nbconvert`
  tracks `execution_succeeded = False` before the try, flips to
  `True` after `_execute_notebook_with_path` returned cleanly, and
  passes the flag through in the finally block.
- `src/clm/core/course.py` — `_sweep_orphan_cassette_staging_files`
  calls `merge_staging_into_canonical(..., sweep_orphans=True)`.
  Docstring updated to explain the discriminator (single-threaded
  pre-build sweep → no concurrency → markerless = confirmed orphan).
- `docs/claude/design/http-replay.md` — appended "Completion-marker
  semantics (issue #115)" section: contract table, success/failure
  paths, the `skip_errors` / `try-except` / refresh limitations.
- `CHANGELOG.md` — Unreleased / Fixed entry describing the
  partial-chain poisoning fix and the marker mechanism.
- `tests/workers/notebook/test_notebook_processor.py` — new
  `TestDiscriminatingMerge` class with the 7 tests below; existing
  `TestCassetteMerge` tests updated to drop `.completed` markers
  beside the staging files they expect to be folded (via a new
  `_touch_completion_marker(staging)` helper).
- `tests/core/course_test.py` —
  `test_sweep_orphan_staging_files_merges_and_deletes` now writes
  `.completed` markers beside its orphans (with an updated docstring
  clarifying that markerless orphans are *discarded* by the sweep
  per issue #115; the "merges and deletes" branch keeps testing the
  completed-orphan path).

**New tests (all 7 pass)**:

- `test_merge_folds_entries_when_marker_present` — default per-worker
  merge with marker present folds + cleans up both files.
- `test_merge_skips_markerless_in_per_worker_mode` — markerless
  staging is left strictly alone (file still on disk, canonical
  unchanged).
- `test_merge_discards_markerless_in_sweep_mode` — `sweep_orphans=True`
  deletes the staging file and never folds its entries; pre-existing
  canonical entries survive.
- `test_merge_deletes_marker_after_successful_fold` — both the
  staging file and its `.completed` sibling are removed after a
  successful merge.
- `test_persist_recorded_cassette_writes_marker_on_success` — host
  integration: `execution_succeeded=True` produces a merged canonical
  with the recording present (marker was written → fold happened →
  staging + marker cleaned up).
- `test_persist_recorded_cassette_omits_marker_on_failure` —
  `execution_succeeded=False` leaves staging on disk markerless;
  canonical never touched.
- `test_concurrent_workers_dont_consume_each_others_active_staging` —
  Worker A finishes (marker present) and runs its merge; Worker B
  is still recording (markerless). A's merge folds A but leaves B
  intact; B's recordings are not leaked into canonical.

**Verification**: 25 cassette-related tests pass (7 new
`TestDiscriminatingMerge` + 8 existing `TestCassetteMerge` updated to
write markers + 7 `TestCompletionMarker` + 3 `TestBootstrapDurability`);
3 `test_sweep_orphan_staging_files_*` tests pass; full
`test_notebook_processor.py` (117 tests) green; non-docker test suite
green (5587 pass, 1 known pre-existing heartbeat flake unrelated to
cassettes, 11 skipped); `ruff check`, `ruff format --check`, and
`mypy` all clean on every touched file.

---

#### Original Phase 2 plan (historical, for the PR reviewer)

Rewrite `merge_staging_into_canonical` to require a marker for
folding, with a new `sweep_orphans: bool` flag:

```python
def merge_staging_into_canonical(
    paths: CassettePaths,
    *,
    sweep_orphans: bool = False,
) -> int:
    """...

    Args:
        sweep_orphans: When True (pre-build invocation), markerless
            staging files are treated as confirmed orphans from
            aborted previous builds: their entries are discarded and
            the staging files are deleted. When False (default,
            per-worker post-execution invocation), markerless
            staging files are left untouched — they may belong to a
            concurrent worker that hasn't completed yet.
    """
```

Inside the lock-held block:

1. Load canonical (unchanged).
2. For each `staging_path` in the glob:
   - If `has_completion_marker(staging_path)`:
     - Fold entries via existing dedup logic.
     - Delete staging + marker on success.
   - Elif `sweep_orphans`:
     - Discard entries (do not fold).
     - Delete staging (no marker to delete).
     - Log INFO: "discarded orphan staging '<path>' (no completion marker)".
   - Else (markerless, not in sweep mode):
     - Skip. Log DEBUG: "skipped markerless staging '<path>'
       (concurrent worker?)".
3. Atomic write canonical.

Update `_persist_recorded_cassette` in
`src/clm/workers/notebook/notebook_processor.py` to accept and act
on an `execution_succeeded` flag:

```python
def _persist_recorded_cassette(
    self,
    cid: str,
    payload: NotebookPayload,
    paths: "CassettePaths | None",
    *,
    execution_succeeded: bool,
) -> None:
    if paths is None:
        return
    mode = payload.http_replay_mode
    if not mode or mode in ("disabled", "replay"):
        return

    if execution_succeeded:
        write_completion_marker(paths)
    # else: leave staging on disk for the next build's pre-build sweep
    #       to discard, so a partial chain never lands in canonical.

    try:
        merged = merge_staging_into_canonical(paths)  # default sweep_orphans=False
    ...
```

And `_create_using_nbconvert` records success/failure:

```python
execution_succeeded = False
try:
    await self._execute_notebook_with_path(...)
    execution_succeeded = True
except ...:
    raise
finally:
    if replay_injected:
        _strip_injected_cells(processed_nb)
    self._persist_recorded_cassette(
        cid, payload, cassette_paths,
        execution_succeeded=execution_succeeded,
    )
```

Update `Course._sweep_orphan_cassette_staging_files` in
`src/clm/core/course.py:484-487` to pass `sweep_orphans=True`:

```python
merged = merge_staging_into_canonical(
    CassettePaths(canonical=canonical, staging=synthetic),
    sweep_orphans=True,
)
```

Tests:

- `test_merge_folds_entries_when_marker_present`
- `test_merge_skips_markerless_in_per_worker_mode`
- `test_merge_discards_markerless_in_sweep_mode`
- `test_merge_deletes_marker_after_successful_fold`
- `test_persist_recorded_cassette_writes_marker_on_success`
- `test_persist_recorded_cassette_omits_marker_on_failure`
  (call with `execution_succeeded=False` and verify no marker
  is written even though merge is attempted).
- `test_concurrent_workers_dont_consume_each_others_active_staging`
  — Worker A completes (marker written, merge runs); inject a
  fake "Worker B" staging file midway with no marker; assert A's
  merge does not delete or fold B's staging.

### Phase 3 — End-to-end §C regression test [DONE]

Shipped to the worktree (no PR yet — bundled with Phases 1 and 2).

**File modified**:

- `tests/workers/notebook/test_notebook_processor.py` — new
  `TestIssue115PartialChainRegression` class with 2 end-to-end
  scenarios driven through the real
  `merge_staging_into_canonical` (no kernel). Both use shared
  class-level constants for the chain (opener URI shared between
  the aborted and the completed staging, distinct response strings
  so canonical's stored response unambiguously identifies the
  winning session). Reuses the existing `_write_cassette`,
  `_write_two_interactions`, and `_touch_completion_marker`
  helpers from the file rather than adding parallel infrastructure.

**New tests (both pass)**:

- `test_pre_build_sweep_then_completed_session_lands_consistent_chain`
  — chronological walk of the §C scenario across two builds:
  Build 1 leaves Session A's markerless chain-opener on disk
  (kernel died). Build 2's pre-build sweep discards A (asserts
  canonical not even created, since no markered work was folded).
  Build 2's worker then records Session B's full chain
  (chain-opener with same dedup key but different response, plus
  chain-closer whose URI embeds B's opener response) and B's
  per-worker merge folds it. Asserts canonical holds B's response
  (not A's stale one), the chain-closer is present, no debris on
  disk.
- `test_aborted_and_completed_stagings_present_concurrently_keep_completed_response`
  — negative regression: A (markerless) and B (markered) on disk
  at the same instant. `staging-a-aborted` sorts alphabetically
  before `staging-b-completed`, which is the slot the pre-fix
  "first-seen wins" merger would have used to seed canonical with
  A's stale response. The new per-worker merge leaves A strictly
  alone and folds only B; canonical holds B's response. A's
  staging stays on disk for the next pre-build sweep.

**Verification**: all 24 cassette-related tests pass (7
`TestCompletionMarker` + 8 `TestCassetteMerge` + 7
`TestDiscriminatingMerge` + 2 new
`TestIssue115PartialChainRegression`); full
`test_notebook_processor.py` (119 tests) green;
`tests/core/course_test.py` (31 tests) green; combined
150 tests pass in 13.6s under `-n0`. `ruff check`, `ruff format
--check`, and `mypy` clean on the touched file — the 4 pre-existing
`make_notebook_json` arg-type mypy errors in `TestBootstrapDurability`
were verified to predate Phase 3 (same count and root cause before
and after the insertion, line numbers shifted by the new class size).

### Phase 4 — `clm cassette doctor` (OPTIONAL, separate issue) [TODO]

Defer to a follow-up issue. Sketch:

- New `clm cassette doctor [SPEC] [--fix] [--min-text-len N]` command.
- Walks each canonical cassette referenced by the spec.
- For each interaction:
  - Parse the response body; extract chat-completion content
    fields (`choices[].message.content` or `delta.content` in
    streaming responses).
  - Treat each extracted content of length ≥ `--min-text-len`
    (default 50) as a "chain edge candidate".
  - Check whether any other interaction's request body contains
    that text as a substring.
  - If not, flag as orphan.
- Output: per-cassette report of orphan-pointing interactions.
- `--fix` mode: rewrite the cassette without the orphan-pointing
  entries (so the next build re-records that chain). Atomic write.
- Useful for cleaning up cassettes that were poisoned BEFORE the
  marker fix shipped (issue-#115 scenarios already merged into
  canonical) and for the conditional-skip case (edge #3 above)
  which the marker doesn't catch.

Could be wired into CI as a check on every cassette change. Not
required for the primary fix to land.

## 4. Files Affected

Status legend: ✅ = touched in Phase 1, 2, or 3.

| File | Change | Status |
|---|---|---|
| `src/clm/workers/notebook/http_replay_cassette.py` | Phase 1: marker helpers. Phase 2: rewrite `merge_staging_into_canonical` with `sweep_orphans` flag + `_delete_quietly` helper. | ✅ Phase 1 / ✅ Phase 2 |
| `src/clm/infrastructure/utils/path_utils.py` | Add `*.completed` to `SKIP_OUTPUT_FILE_PATTERNS` + `SKIP_OUTPUT_FILE_GLOBS`. | ✅ |
| `tests/workers/notebook/test_notebook_processor.py` | `TestCompletionMarker` class (Phase 1) + `TestDiscriminatingMerge` class (Phase 2) + existing `TestCassetteMerge` updated with `_touch_completion_marker` calls + `TestIssue115PartialChainRegression` (Phase 3 end-to-end + negative regression). | ✅ Phase 1 / ✅ Phase 2 / ✅ Phase 3 |
| `src/clm/workers/notebook/notebook_processor.py` | Thread `execution_succeeded` through `_persist_recorded_cassette`; call marker writer on success path; flip `execution_succeeded` in `_create_using_nbconvert`. | ✅ Phase 2 |
| `src/clm/core/course.py` | Pre-build sweep passes `sweep_orphans=True`; docstring updated. | ✅ Phase 2 |
| `tests/core/course_test.py` | `test_sweep_orphan_staging_files_merges_and_deletes` now writes `.completed` markers on its orphans (with updated docstring). | ✅ Phase 2 |
| `docs/claude/design/http-replay.md` | Appended "Completion-marker semantics" section describing the contract. | ✅ Phase 2 |
| `CHANGELOG.md` | Unreleased / Fixed entry: cassette merge discards partial chains from aborted sessions (#115). | ✅ Phase 2 |

No info-topic update required (this is internal mechanics; no
user-visible CLI surface change for the primary fix). If Phase 4 is
implemented, `src/clm/cli/info_topics/commands.md` needs the new
`clm cassette doctor` entry.

## 5. Open Questions

1. **Should the per-worker merge ever discard markerless staging?**
   Current design: never. The pre-build sweep is the only place
   that discards. This is the safest concurrency story but means a
   long-running build with many crashed workers accumulates
   markerless staging until the *next* build runs. Probably fine —
   the staging files are small and inside `_cassettes/`. If
   accumulation turns out to be a problem, add a "discard staging
   older than 24h" rule to the per-worker merge later.

2. **Should marker payload include the cassette name or build ID?**
   Currently just `completed_at` + `host_pid` + `schema`. Tying the
   marker to a specific build run would let the cassette-doctor
   detect "marker present but from a stale build" — but the
   completion semantics don't actually need that. Punting.

3. **Should `skip_errors=True` skip the marker?** (Edge case #8.)
   Tempting — "errors happened, so don't mark complete" — but it
   requires the host to know whether *any* cell raised, which
   nbclient doesn't surface cleanly when `allow_errors=True`.
   `_clear_error_outputs` walks the notebook post-hoc looking for
   error outputs, so we *could* check there. Probably not worth
   the complexity in v1; `skip_errors` users have already accepted
   degraded determinism. Punt.

4. **Should the marker fix ship with an opt-out env var?** E.g.,
   `CLM_CASSETTE_PARTIAL_CHAIN_FILTER=0` to revert to today's
   behaviour. Generally CLM removes flags after rollouts (the
   git-friendly-output-writes PR series removed all rollout env
   vars on the second milestone). Probably no: ship without a
   flag, document the change in CHANGELOG, deal with regressions
   via revert if needed.

## 6. Definition of Done

- [ ] Phases 1, 2, 3 merged to master via a single PR (the marker
  + merge + sweep changes are tightly coupled; splitting them buys
  no review value).
- [x] All existing http-replay tests still pass — particularly
  `test_two_concurrent_workers_full_seed_record_merge_cycle`
  (issue-#86 regression guard) and
  `test_concurrent_merges_do_not_lose_interactions`. Verified in
  the Phase 3 verification run (`tests/workers/notebook/test_notebook_processor.py`,
  119 tests green under `-n0`).
- [x] New concurrency test (Phase 2) demonstrates that a marker
  fix does not regress issue #86
  (`test_concurrent_workers_dont_consume_each_others_active_staging`).
- [x] §C end-to-end test (Phase 3) passes — partial-chain merge is
  discarded
  (`TestIssue115PartialChainRegression::test_pre_build_sweep_then_completed_session_lands_consistent_chain`
  plus the negative-regression sibling).
- [ ] PythonCourses team runs an `--http-replay=replay` build of
  `slides_010_prompt_templates.py` with a freshly bootstrapped
  cassette and confirms no `CannotOverwriteExistingCassetteException`
  on Q3. Signal-back: comment on issue #115 with the build log.
- [x] CHANGELOG entry added (Phase 2; under `[Unreleased] / Fixed`,
  see CHANGELOG.md lines 134–151).
- [ ] Phase 4 (`cassette doctor`) filed as a separate follow-up
  issue if not implemented in the same PR.

## 7. References

| Topic | Path / link |
|---|---|
| Issue text | [hoelzl/clm#115](https://github.com/hoelzl/clm/issues/115) |
| Predecessor (closed) | [hoelzl/clm#95](https://github.com/hoelzl/clm/issues/95) |
| Related closed (concurrency race) | [hoelzl/clm#86](https://github.com/hoelzl/clm/issues/86), archive `docs/claude/http-replay-race-fix-handover-archive.md` |
| Merge code | `src/clm/workers/notebook/http_replay_cassette.py:93-187` (merge), `:190-211` (`_dedup_key`) |
| Bootstrap (eager-save + atexit) | `src/clm/workers/notebook/notebook_processor.py:114-211` |
| Host orchestration | `src/clm/workers/notebook/notebook_processor.py:1375-1418` (`_persist_recorded_cassette`), `:1445-1541` (`_create_using_nbconvert` finally block) |
| Pre-build sweep | `src/clm/core/course.py:413-498` |
| Ignore patterns | `src/clm/infrastructure/utils/path_utils.py:83-94` |
| Existing merge tests | `tests/workers/notebook/test_notebook_processor.py:2340-2654` |
| Design doc to update | `docs/claude/design/http-replay.md` |
| PythonCourses §C source | PythonCourses repo: `docs/handover-slide-format-redesign-course.md` §C (read-only from CLM side) |

---

**Last updated**: 2026-05-21 — Phases 1, 2, and 3 shipped to worktree.
Ready for a bundled PR covering all three phases.
