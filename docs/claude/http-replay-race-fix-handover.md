# Handover: Fix HTTP-Replay Race in `seed_staging_from_canonical` (Issue #86)

## 1. Feature Overview

Fix a concurrent-worker race condition introduced by PR #83 that makes
`clm build --http-replay=replay` unusable for any course with `http-replay="yes"`
topics when `--notebook-workers > 1`. The race causes
`CannotOverwriteExistingCassetteException` errors for cells whose recorded
interactions are actually present in the canonical cassette.

**Issue**: https://github.com/hoelzl/clm/issues/86
**Branch**: master (worktree: `lively-riding-owl`)
**Blocks**: PythonCourses slide-format redesign Phase A.1 (snapshot/verify baseline)
**Investigation commit**: not yet committed — investigation lives in this handover

### Symptoms reported

1. **Symptom #1 (real)**: strict `--http-replay=replay` mode raises
   `CannotOverwriteExistingCassetteException` on 20+ cells across the AZAV ML
   course; reporter hypothesized a vcrpy query-param-order mismatch.
2. **Symptom #2 (likely misdiagnosis)**: `--http-replay=refresh` produces
   "incomplete" cassettes (e.g., `slides_010c_requests.http-cassette.yaml`
   with 1 interaction). The deck only makes 1 HTTP call, so 1 interaction
   is correct.
3. **Sub-bug (separate)**: `clm build` exits 0 even when cells failed.

### Root cause (confirmed)

`seed_staging_from_canonical()` in `src/clm/workers/notebook/http_replay_cassette.py:68-96`
calls `merge_staging_into_canonical(paths)` at line 89. That helper globs
**every** `*.http-cassette.yaml.staging-*` file in the canonical's parent
directory, merges them into canonical, and **`unlink`s** them — including
**active staging files belonging to concurrent workers that haven't started
their kernel yet**.

Reproduced deterministically with `scratch_race_repro.py` (now deleted) using
the actual CLM module:

```
>>> Worker A: seed_staging_from_canonical()   # creates staging-A
  staging_A exists: True
>>> Worker B: seed_staging_from_canonical()   # sweeps & deletes staging-A
  *** staging_A exists: False ***
```

When Worker A's kernel finally boots and `vcr.use_cassette(staging-A)` runs,
`FilesystemPersister.load_cassette` raises `CassetteNotFoundError`, vcrpy
silently treats the cassette as empty (`data = []`), and the first HTTP call
in replay mode (`record_mode="none"`) crashes with
`CannotOverwriteExistingCassetteException`.

### Why the symptom looked like a query-order bug

The error message includes the live request URL. The reporter compared it to
the URL in the **canonical** file (which still exists on disk) and noticed
the query params were in different insertion order — concluding vcrpy's
`query` matcher was broken. Verified in isolation that this is NOT the case:
vcrpy 8.1.1's `query` matcher uses `sorted(parse_qsl(...))` and works
correctly with the exact CLM `match_on=("method","scheme","host","port","path","query","body")`
tuple. A record-then-replay roundtrip with reordered params succeeds. The
staging file the kernel actually points at was simply deleted.

## 2. Design Decisions

### Recommended approach: **remove the seed-time orphan sweep**

The cleanest fix is to delete the `merge_staging_into_canonical` call from
inside `seed_staging_from_canonical` and rely on the **already-existing**
pre-build sweep in `Course.process_all` (`src/clm/core/course.py:380-465`,
`_sweep_orphan_cassette_staging_files`) plus the post-execution merge in
`_persist_recorded_cassette`.

**Why this works**:

- PR #83's pre-build sweep at `Course.process_all`/`Course.process_file`
  runs **once**, **before any worker starts**, and is therefore safe to
  delete-as-it-goes. It already handles the "killed previous build left
  orphans" case PR #83 was trying to close.
- The per-worker `_persist_recorded_cassette` merge (post-execution) is
  also safe because by then all workers have committed their final state
  to disk via vcrpy's `__exit__` / `atexit`.
