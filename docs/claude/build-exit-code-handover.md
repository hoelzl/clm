# Handover: Fail `clm build` exit code on notebook cell errors (Issue #90)

## 1. Feature Overview

`clm build` currently exits with status **0** even when one or more notebook
cells crash during execution. Only two paths produce a non-zero exit:

- `SIGTERM` after a second shutdown request (`src/clm/cli/commands/build.py:1555`).
- `--verify-against` divergence (`src/clm/cli/commands/build.py:1652`).

The build summary records errors via `BuildReporter`, but the process exits
cleanly, so CI, scripts, and pre-commit hooks cannot detect cell failures
programmatically. This was surfaced during the issue #86 investigation: 20+
cells crashed on `CannotOverwriteExistingCassetteException` and `clm build`
still returned 0, which masked the underlying race.

**Issue**: https://github.com/hoelzl/clm/issues/90
**Branch**: master (worktree as needed — pick a fresh `clm/issue-90-...` worktree)
**Related (closed)**: #86 (race fix that surfaced this bug)
**Predecessor handover** (fully retired): `docs/claude/http-replay-race-fix-handover-archive.md`

### Symptoms

1. Run any course where a code cell raises (e.g., `raise RuntimeError("nope")`).
2. `clm build <spec>` — the build summary lists the cell error.
3. `echo $LASTEXITCODE` (or `$?`) — **0**.

Expected: non-zero exit code when one or more cell errors are present,
at minimum under `--http-replay=replay` (CI's strictest mode).

### Why this matters

- CI can't gate on cell failures with `--http-replay=replay` — the strictest
  replay mode silently succeeds.
- The same is true for scripted pre-publish checks and Git hooks.
- Without this fix, the issue #86 class of regressions (concurrent cassette
  races, future replay strictness changes) will keep being invisible until
  a human reads the summary.

## 2. Design Decisions

### Recommended approach: thread error count back, exit non-zero in entry point

`main_build` already builds an in-memory `BuildReporter` whose `.errors` list
is deduplicated and serialized into a `BuildSummary` by
`build_reporter.finish_build()` (`src/clm/cli/build_reporter.py:294-348`).
The call site at `src/clm/cli/commands/build.py:805-854` (inside
`process_course_with_backend._run_stages`) is the **single** place a build's
final error count materializes.

Change shape:

1. `process_course_with_backend` captures the `BuildSummary` returned by
   `build_reporter.finish_build()` and returns it.
2. `main_build` awaits `process_course_with_backend` and returns the summary
   (or an `int` count, or a small dataclass — see "open design choices").
3. The Click entry point at `src/clm/cli/commands/build.py:1584-1622` captures
   the return value of `asyncio.run(main_build(...))` and `sys.exit(1)` if
   the error count is non-zero **and** the new flag/policy says we should fail.

This mirrors the `--verify-against` post-build pattern already in place at
`src/clm/cli/commands/build.py:1639-1653` (compute report, decide, exit).

### Open design choices

These need user/reviewer alignment before Phase 2:

1. **Default policy under `--http-replay=replay`**: issue #90 suggests
   default-on. **Recommendation: yes** — strict replay mode is the CI
   contract and any cell failure is already-recorded-and-now-broken
   behavior worth failing on.

2. **Default policy under other replay modes** (`once`, `new-episodes`,
   `refresh`, `disabled`): issue #90 suggests opt-in. **Recommendation:
   keep current behavior (exit 0)** for local iterative work where users
   tolerate transient errors. Bumping this default would be a noisy break
   for everyone running `--http-replay=new-episodes` locally.

3. **CLI flag name**: issue #90 proposes `--fail-on-cell-error`. Two
   alternatives worth considering:
   - `--fail-on-error` (broader; also covers `notebook_compilation`,
     `worker_timeout`, `missing_module`, etc. that today don't trip the
     exit code either).
   - `--strict` (terse; ambiguous — already used informally for
     `--strict-verify`).
   - **Recommendation**: `--fail-on-error` plus a one-line note in
     `commands.md` that lists which categories count. The
     `error_categorizer` already produces a stable `category` field for
     every `BuildError`, so we can scope it later if needed without
     renaming the flag.

4. **Negative form**: `--no-fail-on-error` is the natural opt-out under
   `--http-replay=replay`. Click handles this automatically with
   `--fail-on-error/--no-fail-on-error` (two-option pattern).

5. **Env var**: probably yes — `CLM_FAIL_ON_ERROR={1,true,yes}` so CI can
   force-enable without touching every command line. Mirror the
   `_resolve_http_replay_mode` precedence: CLI > env var > policy default.

6. **What counts as "error"**: the `BuildReporter.errors` list after
   dedup. Do not introduce a separate "cell-only" filter unless reviewers
   want it — if `--fail-on-error` is the flag name, all
   `severity="error"` categories should count. Today the categories that
   surface cell-execution failures are `cell_execution`,
   `notebook_compilation`, `notebook_processing` (see
   `src/clm/cli/error_categorizer.py:181-219`).

### Alternatives considered

| Approach | Rejected because |
|---|---|
| **Always fail on any error**, no flag | Backwards-incompatible break for anyone scripting around the current exit-0 behavior. Even if "correct," needs an opt-in transition window. |
| **Raise `SystemExit(1)` from `BuildReporter.finish_build`** | Couples a reporter to process exit; surprising side-effect. Also breaks programmatic users of `main_build` (e.g., the watch mode, tests). |
| **Inspect the SQLite jobs DB after the run** for failed-job rows | Reinvents what `BuildReporter` already tracks. Two sources of truth for "did the build succeed." |
| **Exit non-zero only if any error is `severity="error"`** | This is effectively what we're doing — every entry in `.errors` is already severity="error" today. Worth confirming during implementation rather than designing for it. |

### Constraints

- **Must not regress watch mode** (`build.py:990-1015`). Watch mode runs
  builds in a loop and currently never exits except via SIGTERM. The new
  exit logic must apply only to one-shot builds, not the watch loop —
  the simplest way is to keep the exit decision at the Click entry point
  (outside the `while not shut_down` loop), so watch mode continues
  whether or not a given iteration produced errors.
- **Must not regress `--verify-against`** (`build.py:1639-1653`). That
  post-build phase already exits 1 on diffs; the new exit must compose
  cleanly — if both an error count and a verify diff happen, exit 1 is
  correct regardless of which check trips first. Prefer running the
  error-count check **before** verify so the cause is clear in CI logs.
- **Pre-commit runs `ruff check`, `mypy`, and the fast test suite.**
  Changes must pass `uv run pytest` locally and `uv run ruff check src/ tests/`.
- **Info topics rule** (CLAUDE.md §Info Topics Maintenance Rule): a new
  CLI flag means `src/clm/cli/info_topics/commands.md` MUST be updated in
  the same change. The current `commands.md` has no `--http-replay` row
  either, so consider adding both at once (out of scope to land #90, but
  worth noting).

## 3. Phase Breakdown

### Phase 1: Add a failing integration test [DONE]

**Goal**: A test that runs `clm build` against a course where one cell
raises and asserts the exit code is non-zero under `--http-replay=replay`
(or under `--fail-on-error`, depending on naming locked in §2).

**File**: `tests/cli/test_build_command.py` — pick a class near the
`test_build_runs_main_build_with_mocked_pipeline` pattern at line 897.
That test shows how to stub out the heavy infrastructure (course, backend,
workers) while exercising the real `main_build` → `asyncio.run` →
`sys.exit` path.

**Test sketches** (two complementary tests; both should fail on master,
both pass after Phase 3):

```python
def test_build_exits_nonzero_when_cell_errors_under_replay_mode(
    self, tmp_path, monkeypatch
):
    """Issue #90: --http-replay=replay must fail when a cell errors."""
    # Use the mocked-pipeline scaffolding from
    # test_build_runs_main_build_with_mocked_pipeline (line 897+).
    # Patch BuildReporter.finish_build to return a BuildSummary with
    # one synthetic BuildError(category="cell_execution"), then invoke
    # `clm build --http-replay=replay <spec>` via CliRunner and assert
    # result.exit_code != 0.

def test_build_exits_zero_when_no_cell_errors_under_replay_mode(
    self, tmp_path, monkeypatch
):
    """Sanity: clean builds still exit 0 under --http-replay=replay."""
    # Same scaffolding, but finish_build returns a BuildSummary with no
    # errors. exit_code must be 0.
```