- The seed-time sweep adds **no additional safety** in the normal case
  and creates the race in the concurrent case.

### Alternatives considered

| Approach | Rejected because |
|---|---|
| **Filter sweep to "stale" files only** (e.g., check PID liveness from filename `staging-{pid}-{uuid}`) | PID liveness is racy on Windows; PIDs get recycled; doesn't help when workers are in the same process tree. Too fragile. |
| **Lock-then-claim pattern** (touch a `.claim` file next to staging that workers remove in `_persist`) | Adds another moving part; still leaves orphan claims after kill. Significant refactor for marginal benefit. |
| **Skip the sweep only for this worker's own staging name** | Worker B's staging doesn't exist yet at sweep time, so trivially filtering its own name doesn't help. Filtering "all currently-running workers' stagings" requires cross-process coordination — bigger refactor than worth. |
| **Mark stagings with mtime, skip recent ones** | Heuristic. Defines "recent" arbitrarily. False negatives during slow builds, false positives during fast kernel boots. |
| **Keep the sweep but don't delete files** | Then stagings accumulate forever; defeats the purpose. |

### Constraints

- **Must not regress PR #83's stated goal**: "orphan staging files crash
  the next build" must remain fixed. The pre-build sweep in `process_all`
  already covers this.
- **Must not regress PR #82's stated goal**: build-scoped cassette
  snapshots for stable `execution_cache_hash` between Stage 3 and Stage 4.
  Not affected — the snapshot reads canonical post-sweep.
- **Must not regress PR #81's stated goal**: `body` in `match_on` so
  divergent recordings fail loudly instead of serving wrong responses.
  Not affected — `match_on` is unchanged.
- **Pre-commit hook runs ruff + mypy + fast test suite**. Changes must
  pass `uv run ruff check src/ tests/` and `uv run pytest`.

## 3. Phase Breakdown

### Phase 1: Add a regression test for the concurrent-worker race [DONE]