A third, optional regression covers the opt-out:

```python
def test_build_exits_zero_with_no_fail_on_error_even_with_cell_errors(
    self, tmp_path, monkeypatch
):
    """Operator opt-out: --no-fail-on-error preserves legacy exit 0."""
```

**Acceptance**:
- Tests fail on `master` (`exit_code == 0` for the first one).
- Tests pass after Phases 2-3 land.
- Tests are fast (no real workers, no real kernels) — they belong in the
  fast suite (no marker).

**What landed** (commit not yet made; tests are staged in
`tests/cli/test_build_command.py`):

A new test class `TestBuildExitCodeOnCellErrors` with **8 tests** covering
both directions of every precedence axis. Five fail on master (good
regression baseline); three are pass-on-master sanity guards:

| Test | On master | After Phase 3 |
|---|---|---|
| `..._exits_nonzero_when_cell_errors_under_replay_mode` | ❌ fail (exit 0) | ✅ pass |
| `..._exits_zero_when_no_cell_errors_under_replay_mode` | ✅ pass (sanity) | ✅ pass |
| `..._exits_zero_with_no_fail_on_error_even_with_cell_errors` | ❌ fail (no flag) | ✅ pass |
| `..._exits_zero_under_new_episodes_default_with_cell_errors` | ✅ pass (default-off guard) | ✅ pass |
| `..._exits_nonzero_under_new_episodes_with_explicit_fail_on_error` | ❌ fail (no flag) | ✅ pass |
| `..._clm_fail_on_error_env_forces_failure` | ❌ fail (env ignored) | ✅ pass |
| `..._clm_fail_on_error_env_disables_failure_under_replay` | ✅ pass (sanity) | ✅ pass |
| `..._cli_flag_overrides_env_var` | ❌ fail (no flag) | ✅ pass |

Plus a module-level helper `_setup_mocked_build_pipeline(tmp_path,
monkeypatch, *, summary_errors)` that captures the 80-line scaffolding
from `test_build_runs_main_build_with_mocked_pipeline:897` and stubs the
fake `BuildReporter` so `finish_build()` returns a real `BuildSummary`
with the requested synthetic errors. Phase 2/3 implementation can rely on
this same helper for any follow-up tests.

**Important contract locked by these tests**: the two tests
`test_build_exits_nonzero_under_new_episodes_with_explicit_fail_on_error`
and `test_cli_flag_overrides_env_var` assert `result.exit_code == 1`
specifically (not just `!= 0`). Phase 3 **must** use `sys.exit(1)` — not
`raise SystemExit("...")` with a string, and not any other exit code —
for the cell-error failure path. The handover's Phase 3 sketch already
specifies `sys.exit(1)`; keep it that way.

### Phase 2: Thread `BuildSummary` back from `main_build` [TODO]

**Discoveries from Phase 1 to apply here**:

- `BuildReporter(formatter)` is constructed at exactly one site —
  `build.py:1134`. The Phase 1 tests mock that constructor via
  `monkeypatch.setattr(build_module, "BuildReporter", lambda formatter: fake)`,
  so Phase 2's plumbing changes are observable end-to-end through
  `main_build` without touching the construction site.
- `BuildSummary.errors: list[BuildError]` exists with a `severity` field
  (`build_data_classes.py:118-147`); the `severity` is already `"error"`
  for everything `BuildReporter` collects, so a flat `len(summary.errors)
  > 0` check at the entry point is correct. No data-class changes
  needed.
- `BuildReporter.finish_build` is called from **two** places inside
  `_run_stages` (the image-collision early-exit at `build.py:810` and
  the normal-path `finally` at `build.py:853`). The early-exit already
  `raise SystemExit("...")` with a string message — leave it alone. Only
  the line-853 call needs its return value captured and threaded out.
- The watch-mode loop wraps `process_course_with_backend` calls inside
  `main_build` — when Phase 2 starts returning a summary, watch mode
  must discard it (or return `None`) so the entry-point exit check
  never fires for watch builds. The simplest shape: `main_build` returns
  `BuildSummary | None`, where watch mode returns `None`.

**File**: `src/clm/cli/commands/build.py`

**Changes**:

1. `process_course_with_backend` (line 780) — `_run_stages` already calls
   `build_reporter.finish_build()` at lines 810 and 853. Capture the
   return value at line 853 (the normal-path call; line 810 is the
   image-collision early-exit which already raises `SystemExit`) and
   return it from `_run_stages`. Propagate it out of
   `process_course_with_backend`.

2. `main_build` (line 1018) — capture the summary from
   `process_course_with_backend(...)` and return it (or return
   `None` in the watch-mode/early-exit paths, since the Click entry
   point will only call this in one-shot mode).

3. Watch mode (`build.py:990-1015`) — does not return a summary;
   that's fine, watch mode never exits via this path.

**Important**: do NOT change the call signature of `finish_build()`. It
already returns `BuildSummary` (line 348 of `build_reporter.py`); the
current callers ignore the return value. We're just plumbing what's
already there.

**Acceptance**: existing tests still pass (especially
`test_build_runs_main_build_with_mocked_pipeline`); the new Phase 1
tests still fail (no exit logic yet, just plumbing).

### Phase 3: Add `--fail-on-error` flag + exit logic [TODO]

**Design choices locked during Phase 1** (committed via the test
contract; do not change without updating the tests in
`TestBuildExitCodeOnCellErrors`):

- Flag name: `--fail-on-error / --no-fail-on-error` (Click two-option
  pattern, `default=None` tri-state).
- Default policy: **on** under `--http-replay=replay` (including the
  CI-aware default), **off** elsewhere.
- Env var: `CLM_FAIL_ON_ERROR` accepting `{1,true,yes,0,false,no}`
  (case-insensitive); invalid values raise `click.UsageError`.
- Precedence: explicit CLI flag > `CLM_FAIL_ON_ERROR` > replay-mode
  default.
- Exit code for the cell-error failure path: **exactly `sys.exit(1)`**
  — two Phase 1 tests assert `result.exit_code == 1` specifically.
- Failure message before exit: `click.echo(..., err=True)` (per Phase 3
  sketch below). The Phase 1 tests do not assert the message text, so
  the exact wording is flexible.

**File**: `src/clm/cli/commands/build.py`

**Changes**:

1. Add a Click option near the `--http-replay` block (line 1417+):

   ```python
   @click.option(
       "--fail-on-error/--no-fail-on-error",
       default=None,  # tri-state: None = use replay-mode default
       help=(
           "Exit with non-zero status if any cell/notebook error is "
           "reported during the build. Default: on under "
           "--http-replay=replay (incl. CI default), off otherwise. "
           "Override via CLM_FAIL_ON_ERROR={1,true,yes,0,false,no}."
       ),
   )
   ```

   Add `fail_on_error` to the parameter list of the `build` function
   (line 1461) and to the `main_build` parameter list (line 1018) if
   `main_build` needs to see it. (Probably not — the decision lives in
   the entry point after `asyncio.run` returns.)

2. Add a resolver next to `_resolve_http_replay_mode`
   (`build.py:51-74`):

   ```python
   def _resolve_fail_on_error(
       cli_value: bool | None, resolved_http_replay_mode: str
   ) -> bool:
       """Precedence: CLI > CLM_FAIL_ON_ERROR > replay-mode default."""
       if cli_value is not None:
           return cli_value
       env_value = os.environ.get("CLM_FAIL_ON_ERROR")
       if env_value is not None:
           normalized = env_value.strip().lower()
           if normalized in ("1", "true", "yes"):
               return True
           if normalized in ("0", "false", "no"):
               return False
           raise click.UsageError(
               f"Invalid CLM_FAIL_ON_ERROR={env_value!r}. "
               "Valid values: 1/true/yes/0/false/no."
           )
       return resolved_http_replay_mode == "replay"
   ```