**Goal**: Failing test that captures the exact race (Worker A's staging gets
deleted by Worker B's seed sweep). Must fail on current `master` and pass
after Phase 2.

**File**: `tests/workers/notebook/test_notebook_processor.py` (existing
`TestHttpReplayCassettePaths` class around line 2160; the existing tests at
`test_merge_sweeps_orphan_staging_files` at line 2357 are the pattern to
follow).

**Test sketch**:

```python
def test_seed_does_not_delete_concurrent_workers_staging(self, tmp_path):
    """Worker B's seed must not delete Worker A's still-active staging file.

    Regression for issue #86: PR #83 added an orphan sweep inside
    seed_staging_from_canonical that can't distinguish dead orphans from
    active staging files of currently-running concurrent workers.
    """
    from clm.workers.notebook.http_replay_cassette import (
        resolve_paths,
        seed_staging_from_canonical,
    )

    topic = tmp_path / "topic"
    topic.mkdir()
    cassette_name = "slides.http-cassette.yaml"
    canonical = topic / cassette_name
    canonical.write_text(_MINIMAL_CASSETTE_YAML, encoding="utf-8")

    paths_A = resolve_paths(topic, cassette_name)
    paths_B = resolve_paths(topic, cassette_name)

    # Worker A seeds first. Its staging file is now on disk.
    seed_staging_from_canonical(paths_A)
    assert paths_A.staging.exists()

    # Worker B starts. Its seed must NOT delete A's active staging.
    seed_staging_from_canonical(paths_B)
    assert paths_A.staging.exists(), (
        "Worker B's seed deleted Worker A's active staging file — "
        "regression for issue #86 race condition."
    )
    assert paths_B.staging.exists()
```

`_MINIMAL_CASSETTE_YAML` can be any valid 1-interaction vcrpy cassette
(see line 2318 `test_merge_creates_canonical_when_only_staging_exists` for
an existing example of the format).

**Acceptance**: test fails on current master, passes after Phase 2.

### Phase 2: Remove the seed-time orphan sweep [DONE]

**File**: `src/clm/workers/notebook/http_replay_cassette.py`

**Change**: in `seed_staging_from_canonical` (lines 68-96), delete the
`merge_staging_into_canonical(paths)` call and the surrounding try/except
that wraps it. Update the docstring to reflect that orphan sweeping now
happens once per build in `Course.process_all` / `Course.process_file`.

**Result after change** (lines 68 onward):

```python
def seed_staging_from_canonical(paths: CassettePaths) -> None:
    """Seed this worker's staging file from the canonical cassette.

    Orphan staging files from previously-killed builds are swept once
    per build in :meth:`Course.process_all` / :meth:`Course.process_file`
    via :meth:`Course._sweep_orphan_cassette_staging_files`, *before* any
    worker starts — so by the time this function runs, the canonical
    cassette already includes any recoverable orphan interactions. We
    just need to give vcrpy a starting point: copy canonical (if it
    exists) into this worker's per-invocation staging file so the
    kernel can replay recorded interactions offline.
    """
    paths.staging.parent.mkdir(parents=True, exist_ok=True)
    if paths.canonical.exists():
        shutil.copy2(paths.canonical, paths.staging)
```

**Acceptance**:
- Phase 1 test passes.
- All existing tests still pass (especially `test_merge_sweeps_orphan_staging_files`,
  `test_sweep_orphan_staging_files_merges_and_deletes` —
  these exercise the pre-build sweep, not the seed-time sweep).
- `pytest -m "not docker"` is green.

### Phase 3: Add a regression test for end-to-end concurrent build [DONE]

**Goal**: End-to-end test that two concurrent workers building the same
notebook with a non-trivial replay cassette do not corrupt each other's
staging.

**File**: `tests/workers/notebook/test_notebook_processor.py` or
`tests/core/course_files/notebook_file_test.py` (the latter has
`TestProcessNotebookOperationHttpReplay` which is the closer pattern).

**Approach**: simulate two `_resolve_cassette_paths` + `seed_staging_from_canonical`
calls in parallel via threads or simply sequenced as in Phase 1, then
manually invoke `merge_staging_into_canonical` for each and assert that
both workers' synthetic recordings end up in canonical after dedup.

This test should NOT require spinning up real kernels — operate at the
http_replay_cassette module level. The existing tests at lines 2318-2429
show the right level of integration.

**Acceptance**:
- Both workers' staging files survive their full mock-execution.
- Final canonical contains all interactions from both workers
  (deduplicated where appropriate).

### Phase 4 (optional, separate sub-bug): Fail the build on cell errors [TODO]

**File**: `src/clm/cli/commands/build.py` (around `main_build` at line 1018
and the surrounding `asyncio.run(main_build(...))` at line 1584).

**Problem**: cell failures during the build don't propagate to a non-zero
exit code. Only SIGTERM (line 1555) and `--verify-against` divergence
(line 1652) cause `sys.exit(1)`. The build summary records errors but the
process exits 0. Particularly painful when using `--http-replay=replay` in
CI (any cell failure should be a hard build failure).

**Suggested change**: thread an error count back from `main_build`, and
`sys.exit(1)` after the build call if it is non-zero. May need a CLI
flag like `--fail-on-cell-error` (default-on for `--http-replay=replay`,
opt-in elsewhere) to preserve backward compatibility for users who
currently rely on exit-0-with-errors.

**Acceptance**: a build with N cell failures returns exit code 1 under
`--http-replay=replay`. **This is a separate issue and should probably
be filed independently from #86**; mentioning here so it isn't forgotten.

## 4. Current Status

- **Phase 1**: DONE (2026-05-19). Unit regression test
  `TestCassetteMerge::test_seed_does_not_delete_concurrent_workers_staging`
  added in `tests/workers/notebook/test_notebook_processor.py`. Confirmed
  to fail on the buggy code and pass after Phase 2.
- **Phase 2**: DONE (2026-05-19). Stripped the
  `merge_staging_into_canonical(paths)` call out of
  `seed_staging_from_canonical` in
  `src/clm/workers/notebook/http_replay_cassette.py`. Function body is
  now just `mkdir` + conditional `shutil.copy2`. Pre-build sweep in
  `Course._sweep_orphan_cassette_staging_files` handles orphan recovery.
- **Phase 3**: DONE (2026-05-19). End-to-end test
  `TestCassetteMerge::test_two_concurrent_workers_full_seed_record_merge_cycle`
  walks both workers through seed → kernel-load → record → concurrent
  merge and asserts canonical converges to the deduplicated union.
  Verified to fail under the reverted-buggy code; passes under the fix.
  Added module-level helper `_write_two_interactions` next to
  `_write_cassette`.
- **Phase 4**: TODO. Separate sub-bug (exit-code-0 on cell failures);
  track independently if pursued — not bundled with this fix.

### Investigation artifacts

The investigation that produced this handover ran four scratch repro
scripts (`scratch_vcr_repro.py`, `scratch_vcr_repro2.py`, `scratch_vcr_repro3.py`,
`scratch_race_repro.py`) — all confirmed:

1. vcrpy 8.1.1 `query` matcher correctly handles different param insertion
   orders with the exact CLM `match_on` tuple → Symptom #1's stated cause
   is incorrect.
2. The concurrent-worker race in `seed_staging_from_canonical` is
   deterministically reproducible.

Scratch files were deleted after the investigation. Don't recreate them
unless needed for debugging; the test in Phase 1 covers the same ground.

### Blockers / open questions

- **None for Phases 1-3.** The fix is straightforward and low-risk.
- **For Phase 4** (separate sub-bug): need to decide whether cell failures
  should fail the build unconditionally or only under `--http-replay=replay`.
  Recommendation: file a separate issue, do not bundle with #86.

### Tests state

- Existing tests at `tests/workers/notebook/test_notebook_processor.py`
  lines 2160-2440 and `tests/core/course_test.py::test_sweep_orphan_*`
  are passing on `master` (verified by reading; not re-run as part of
  this investigation).
- No regression test exists for the concurrent-worker race scenario.
  PR #83's tests only exercise pre-existing orphan files (from a prior
  killed build), not concurrent active workers.