3. Wire the exit at the entry point, after `asyncio.run(...)` returns
   (`build.py:1584-1622`) and **before** the `--verify-against` block
   (line 1639) so the cause is unambiguous:

   ```python
   summary = asyncio.run(main_build(...))

   resolved_fail_on_error = _resolve_fail_on_error(
       fail_on_error, resolved_http_replay_mode
   )
   if (
       resolved_fail_on_error
       and summary is not None
       and len(summary.errors) > 0
   ):
       click.echo(
           f"\nBuild failed: {len(summary.errors)} error(s) reported "
           f"during build. See summary above.",
           err=True,
       )
       sys.exit(1)
   ```

   Note: `resolved_http_replay_mode` is currently computed inside
   `main_build` (line 1074). Hoist it to the entry point (compute once,
   pass into `main_build`) so the exit-policy resolver can see it
   without re-implementing the logic. This is a small refactor — keep
   the existing precedence semantics intact.

**Acceptance**:
- Phase 1 tests pass.
- Watch mode still loops; SIGTERM still wins (line 1555).
- `--verify-against` divergence still exits 1; combined verify + cell
  errors still exit 1 (with the cell-error message first, since we
  check it first).

### Phase 4: Docs + CHANGELOG [TODO]

**Files**:

- `src/clm/cli/info_topics/commands.md` — add a row for
  `--fail-on-error/--no-fail-on-error` near the `--http-replay` family.
  Use `{version}` placeholder if referencing the version it landed in;
  do NOT hardcode the version number (CLAUDE.md §Info Topics).
- `CHANGELOG.md` under `[Unreleased]` → "Changed" or "Fixed" — note that
  `clm build` now exits non-zero on cell errors under `--http-replay=replay`
  by default, with `--no-fail-on-error` opt-out. Cite issue #90.

**Acceptance**:
- `clm info commands` output shows the new flag.
- CHANGELOG entry follows the style of the cassette/monitor entries
  (`f208612`).

## 4. Current Status

- **Issue filed**: 2026-05-19 by hoelzl, OPEN, no comments, no labels.
- **Phase 1 complete (2026-05-19)**: 8 CLI tests added to
  `tests/cli/test_build_command.py` under `TestBuildExitCodeOnCellErrors`.
  5 fail on master (the regression baseline), 3 are pass-on-master
  sanity guards. All 73 prior tests in the file still pass. `ruff check`
  clean, `ruff format` applied. **Tests are staged but not yet
  committed** — commit message per Next Steps step 3.
- **Predecessor work merged**: PR #87 (commits `fe0ecf5` + `63d8cfc`)
  closed issue #86 on 2026-05-19. That fix is what surfaced #90 as a
  separate bug. The retired archive at
  `docs/claude/http-replay-race-fix-handover-archive.md` (lines 246-265,
  287-289) flagged #90 explicitly.
- **No blockers** for Phases 2-4. Design choices locked into Phase 1's
  test contract (see "Design choices locked during Phase 1" in Phase 3
  above).

### Blockers / open questions

- None remaining. The open design choices in §2 are now locked by the
  Phase 1 test contract — changing flag name, defaults, env-var name,
  or exit code would require updating the staged tests too.
- Watch-mode interaction confirmed via design: `main_build` returns
  `BuildSummary | None`, watch mode returns `None`, entry-point exit
  check skips on `None`.

## 5. Next Steps

1. ~~Create a worktree off `master`.~~ — Done. Working in
   `worktree-tender-dancing-ember` (branch
   `worktree-tender-dancing-ember`) off master at `a10693a`.
2. ~~Lock the open design choices (§2).~~ — Done. Committed via the
   Phase 1 test contract; see "Design choices locked during Phase 1"
   under Phase 3 above.
3. **Phase 1 complete; commit staged tests** —
   `test(cli): add regression for exit code on cell errors (#90)`.
4. **Next: implement Phase 2** (plumb `BuildSummary` return value) —
   commit as `refactor(cli): thread BuildSummary back from main_build (#90)`.
5. Implement Phase 3 (flag + exit logic) — commit as
   `feat(cli): exit non-zero on cell errors under --http-replay=replay (#90)`.
   Must use `sys.exit(1)` exactly (Phase 1 tests assert `exit_code == 1`).
6. Implement Phase 4 (docs + CHANGELOG) — commit as
   `docs(cli): document --fail-on-error and CHANGELOG entry (#90)`.
7. Open a PR referencing #90; close #90 on merge.

### Gotchas

1. **Pre-commit hook runs the fast suite.** If a commit fails the hook,
   the commit did NOT happen. Fix the issue, re-stage, create a NEW
   commit. Never `--amend` a rejected commit. (See CLAUDE.md §Git
   Workflow.)

2. **Don't bundle this with broader exit-code cleanups.** Specifically,
   don't try to also fix `--verify-against`-style ordering or audit every
   `sys.exit` site in CLM. The scope is: cell-error → non-zero exit, with
   a flag and a sane default. Anything more is a separate PR.

3. **Watch mode is a trap.** It runs a build in a loop. Make sure the
   exit logic sits in the one-shot path only (outside `while not shut_down`
   in `watch_for_changes`). The cleanest place is the Click entry point,
   after `asyncio.run`, where the existing `--verify-against` exit also
   lives.

4. **`BuildReporter.finish_build` deduplicates errors.** The summary
   reflects unique errors; if the same cell error is reported twice from
   two output targets, it counts once. This is what we want — exit code
   should track "did anything fail," not raw error volume.

5. **`SystemExit` is already raised inside `_run_stages`** for image
   collisions (`build.py:812`). That path bypasses the new exit logic
   (it exits before `main_build` returns), which is fine — collisions
   are already a hard fail. The new flag controls cell-error exit only.

6. **CI default**: `_resolve_http_replay_mode` already returns
   `"replay"` when `CI=true`. So with the proposed default policy, CI
   builds will automatically exit non-zero on cell errors. This is the
   intended outcome — make sure to note it in the CHANGELOG so users
   are not surprised.

## 6. Key Files & Architecture

### Files implicated

| File | Role |
|---|---|
| `src/clm/cli/commands/build.py` | **All Phase 2-3 changes go here.** `main_build` at line 1018; `process_course_with_backend` at line 780; `_resolve_http_replay_mode` at line 51; Click entry point and `asyncio.run` call at lines 1461-1622; existing `sys.exit(1)` precedents at lines 1555 (SIGTERM) and 1652 (`--verify-against`). |
| `src/clm/cli/build_reporter.py` | **No changes expected.** `BuildReporter.finish_build` already returns `BuildSummary` (line 348). `errors` list at line 36; dedup at line 240. |
| `src/clm/cli/build_data_classes.py` | `BuildSummary.errors: list[BuildError]` — already structured for counting. |
| `src/clm/cli/error_categorizer.py` | Categories that surface cell failures: `cell_execution` (line 205), `notebook_compilation` (line 181), `notebook_processing` (line 217). Reference only — no changes needed. |
| `src/clm/cli/info_topics/commands.md` | **Phase 4: add a row for the new flag.** |
| `tests/cli/test_build_command.py` | **Phase 1 tests done.** `TestBuildExitCodeOnCellErrors` class + `_setup_mocked_build_pipeline` helper + `_make_cell_error` factory. Pattern adapted from `test_build_runs_main_build_with_mocked_pipeline:897`. Reuse `_setup_mocked_build_pipeline` for any Phase 2/3 follow-up tests. |
| `CHANGELOG.md` | **Phase 4: `[Unreleased]` entry**, cite #90. |

### How it fits

```
clm build --http-replay=replay [--fail-on-error/--no-fail-on-error]
  → @click build entry point (build.py:1461)
    → _resolve_http_replay_mode (line 51)           ← already there
    → asyncio.run(main_build(...))                  ← line 1584
        → main_build (line 1018)
            → process_course_with_backend (line 780)
                → _run_stages
                    → ... worker pipeline ...
                    → finally:
                        build_reporter.finish_build() → BuildSummary  ← Phase 2: capture
            ← Phase 2: return BuildSummary
        ← Phase 2: return BuildSummary
    ← captured `summary` in entry point             ← Phase 3
    → _resolve_fail_on_error(...)                   ← Phase 3: new
    → if resolved and summary.errors: sys.exit(1)   ← Phase 3: new
    → (existing --verify-against block at line 1639)
```

### Conventions to maintain

- **No `print()` in library code** — `logger.getLogger(__name__)`, or
  `click.echo(...)` for user-facing CLI output. The exit message in
  Phase 3 uses `click.echo(..., err=True)` since it's an error.