## 5. Next Steps

**Phases 1-3 are complete.** The #86 fix is functionally landed on this
worktree. Remaining work to finalize the issue:

1. CHANGELOG entry under `[Unreleased]` → **done** (see "Fixed" section,
   "HTTP-replay race between concurrent worker seeds (issue #86)").
2. Commit the three logical changes (test, fix, e2e test) and push.
3. Open the PR referencing #86; close #86 on merge.
4. **Phase 4 (separate sub-bug):** file as its own GitHub issue if
   pursued — exit-code-0-on-cell-error is unrelated to #86's race.

### Historical Setup notes (kept for context)

### Setup

```bash
# Activate the venv and run the existing http-replay tests once to
# establish a green baseline:
uv run pytest tests/workers/notebook/test_notebook_processor.py::TestHttpReplayCassettePaths -v
uv run pytest tests/core/course_test.py -k "sweep_orphan" -v
```

### Implement Phase 1

1. Open `tests/workers/notebook/test_notebook_processor.py`.
2. Find the `TestHttpReplayCassettePaths` class (around line 2160).
3. Locate `test_merge_sweeps_orphan_staging_files` at line 2357 — it
   has the helper imports and YAML fixture pattern you'll need.
4. Add the new test from Phase 2's sketch above. The minimal cassette YAML
   helper is already used in nearby tests; reuse it.
5. Run the new test in isolation and confirm it **fails** on master:
   ```bash
   uv run pytest tests/workers/notebook/test_notebook_processor.py::TestHttpReplayCassettePaths::test_seed_does_not_delete_concurrent_workers_staging -v
   ```
6. Commit the failing test (commit message: `test(http-replay): add regression for issue #86 concurrent-worker seed race`).

### Implement Phase 2

1. Open `src/clm/workers/notebook/http_replay_cassette.py`.
2. Replace `seed_staging_from_canonical` (lines 68-96) with the version
   shown in the Phase 2 section above.
3. Run the regression test from Phase 1; it should now **pass**.
4. Run the full http-replay test suite:
   ```bash
   uv run pytest tests/workers/notebook/test_notebook_processor.py::TestHttpReplayCassettePaths tests/core/course_test.py tests/core/course_files/notebook_file_test.py -v
   ```
5. Run the fast suite as a final guard:
   ```bash
   uv run pytest
   ```
6. Commit the fix (commit message: `fix(http-replay): stop seed-time orphan sweep from deleting concurrent workers' active stagings (#86)`).

### Implement Phase 3

1. Add the end-to-end concurrent test to
   `tests/workers/notebook/test_notebook_processor.py` (or
   `tests/core/course_files/notebook_file_test.py` if preferred — pick the
   one that lines up with existing tests around concurrent merge behavior;
   see `test_concurrent_merges_do_not_lose_interactions` at line 2429 of
   `test_notebook_processor.py` for prior art).
2. Verify it passes and that removing the Phase 2 fix breaks it.
3. Commit (`test(http-replay): add end-to-end concurrent-worker test (#86)`).

### Gotchas

1. **Don't try to keep the seed-time sweep "smarter"**. PID-based liveness
   checks are racy on Windows and don't help with intra-process concurrency.
   The pre-build sweep already covers the scenario PR #83 was added for.

2. **The reporter's hypothesis (query-order mismatch) is wrong.** Don't
   waste time on vcrpy `match_on` tuning. The matcher works correctly.
   The staging file the kernel was looking at had simply been deleted.

3. **Symptom #2 is likely a misdiagnosis** based on misreading the deck.
   `slides_010c_requests.py` (the deck cited) only makes 1 HTTP call. The
   1-interaction cassette is correct. After the fix, refresh-mode builds
   should produce stable cassettes; if the user reports otherwise after
   Phase 2, investigate as a separate bug rather than expanding scope here.

4. **Don't commit `scratch_*.py` files**. They were deleted after the
   investigation. The Phase 1 test replaces them.

5. **Pre-commit hook runs the fast suite**. If a commit fails the hook,
   the commit did NOT happen. Fix the issue, re-stage, create a new
   commit. Never `--amend`.

6. **Don't bundle Phase 4 (exit-code-0 sub-bug)** with this fix. File it
   separately. The race fix should land as a focused, minimal change.

## 6. Key Files & Architecture

### Files implicated

| File | Role |
|---|---|
| `src/clm/workers/notebook/http_replay_cassette.py` | **THE FIX GOES HERE.** `seed_staging_from_canonical` at lines 68-96. |
| `src/clm/workers/notebook/notebook_processor.py:1423-1431` | Per-worker call site for seed; no changes needed. |
| `src/clm/core/course.py:380-465` | `_sweep_orphan_cassette_staging_files` — the pre-build sweep that already handles truly-orphan files. Untouched by this fix; understanding it is important so the fix doesn't reintroduce the gap PR #83 closed. |
| `src/clm/core/course.py:296-358` | `_snapshot_cassettes_for_build` and `process_all` — show the sweep+snapshot ordering at the start of every build. Unchanged. |
| `tests/workers/notebook/test_notebook_processor.py` | **Phase 1 + Phase 3 tests go here** (around the `TestHttpReplayCassettePaths` class at line 2160). |

### Entry points / how it fits

```
clm build --http-replay=replay
  → main_build (cli/commands/build.py:1018)
    → Course.process_all (core/course.py:334)
      → _sweep_orphan_cassette_staging_files (core/course.py:380)   ← pre-build sweep (untouched)
      → _snapshot_cassettes_for_build (core/course.py:296)          ← snapshot (untouched)
      → process_stage_for_target ... → backend dispatches to workers
        → (worker process) NotebookProcessor._create_using_nbconvert
          → _resolve_cassette_paths
          → seed_staging_from_canonical                              ← **FIX HERE: remove inner merge call**
          → _maybe_inject_http_replay
          → ExecutePreprocessor.preprocess (kernel runs notebook)
          → _persist_recorded_cassette
            → merge_staging_into_canonical                           ← post-execution merge (untouched)
```