- **Defensive `# noqa: BLE001`** — existing style for defensive exception
  catches. Probably none needed here.
- **Python over bash** — the test harness should be pytest, not a shell
  script. (See CLAUDE.md §Code Conventions.)

## 7. Testing Approach

### Strategy

- **Unit/CLI tests** in `tests/cli/test_build_command.py` using
  `CliRunner` + monkeypatched `BuildReporter.finish_build` to inject a
  `BuildSummary` with synthetic errors. Fast, deterministic, no kernels.
- **No new integration test needed.** A real cell-crash integration test
  would require spinning up a Jupyter kernel; the existing pipeline tests
  already cover the error-capture side (see
  `tests/workers/notebook/test_notebook_error_context.py`). The exit-code
  decision is purely CLI-layer logic.
- **No Docker tests needed** — exit-code logic is identical in Docker mode.

### What's tested by the existing suite

- `BuildReporter.finish_build` returns a `BuildSummary` with deduplicated
  `errors` (existing tests in `tests/cli/test_build_reporter.py` if
  present, otherwise covered by the smoke pipeline tests).
- `test_build_runs_main_build_with_mocked_pipeline` exercises the full
  Click → `main_build` → `asyncio.run` plumbing with stubs.

### What's missing

- **Exit code != 0 on cell error**. Phase 1 covers this.
- **`--no-fail-on-error` opt-out**. Covered by optional third test.
- **Env-var precedence** (`CLM_FAIL_ON_ERROR`). Worth a small parametrized
  test if Phase 3 ships with the env-var resolver.

### Commands

```bash
# Targeted, fastest:
uv run pytest tests/cli/test_build_command.py -v

# Plus the build-reporter unit tests (if any):
uv run pytest tests/cli/ -v

# Fast suite (also what pre-commit runs):
uv run pytest

# Pre-release / pre-merge gate (Docker tests run in CI only):
uv run pytest -m "not docker"

# Lint + types:
uv run ruff check src/ tests/
uv run mypy src/
```

## 8. Session Notes

### Why this is small but easy to over-scope

The fix is conceptually one line: `if summary.errors: sys.exit(1)`. Most
of the work is:

- Plumbing the `BuildSummary` return value through one extra function
  call (Phase 2).
- Adding a Click flag with the right default and precedence rules
  (Phase 3).
- Not regressing watch mode and `--verify-against`.
- Updating `commands.md` and `CHANGELOG.md`.

Resist the temptation to also fix the long tail of other categories that
should arguably exit non-zero (`worker_timeout`, `missing_template`,
`output_path_conflict`, etc.). Those can ride on the same flag in a
follow-up; landing #90 with cell errors is the contract the issue asks
for.

### Why the issue's recommended default makes sense

`--http-replay=replay` is the strictest mode and the CI default (per
`_resolve_http_replay_mode` at `build.py:51-74`). In that mode, any cell
failure is, by definition, a divergence from a recorded successful run
— there's no transient excuse. Other modes (`new-episodes`, `refresh`)
exist precisely because users want to iterate over partial/transient
failures. Default-on under `replay`, opt-in elsewhere, matches the
intent of each mode without changing local workflows.

### Don't forget the info-topics rule

CLAUDE.md §"Info Topics Maintenance Rule (CRITICAL)" says: when a CLI
flag is added or changed, `src/clm/cli/info_topics/commands.md` MUST be
updated in the same change. Phase 4 covers this. `clm info commands`
output is what downstream agents in course repositories rely on.

### Investigation artifacts

None. This handover is based on:

- Reading issue #90 verbatim.
- Reading the predecessor archive (`http-replay-race-fix-handover-archive.md`
  §3 Phase 4 and §5 step 4) which flagged this issue.
- Walking `src/clm/cli/commands/build.py` (lines 1-1700), the
  `BuildReporter` class, and `error_categorizer.py` to confirm where
  errors materialize and where the entry point exits.

No scratch scripts or repros were created. The mocked-pipeline test
pattern in `test_build_command.py:897` is sufficient to drive Phase 1.

---

**Last Updated**: 2026-05-19 (Phase 1 complete; tests staged, not yet
committed; Phase 2 next)
**Author**: Claude Opus 4.7 (1M context) under user `tc`