### Conventions to maintain

- **No print/log spam in library code**: `logger.getLogger(__name__)` only.
- **Defensive `BLE001` exception catches**: existing code marks them with
  `# noqa: BLE001 — defensive`. Keep this style for any new defensive code.
- **Atomic writes for canonical**: `_atomic_write_text` in
  `http_replay_cassette.py:220` — use this if any new canonical writes
  are added (none expected for this fix).

## 7. Testing Approach

### Strategy

- **Unit tests at the `http_replay_cassette` module level** (Phase 1 + 3) —
  fastest, most focused. The existing test class `TestHttpReplayCassettePaths`
  is the right home.
- **No new integration tests required** for this fix; the bug is in a
  helper that's already covered by unit tests for its public API. The
  end-to-end concurrent test in Phase 3 is the integration-ish ceiling.
- **No Docker tests needed** — this is direct-mode logic that's
  identical in Docker mode (Docker workers also call
  `seed_staging_from_canonical` via the same code path).

### What is tested by existing suite

- Orphan files from prior builds are swept into canonical
  (`test_merge_sweeps_orphan_staging_files`,
  `test_sweep_orphan_staging_files_merges_and_deletes`).
- Concurrent merges don't lose data
  (`test_concurrent_merges_do_not_lose_interactions` at line 2429 of
  `test_notebook_processor.py`).
- Distinct workers get distinct staging paths
  (`test_resolve_paths_two_workers_get_distinct_staging_paths`).

### What is missing

- **Concurrent workers in the seed phase** — the exact race that
  causes #86. Phase 1 covers the regression; Phase 3 covers the
  end-to-end.

### Commands

```bash
# Targeted, fastest:
uv run pytest tests/workers/notebook/test_notebook_processor.py::TestHttpReplayCassettePaths -v

# Plus the course-level orphan sweep tests:
uv run pytest tests/core/course_test.py -k "sweep_orphan" \
              tests/core/course_files/notebook_file_test.py::TestProcessNotebookOperationHttpReplay \
              -v

# Fast suite (also what pre-commit runs):
uv run pytest

# Pre-release / pre-merge gate (Docker tests run in CI only):
uv run pytest -m "not docker"
```

## 8. Session Notes

### Why the reporter's diagnosis was wrong but reasonable

The error message vcrpy raises includes the live request URL but **not**
the staging cassette's path or its current contents. The reporter inspected
the canonical file (which still had the interaction) and noticed param
ordering differed. From that vantage, the query-order hypothesis was the
most natural explanation. Without the realization that the staging file
had been deleted between seed and kernel boot, that path leads nowhere.

If you find yourself debugging a similar future report, **always check
whether the staging file referenced in the error actually existed when
the kernel tried to load it**. The error message says "current record
mode ('none')" — replay mode with an empty cassette looks the same as
"cassette doesn't contain this request" because both produce the same
exception.

### Why PR #83's seed-time sweep was added in the first place

Per PR #83's commit message: "a notebook killed mid-build leaves a
`slides_x.http-cassette.yaml.staging-<pid>-<uuid>` file behind. If the
next build's `compute_other_files` reaches it before
`merge_staging_into_canonical` does, payload b64 encoding crashes."

The defense-in-depth was: (a) pre-build sweep in `Course.process_all`,
(b) per-worker sweep in `seed_staging_from_canonical`, (c) filter in
`compute_other_files`. Layers (a) and (c) are sufficient. Layer (b) is
what creates this bug. Removing (b) does not regress (a) or (c).

### CHANGELOG entry

When the fix lands, add an entry to `CHANGELOG.md` under `[Unreleased]`
referencing #86. Style follows existing entries like the cassette/monitor
PRs (`f208612`).

### Don't reach for vcrpy version pinning

vcrpy 8.1.1 is behaving correctly. There is no upstream bug here.

---

**Last Updated**: 2026-05-19 (Phases 1-3 implemented; CHANGELOG entry added)
**Author of investigation**: Claude Opus 4.7 (1M context) under user `tc`
