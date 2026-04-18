# Handover: Test Coverage Expansion (Round 2)

**Created**: 2026-04-17
**Last Updated**: 2026-04-18 (PR 6 shipped — round 2 target met)
**Starting coverage (fast suite)**: **74%** (5,264 / 20,037 statements missed, 3,311 tests)
**Current coverage (fast suite)**: **86.20%** (after PR 6; 3,882 tests passing + 4 xfailed, 20,038 statements)
**Measurement command**: `pytest -m "not docker and not slow and not integration and not e2e" --cov=src/clm`
**Target**: ✅ **≥ 85% achieved** — round 2 is complete.

Prior coverage effort (53% → 69%, Phases 1–4) is archived in
`docs/claude/test-coverage-continuation-guide.md`. **This document does not re-plan that work** — it
plans the next round, incorporating explicit decisions from the maintainer (see §3).

## PR status (round 2)

| PR | Focus | Planned Δ | Actual Δ | Status |
|---|---|---:|---:|---|
| 1 | `cli/commands/{database,config}.py` + `core/course.py` JupyterLite paths | +1.5% | +1.13% | ✅ shipped |
| 2 | `cli/output_formatter.py` verbose + `sqlite_backend.py` resilience | +1.5% | +1.20% | ✅ shipped |
| 3 | MCP server, JupyterLite worker, Monitor TUI smoke/diagnostic tests | +1.5% | +1.81% | ✅ shipped (4 xfail bugs documented) |
| 4 | `cli/commands/build.py`, `cli/commands/docker.py`, `infrastructure/api/*` | +4.0% | +3.95% | ✅ shipped |
| 5 | `recordings/processing/{utils,pipeline,compare}.py` | +1.5% | +1.08% | ✅ shipped |
| 6 | Remaining low-hanging CLI + `recordings/processing/batch.py` | +2.0% | +3.03% | ✅ shipped |

**Round 2 final**: 74% → 86.20% (+12.20pp across PRs 1–6). **Target met** (≥ 85%).

---

## 1. How to reproduce the baseline

```powershell
# from worktree or main repo
uv run pytest -m "not docker and not slow and not integration and not e2e" `
  --cov=src/clm --cov-report=term-missing --cov-report=json:coverage.json -q
```

The JSON report drives the per-file numbers below. Regenerate after each PR and diff against
`coverage.json` to confirm the expected lift.

Do **not** chase the Docker/integration-only paths in the fast suite — many low numbers in
`infrastructure/api/*` and `cli/commands/docker.py` look worse than they are in CI because the
corresponding test files are marked `docker`/`integration` and skipped.

---

## 2. Where the gaps are

Ranked by absolute missed lines in the fast suite. Numbers are from the 2026-04-17 run.

| Area | Miss | Cov | Read as |
|---|---:|---:|---|
| `cli/commands/*` (29 files) | 2,207 | 47.6% | Biggest single area; user-facing |
| `infrastructure/workers/*` | 421 | 78% | Critical; docker paths inflate gap |
| `cli/monitor/*` (Textual TUI) | 343 | 27% | Has display bugs — see §3.4 |
| `cli/output_formatter.py` | 142 | 61% | VerboseOutputFormatter detail paths |
| `infrastructure/api/*` | 224 | 50% | Reachable via FastAPI TestClient |
| `recordings/processing/*` | 200 | 36% | **Not legacy** — see §3.3 |
| `infrastructure/backends/sqlite_backend.py` | 178 | 58% | Resilience / dead-worker recovery |
| `core/course.py` | 140 | 59% | New JupyterLite flow (see §3.2) |
| `voiceover/*` | 197 | 79% | Backend-gated (Whisper/HF/Mistral) |

### 2.1 Files completely uncovered (0%)

| File | Stmts | Notes |
|---|---:|---|
| `cli/monitor/app.py` | 84 | TUI shell — tests in PR 3 |
| `cli/monitor/widgets/workers_panel.py` | 77 | Display bugs live here |
| `cli/monitor/widgets/queue_panel.py` | 39 | Display bugs live here |
| `workers/jupyterlite/jupyterlite_worker.py` | 62 | **Not docker-only** — see §3.1 |
| `mcp/server.py` | 45 | Thin FastMCP registration layer — see §3.1 |
| `recordings/processing/compare.py` | 20 | Used by `clm recordings compare` |
| `cli/commands/*/__main__.py` | 1–1 | Entry shims; skip |

---

## 3. Maintainer decisions and clarifications (2026-04-17)

These override the initial triage.

### 3.1 MCP server and JupyterLite worker: both need smoke tests

- **MCP server** (`src/clm/mcp/server.py`). `mcp/tools.py` is already 90% covered; only the FastMCP
  wiring in `create_server(data_dir)` is untested. A smoke test that calls `create_server()` and
  inspects the registered tools is enough to catch registration regressions and argument-drift
  between the `@mcp.tool()` wrappers and the underlying `handle_*` functions.
- **JupyterLite worker** (`src/clm/workers/jupyterlite/jupyterlite_worker.py`). It is **not**
  docker-only — JupyterLite sites build on host Python when the `[jupyterlite]` extra is installed.
  Unit tests should construct a `JupyterLiteWorker` against an in-memory SQLite job queue, stub
  `builder.build_site`, and assert that `_process_job_async` correctly unpacks the payload into
  `BuildArgs` and writes a cache entry.

### 3.2 `core/course.py`: new code landed without direct tests

Missed lines 420–481 and 490–515 implement `process_jupyterlite_for_targets` and related per-target
aggregation introduced in commits `e0a2a2d` (`feat(jupyterlite): report JupyterLite generation as
its own build phase`) and `d3da1a3` (`refactor(jupyterlite): one site per (target, language), not
per kind`). This is a user-visible build phase with no direct test coverage today. Extend the
existing multi-target / `DummyBackend` patterns in `tests/core/test_multi_target_course.py`.

### 3.3 `recordings/processing/*` is actively used — not legacy

Verified imports (2026-04-17): the module is a shared helper layer for the recordings pipeline,
imported from

- `cli/commands/recordings.py` — `pipeline`, `compare`, `utils`, `batch`, `config`
- `recordings/workflow/assembler.py` — `utils.find_ffmpeg`, `utils.run_subprocess`
- `recordings/workflow/backends/{onnx,external,auphonic}.py` — `batch.VIDEO_EXTENSIONS`,
  `config.PipelineConfig`, `utils`
- `recordings/workflow/directories.py` — `batch.VIDEO_EXTENSIONS`

The `clm recordings serve` command (currently being significantly extended) depends on this layer
transitively via `recordings/web/app.py` and the workflow backends. Keep coverage here as a
priority (PR 5), and do **not** omit the module from coverage.

### 3.4 Monitor TUI has display bugs — tests should find them

The TUI is in active use but has known display bugs. The testing goal is **diagnostic** (reproduce
bugs in a test so they can be fixed and regressions caught), not just chasing line coverage. Use
Textual's built-in `App.run_test()` pilot harness plus direct unit-level calls into
`ActivityPanel.update_events`, `WorkersPanel.update_workers`, `QueuePanel.update_queue`, and
`StatusHeader.update_status` with synthesized `StatusInfo` / `ActivityEvent` data. See §5.3 for
recipe.

---

## 4. PR plan

Five PRs in order. PRs 1–4 are shipped; only PR 5 remains.

```
PR 1  ✅ shipped     ──►  PR 2  ✅ shipped
PR 3  ✅ shipped     (smoke tests, independent)
PR 4  ✅ shipped     (CLI + API, independent of 1-3)
PR 5  ⏳ remaining   (processing, depends only on tooling)
```

Actual coverage deltas recorded per PR (see the status table at the top of this doc). PR 5 has a
planned delta of +1.5pp that should land the round at or above the 85% target.

Rules for every PR:

- All new tests run in the fast suite (`pytest -m "not docker and not slow and not integration and not e2e"`).
- Follow existing patterns — don't invent a new harness when `click.testing.CliRunner`,
  `fastapi.testclient.TestClient`, `textual.app.App.run_test()`, or `DummyBackend` fits.
- If a module has a genuine external dependency (ffmpeg, Whisper, Docker daemon), mock at the
  narrowest stable seam (e.g., `subprocess.run`, `builder.build_site`).
- Update `docs/claude/test-coverage-continuation-guide.md` with a new Phase 5+ row per PR so the
  historical log stays complete.
- Never loosen existing coverage thresholds to mask regressions.
- Pre-commit hooks run ruff, mypy, and the fast suite. If a hook fails the commit did **not**
  happen — fix, re-stage, make a **new** commit (no `--amend`).

---

## 5. PR-by-PR recipes

> **PRs 1–4 are shipped.** Their recipes remain below as historical reference for what was built
> and how. Only §5.5 (PR 5) is still actionable work.

### PR 1 — Small, high-ROI CLI + core coverage ✅ shipped (2026-04-17)

**Goal**: cover unit-testable user-facing commands and the new JupyterLite build path.

**Scope**:

1. **`cli/commands/config.py`** (78 stmts, 67 miss → target 90%).
   - Subcommands: `clm config init` (`--location {user,project}`, `--force`),
     `clm config show`.
   - Test via `click.testing.CliRunner` + `tmp_path`; patch `platformdirs.user_config_dir` to
     redirect the user-level path into `tmp_path`.
   - Assert that `init` writes a TOML file with the documented keys, respects `--force`, and
     refuses to overwrite without it.
   - Assert that `show` prints the resolved merged config (user + project layers) — verify
     precedence when both files exist.

2. **`cli/commands/database.py`** (206 stmts, 164 miss → target 85%).
   Commands: `stats`, `prune`, `vacuum`, `clean`.
   - `stats`: construct a `JobQueue(tmp_db)` with a couple of jobs, run `clm db stats`, assert the
     row counts in the output.
   - `prune`: seed old completed jobs (backdate `completed_at`), run `clm db prune --older-than 7d`,
     assert row count and that the `--dry-run` path does not modify the DB.
   - `vacuum`: assert it runs to completion on `jobs` and `cache` DBs and logs expected output.
   - `clean`: with `--force`, verify it removes orphaned rows; without `--force`, verify prompts /
     dry-run behavior. `--remove-missing` should delete cache rows whose files are gone.
   - Existing smoke test lives in `tests/cli/test_workers_reap.py::test_clean_db_clean_processes` —
     follow its pattern for DB setup.

3. **`core/course.py` JupyterLite aggregation** (lines 420–481, 490–515).
   - Extend `tests/core/test_multi_target_course.py` with fixtures that include a target with
     `format="jupyterlite"` and an effective `<jupyterlite>` config.
   - Use a `DummyBackend` that records every submitted operation; assert that
     `process_jupyterlite_for_targets` produces exactly one `BuildJupyterLiteSiteOperation` per
     `(target, language)` pair and that `notebook_trees` contains one entry per kind.
   - Add a negative test for the "no config → skip" path and the `opted_in_jupyterlite_site_count`
     accessor.

**Done when**:
- `cli/commands/config.py` and `cli/commands/database.py` reach ≥ 85% each.
- `core/course.py` missing-lines list no longer includes 420–481, 490–515.
- Overall coverage rises by ≈ 1.5 percentage points.

---

### PR 2 — Formatting and persistence resilience ✅ shipped (2026-04-17)

**Goal**: lock down user-facing error output and the SQLite backend's recovery paths.

**Scope**:

1. **`cli/output_formatter.py`** (360 stmts, 142 miss → target 85%).
   Missed blocks are the verbose renderer's detail sections (lines 289–342, 355–468, 640–746).
   Add table-driven tests over `VerboseOutputFormatter`:
   - `_show_error_detail(index, error)` for each `BuildError` severity / source-type combination.
   - `show_summary(summary)` with: zero errors, only warnings, mixed, and an overflow summary where
     error/warning lists are truncated.
   - `show_file_completed` and `show_stage_start` when invoked with unusual file counts (0, 1, 1000).
   - Capture `rich` output via `Console(record=True)` and diff against approval fixtures (or assert
     substrings — approval fixtures are fine but keep them small).
   - Existing file `tests/cli/test_build_output.py` (64 tests) is the model; extend the same
     `Console(record=True)` fixture.

2. **`infrastructure/backends/sqlite_backend.py`** (424 stmts, 178 miss → target 80%).
   Prioritize resilience paths — these are the bugs that silently leak jobs.
   - `_cleanup_dead_worker_jobs` (lines 236–291): seed a job in state `processing` whose worker is
     `dead`, call the method, assert the job is reset to `pending` with `worker_id` cleared and
     `started_at = NULL`; also assert the early-return when no stuck jobs exist and the exception
     rollback path (inject a `conn.execute` that raises).
   - `wait_for_completion` edge cases (lines 329–337, 368–477): empty queue + `all_submitted` set,
     queue drained while events still pending, mid-flight cancellation.
   - `submit_*` error paths (lines 744–815, 838–846, 870–919): assert exceptions are logged and
     surfaced, and that retry bookkeeping stays consistent.
   - Use `tmp_path / "jobs.db"` + `init_database`, not `:memory:` (the code paths open their own
     connections and the operations rely on filesystem-backed SQLite).

**Done when**:
- `cli/output_formatter.py` ≥ 85%, `sqlite_backend.py` ≥ 80%.
- Overall coverage rises by ≈ 1.5 percentage points.

---

### PR 3 — Smoke + diagnostic tests (MCP, JupyterLite worker, Monitor TUI) ✅ shipped (2026-04-17)

**Outcome**: 55 pass + 4 xfail tests across three files (`test_server.py`, `test_jupyterlite_worker.py`,
`test_monitor_app.py`). Coverage 76.33% → 78.14% (+1.81pp). Actual per-file: mcp/server.py 82.2%,
jupyterlite_worker.py 100%, monitor/app.py 97.6%, activity_panel.py 83.5%, status_header.py 100%,
workers_panel.py 100%, queue_panel.py 94.9%, data_provider.py 84.6%.

**Monitor TUI bugs documented as strict xfail tests** (still unfixed at end of PR 3 — fixing these
is separate follow-up work):
- Bug #1 — `(?)` duration for 1-second jobs: `julianday()` rounding loses 1s → `CAST(... AS INTEGER) = 0`
  in `data_provider.get_recent_events`; `format_elapsed(0)` renders `"?"` because 0 is falsy in
  `activity_panel._write_event`. Two xfail tests reproduce the data-layer and presentation-layer
  halves of the bug.
- Bug #1 — stale "Started" entries: an xfail test documents that completing a job should remove
  its "Started" entry from the activity log; currently it doesn't.
- Bug #2 — empty title area: `status_header._render_content` uses only health/workers/queue/
  completed-last-hour; has no course-spec tracking. An xfail test asserts the header should show
  the currently-processing course spec name.
- Bug #3 — scroll lag: `WorkersPanel._render_workers` calls `content_widget.remove_children()`
  every tick, forcing Textual to reconstruct the scroll layout. Documented as a module-level
  comment (not a perf test — too flaky for CI).

**Goal**: fill the three zero-coverage surfaces the maintainer flagged, and give the monitor team
a foothold for tracking the known display bugs.

This PR is **independent of PR 1/PR 2** and can land in parallel.

#### 3A. MCP server (`src/clm/mcp/server.py`)

Pattern: call `create_server(data_dir)` and introspect the registered tools. No stdio transport.

- Create `tests/mcp/test_server.py`.
- Reuse `course_tree` fixture from `tests/mcp/test_tools.py` (import or duplicate minimally).
- Test 1: `create_server(data_dir)` returns a `FastMCP` instance named `"clm"`.
- Test 2: the expected tool set is registered:
  `{resolve_topic, search_slides, course_outline, validate_spec, validate_slides,
    normalize_slides, get_language_view, suggest_sync, extract_voiceover, inline_voiceover,
    course_authoring_rules}` (11 tools; exact list derived from `server.py`).
- Test 3: each registered tool's call signature matches its underlying `handle_*` function for
  required arguments. Use `inspect.signature` on the handler vs. the tool's declared parameters
  (FastMCP exposes these via `mcp._tools` or `await mcp.list_tools()` — check the installed
  `mcp` SDK version; prefer the async `list_tools` accessor if present).
- Test 4: one end-to-end smoke — call the `course_outline` tool through `mcp.call_tool(...)` (or
  whatever the SDK's in-process dispatch is called) against the fixture course and assert the JSON
  shape. This catches wiring bugs that the handler-level tests in `test_tools.py` cannot.
- Also cover the thin `run_server(data_dir)` helper: patch `FastMCP.run` and assert it was called
  with `transport="stdio"`.

Target: `mcp/server.py` ≥ 80%.

#### 3B. JupyterLite worker (`src/clm/workers/jupyterlite/jupyterlite_worker.py`)

Pattern: construct a worker against a real tmpdir SQLite DB with `init_database`, stub
`clm.workers.jupyterlite.builder.build_site`, enqueue a synthetic job via `JobQueue.submit_job`,
call `_process_job_async` directly.

- Create `tests/workers/jupyterlite/test_jupyterlite_worker.py`.
- Test 1 — payload unpacking: build a `payload` dict with `notebook_trees`, `output_dir`, `kernel`,
  `wheels`, `environment_yml`, optional branding fields; assert `build_site` receives a `BuildArgs`
  with the right types (`Path` for wheels / env file, stringy fields preserved).
- Test 2 — cache write: stub `build_site` to return a `BuildResult(cache_key="abc", files_count=3,
  site_dir=...)` and assert `JobQueue.add_to_cache` is called with
  `(job.output_file, job.content_hash, {"cache_key": "abc", "files_count": 3, "summary": ...})`.
- Test 3 — cancelled job: set `is_job_cancelled` to `True` via the real queue and assert
  `build_site` is not called.
- Test 4 — error handling: have `build_site` raise; assert the worker surfaces the exception to
  the caller (so the base `Worker.run` marks the job failed).
- Test 5 — `main()` smoke: patch `Worker.get_or_register_worker`, `JupyterLiteWorker.run`,
  `init_database`, and run `main()` with `DB_PATH` / `CLM_API_URL` combinations; assert the SQLite
  vs. API branches choose correctly.
- Do **not** add this to the `docker` mark; it runs on host Python.

Target: `jupyterlite_worker.py` ≥ 80%.

#### 3C. Monitor TUI diagnostic tests

Goal: turn the known display bugs into failing or parameterized tests so they can be fixed and
locked in. The user has observed bugs — write tests that reproduce them, confirm with the user,
then fix.

Prep step (do this first so PR 3 can be reviewed as green):

1. Open an issue or TODO.md entry listing the observed display bugs (ask the user for the current
   list; do not invent symptoms).
2. For each bug, add a test that reproduces it (marked `@pytest.mark.xfail(reason=...)` if not
   fixed in this PR) so the signal is preserved even when the test cannot yet pass.
3. Bugs you fix in this PR: flip the test from xfail to passing in the same commit.

Test harness:

- **Unit level** (fast, deterministic): call widget methods directly.
  - `ActivityPanel`: mount via a tiny host `App`, call `update_events(events)` with synthesized
    `ActivityEvent` lists, then introspect the `RichLog` contents. The event dedup key is
    `f"{job_id}:{event_type}"` — test that (a) duplicates are dropped, (b) ordering is
    chronological (oldest → newest), (c) scroll position is preserved when user has scrolled away.
  - `WorkersPanel`: call `update_workers(stats)` with `WorkerTypeStats` including edge cases
    `total=0`, all-dead, mixed busy/idle, and long `input_file` paths (check truncation).
  - `QueuePanel`: call `update_queue(stats)` with pending/processing/retry mixes.
  - `StatusHeader`: call `update_status(info)` with a `StatusInfo` built from
    `tests/cli/test_monitor_unit.py` fixtures; include extremes (1 worker vs. 100 workers).
- **Integration level** (driving the app):
  ```python
  async with CLMMonitorApp(db_path=tmp_db).run_test() as pilot:
      await pilot.pause()
      # introspect widgets: pilot.app.query_one(ActivityPanel) …
  ```
  Use Textual's `App.run_test()` (no real terminal). Assert widget state after simulated key
  presses (refresh, quit) and after the data provider is poked with fake events.

- **Data provider** (`cli/monitor/data_provider.py`, currently 45%): seed a `JobQueue` with a
  realistic sequence of jobs (pending → processing → completed / failed), call
  `DataProvider.get_activity_events()`, `.get_queue_stats()`, `.get_worker_stats()`, and assert the
  `StatusInfo` model fields. Most of the missing lines (123–223) are aggregation branches that a
  handful of seeded fixtures can exercise.

Target: each widget ≥ 60%, `data_provider.py` ≥ 80%. Overall monitor subtree lifts from 27% → 60%.

---

### PR 4 — Build CLI, Docker CLI, and API layer ✅ shipped (2026-04-18)

**Outcome**: 205 new tests across 6 files. Coverage 78.14% → 82.09% (+3.95pp, essentially on plan).

**Per-file actuals**:
- `cli/commands/build.py`: 43.6% → **80%** (48 tests in `test_build_command.py`)
- `cli/commands/docker.py`: 11.8% → **99%** (70 tests in `test_docker_command.py`)
- `infrastructure/api/client.py`: 50% → **99%** (26 tests in `test_client.py`)
- `infrastructure/api/job_queue_adapter.py`: 27% → **100%** (23 tests in `test_job_queue_adapter.py`)
- `infrastructure/api/server.py`: 49% → **100%** (17 tests in `test_server.py`)
- `infrastructure/api/worker_routes.py`: 35% → **100%** (21 tests in `test_worker_routes_endpoints.py`;
  original `test_worker_routes.py` kept as-is)

**Testing patterns worth knowing for future work in these areas**:
- **Rich console capture**: both `build.py` and `docker.py` bind `console = Console(file=sys.stderr)`
  at import time, so CliRunner stream isolation does *not* capture their output. Swap the module's
  `console` / `cli_console` for a `Console(file=StringIO(), force_terminal=False, no_color=True)`
  and assert on `buf.getvalue()`. Used in both `test_docker_command.py::captured_console` fixture
  and `test_build_command.py::TestReportValidationErrors::test_quiet_mode_emits_short_message`.
- **FastAPI 500 paths**: `TestClient(app, raise_server_exceptions=False)` lets the test observe the
  500 response instead of re-raising. Also, patch a method used *inside* each handler's
  `try`/`except` (e.g. `JobQueue._get_conn`, `JobQueue.get_next_job`) so the handler's generic
  `Exception → HTTPException(500)` conversion runs.
- **Fake uvicorn**: `WorkerApiServer._run_server` is tested with a `FakeUvicornServer` stand-in that
  mimics `should_exit` / `run()`; lets lifecycle, idempotent-start, and stop-on-stubborn-thread
  paths run without opening a socket. One real-uvicorn smoke test exists on port 0 and is
  marked `@pytest.mark.slow`.
- **`build` CLI wrapper tests**: the command reads `ctx.obj["CACHE_DB_PATH"]` / `["JOBS_DB_PATH"]`
  set by the top-level `clm` group. Provide them via `CliRunner().invoke(build, args, obj={...})`
  (the `_invoke_build` helper in `test_build_command.py`).

**Goal**: the big three CLI surfaces that dominate the `cli/commands/` missed-line count, plus the
FastAPI boundary that's currently only reached through docker-marked tests.

**Scope**:

1. **`cli/commands/build.py`** (530 stmts, 299 miss → target 80%).
   - Test with `CliRunner` + a stub `Backend` that records submitted operations (pattern from
     `tests/core/*`).
   - Cover the main flag matrix: `--targets`, `--languages`, `--kinds`, `--formats`,
     `--only-sections`, `--watch-only-sections` (watch tests already exist; extend them),
     `--clean`, `--force-rebuild`, `--dry-run`, reporter selection flags.
   - Assert the expected phase sequence (`discover → notebook → plantuml → drawio → jupyterlite →
     dir-groups → copy`) and that `--only-sections` correctly filters.
   - Edge cases: missing spec file, invalid target name, no work to do.

2. **`cli/commands/docker.py`** (424 stmts, 374 miss → target 70%).
   - This shells out; **do not** invoke real `docker`. Mock `subprocess.run` and
     `subprocess.Popen` at the boundary.
   - Cover `build`, `push`, `pull`, `prune`, cache-stage logic for each service in
     `SERVICE_NAME_MAP`, `get_project_root()`, version string composition.
   - Assert the exact argv list passed to each `docker` invocation (this is how real regressions
     bite — a flag typo). Use a helper like
     `assert called_docker_with(["buildx", "build", "--cache-to", ...])`.
   - Use `runner.invoke(cli, [...], catch_exceptions=False)` so tracebacks aren't swallowed.

3. **`infrastructure/api/*`** (444 stmts, 224 miss; server 49%, client 50%, worker_routes 35%,
   job_queue_adapter 27% → target 80% each).
   - Use `fastapi.testclient.TestClient` against `create_app(job_queue=…)` to exercise
     `worker_routes` in-process.
   - Pattern is already in `tests/recordings/test_web.py`; import `TestClient` the same way.
   - Cover each route with happy path + one error: register worker, heartbeat, claim job, report
     progress, complete job, fail job, list workers.
   - For the `ApiClient` side, drive it against the same test app (`TestClient` can serve as an
     `httpx` transport), so client + server contract-test together.
   - `job_queue_adapter.py` is thin — one test per public method should lift it to > 85%.

**Done when**:
- All three areas reach the stated thresholds.
- Overall coverage rises by ≈ 4 percentage points.

---

### PR 5 — Recordings processing helpers ✅ shipped (2026-04-18)

**Outcome**: 56 new tests across three files. Coverage 82.09% → 83.17% (+1.08pp, slightly under
plan — the processing files were smaller than the CLI surfaces of PR 4, so per-file coverage
overshoots produced a modest total lift).

**Per-file actuals**:
- `recordings/processing/utils.py`: 22% → **99%** (28 tests in `test_processing_utils.py`)
- `recordings/processing/pipeline.py`: 37% → **98%** (extended `test_processing_pipeline.py`; 19 total)
- `recordings/processing/compare.py`: 0% → **100%** (9 tests in `test_processing_compare.py`)

**Testing patterns worth knowing for future work in these areas**:
- **ONNX denoise trims the algorithmic delay from the front of the buffer**: test assertions on
  `sf.write` output length must use `n_samples - delay` (where `delay = ONNX_FFT_SIZE - ONNX_HOP_SIZE`,
  i.e., 480 at 48 kHz) instead of the original sample count. Input length must also be a multiple
  of `ONNX_HOP_SIZE` or padding is added; mock-friendly test inputs pick a clean hop multiple.
- **`find_binary` Windows fallback**: patch both `shutil.which` *and* `Path.is_file` to exercise the
  `sys.prefix / "Scripts"` branch. `monkeypatch.setattr(sys, "platform", "win32")` is enough to
  swap the platform check — the function reads `sys.platform` at call time.
- **Pipeline end-to-end happy path**: patch `run_subprocess`, `run_onnx_denoise`, and
  `get_audio_duration` at `pipeline_module` (the `from .utils import …` imports rebind into the
  pipeline namespace). Return a loudnorm JSON on the `null` (measure pass) invocation and a plain
  completed-process on all others — the apply-pass picks up the measured values.
- **`tempfile.mkdtemp` inspection**: patching `pipeline_module.tempfile.mkdtemp` with a side_effect
  that records the real return value lets tests assert `keep_temp=True` preserves the temp dir
  (and `False` removes it), without stubbing out disk writes.

### PR 6 — Remaining gap to 85% ✅ shipped (2026-04-18)

**Outcome**: 180 new tests across 7 files. Coverage 83.17% → **86.20%** (+3.03pp, well over the
~+2.1pp plan). Round-2 target (≥ 85%) achieved.

**Per-file actuals** (target → actual, where *actual* is the per-file coverage measured across
the full fast suite):
- `cli/commands/jupyterlite.py`: 23% → **94%** (15 tests in `test_jupyterlite_command.py`)
- `cli/commands/monitoring.py`: 23% → **90%** (16 tests in `test_monitoring_command.py`)
- `cli/commands/polish.py`: 28% → **100%** (14 tests in `test_polish_command.py`)
- `cli/commands/recordings.py`: 52% → **91%** (42 tests in `test_recordings_command.py`; existing
  `test_cli_recordings.py` / `test_recordings_auphonic_cli.py` stay in place)
- `cli/commands/voiceover.py`: 26% → **59%** (38 tests in `test_voiceover_command.py`; 1pp under
  the 60% target because the `sync` command's `_merge_notes` helper and the multi-part
  orchestration loop are integration-shaped and were not unit-mocked here)
- `cli/commands/zip_ops.py`: 46% → **100%** (31 tests extending `test_zip_ops.py`)
- `recordings/processing/batch.py`: 49% → **100%** (24 tests extending `test_batch.py`)

**Testing patterns worth knowing for future work in these areas**:
- **`sys.modules` injection for lazy imports**: `monitoring.serve`, `monitoring.monitor`,
  `recordings.process`/`batch`/`assemble`/`serve_recordings`, and the `voiceover.*` commands do
  their heavy imports *inside* the command body, so the mocks must be installed on `sys.modules`
  *before* `runner.invoke(...)`. `monkeypatch.setitem(sys.modules, "clm.web.app", fake_module)`
  is the pattern.
- **Real `JobManager` + stub `ProcessingBackend`**: `TestWaitJobCommand` reuses
  `test_cli_recordings.py`'s pattern — constructing an actual `JobManager(root_dir, store, bus)`
  with a hand-written `ProcessingBackend` subclass whose `poll()` flips the job's state on the
  first tick. Patching `time.sleep` keeps the wait loop fast without flakiness.
- **`_get_*_config` helper fallbacks**: each helper has a `try/except → defaults` path. Test the
  happy path by patching `sys.modules["clm.infrastructure.config"]` with a `MagicMock`, and the
  fallback by setting `get_config.side_effect = RuntimeError`.
- **`zip create` CLI**: `find_output_directories` is the natural narrow seam — patch it to return
  a fixed list of `OutputDirectory` objects and let the command drive the archive creation. The
  archive path is predictable via `_archive_name(output_dir)`.
- **Language filter in `jupyterlite._find_site_dirs`**: looks for `/{lang}/` as a *posix path
  segment*, so fixtures must use real `/de/` directories (not `course-de`) to exercise the
  filter path. When no paths match, it falls back to returning the unfiltered list.

**Out of scope for PR 6** (still deferred — could be a future PR 7 if needed):
- `cli/commands/git_ops.py`, `cli/commands/summarize.py`, `cli/commands/workers.py` — larger; not
  required to hit 85%.
- `infrastructure/workers/pool_manager.py` — docker-marked branches dominate the gap per §6.
- `workers/notebook/notebook_processor.py` — already at 76% after earlier rounds; remaining gap
  is integration-shaped.
- `cli/commands/voiceover.py` sync merge/multi-part paths — would need full `merge_batch` and
  `build_parts` stubs; easier as integration tests.

**Test-infrastructure note (still open)**: `tests/cli/test_cli_unit.py` passes relative
`custom_cache.db` / `custom_jobs.db` paths to `--cache-db-path` / `--jobs-db-path`, causing
those SQLite files to leak into the caller's cwd. Wrap those invocations in a `tmp_path`
sandbox (or use `CliRunner().isolated_filesystem()`) when convenient — not urgent.

---

## 6. Non-goals

- **Textual TUI visual regression.** PR 3 writes diagnostic tests, not full visual snapshots.
  Don't introduce a snapshot library for this round.
- **Docker integration tests.** These are CI-only by design (per CLAUDE.md). No change.
- **Voiceover backends** (Whisper, HF, Mistral paths in `voiceover/transcribe.py`). Each backend
  has environment requirements; leave them `integration`-marked.
- **Worker pool docker branches** in `pool_manager.py`. Covered in docker-marked tests.
- **Deleting the `recordings/processing/` module.** It is still imported from `cli`, `workflow`,
  and `backends` (verified 2026-04-17). Do not remove.

---

## 7. Session-start checklist for picking up this work

**Round 2 is complete** (86.20% fast-suite coverage, target ≥ 85% met). This document can be
archived alongside the round-1 continuation guide once a follow-up session opens PR 7 or the
maintainer decides no further coverage work is planned.

If a **follow-up PR 7** is later needed (e.g. to push into the 88-90% range), the largest
remaining gaps in the fast suite as of PR 6 are:

- `cli/commands/voiceover.py` — `sync` command's multi-part / merge path (~149 miss).
- `cli/commands/git_ops.py`, `cli/commands/summarize.py`, `cli/commands/workers.py` — larger
  CLI surfaces not touched in PR 6.
- `workers/notebook/notebook_processor.py` — 76%; remaining gap is integration-shaped.

Session-start recipe:

1. Read this document plus §3 of `docs/claude/test-coverage-continuation-guide.md` for historical
   context on testing patterns (Console capture, `JobManager` stubs, `sys.modules` injection).
2. Regenerate the coverage baseline (command in §1) and compare against 86.20%. If it has
   regressed, investigate before starting new work.
3. Pick one or two high-miss files from the list above, apply the PR-6 patterns (§5.6), and land
   the tests in a focused PR.
4. Keep updating the continuation guide's phase table so the historical record stays complete.

### Separate follow-up: Monitor TUI bugs (PR 3 xfails)

PR 3 documented four Monitor TUI bugs as `xfail(strict=True)` tests. These are *not* coverage work
— they are real display bugs in production code. Fixing them requires separate design work
(especially bug #2, which needs a new `StatusInfo.current_course_spec` field). When the maintainer
chooses to address them, flipping the xfail → pass in the same commit is the easy part; the
upstream work is:

- **Bug #1 duration rounding**: change `data_provider.get_recent_events` to use
  `(strftime('%s', completed_at) - strftime('%s', started_at))` instead of `julianday()`
  arithmetic, *and* change `activity_panel._write_event` to test `is not None` instead of
  truthiness on `event.duration_seconds`.
- **Bug #1 stale "Started" entries**: dedup key currently combines `job_id` and `event_type`;
  needs to be `job_id` alone, with the latest event wins (or emit an explicit "remove" event
  on job completion).
- **Bug #2 empty title**: add `current_course_spec` to `StatusInfo` (populated from the
  corresponding CLI flag or from the in-progress job's payload) and render it in
  `status_header._render_content`.
- **Bug #3 scroll lag**: `WorkersPanel._render_workers` should update children in place
  (mutate existing Static widgets) rather than `remove_children()` + mount each tick.

## 8. File map

| PR | Test file | Source under test | Status |
|---|---|---|---|
| 1 | `tests/cli/test_config_command.py` | `cli/commands/config.py` | ✅ shipped |
| 1 | `tests/cli/test_db_commands.py` | `cli/commands/database.py` | ✅ shipped |
| 1 | `tests/core/test_multi_target_course.py` (extended) | `core/course.py` JupyterLite paths | ✅ shipped |
| 2 | `tests/cli/test_build_output.py` (extended) | `cli/output_formatter.py` (verbose) | ✅ shipped |
| 2 | `tests/infrastructure/backends/test_sqlite_backend_resilience.py` | `sqlite_backend.py` | ✅ shipped |
| 3 | `tests/mcp/test_server.py` | `mcp/server.py` | ✅ shipped |
| 3 | `tests/workers/jupyterlite/test_jupyterlite_worker.py` | `workers/jupyterlite/jupyterlite_worker.py` | ✅ shipped |
| 3 | `tests/cli/test_monitor_app.py` | `cli/monitor/app.py`, widgets, `data_provider.py` | ✅ shipped (+ 4 xfail bugs) |
| 4 | `tests/cli/test_build_command.py` | `cli/commands/build.py` | ✅ shipped |
| 4 | `tests/cli/test_docker_command.py` | `cli/commands/docker.py` | ✅ shipped |
| 4 | `tests/infrastructure/api/test_worker_routes_endpoints.py` | `worker_routes.py` endpoints + 500s | ✅ shipped |
| 4 | `tests/infrastructure/api/test_server.py` | `infrastructure/api/server.py` (WorkerApiServer) | ✅ shipped |
| 4 | `tests/infrastructure/api/test_client.py` | `infrastructure/api/client.py` | ✅ shipped |
| 4 | `tests/infrastructure/api/test_job_queue_adapter.py` | `infrastructure/api/job_queue_adapter.py` | ✅ shipped |
| 5 | `tests/recordings/test_processing_utils.py` | `recordings/processing/utils.py` | ✅ shipped |
| 5 | `tests/recordings/test_processing_pipeline.py` (extended) | `recordings/processing/pipeline.py` | ✅ shipped |
| 5 | `tests/recordings/test_processing_compare.py` | `recordings/processing/compare.py` | ✅ shipped |
| 6 | `tests/recordings/test_recordings_command.py` (new) | `cli/commands/recordings.py` | ✅ shipped |
| 6 | `tests/cli/test_voiceover_command.py` (new) | `cli/commands/voiceover.py` | ✅ shipped |
| 6 | `tests/cli/test_zip_ops.py` (extended) | `cli/commands/zip_ops.py` | ✅ shipped |
| 6 | `tests/cli/test_monitoring_command.py` (new) | `cli/commands/monitoring.py` | ✅ shipped |
| 6 | `tests/cli/test_polish_command.py` (new) | `cli/commands/polish.py` | ✅ shipped |
| 6 | `tests/cli/test_jupyterlite_command.py` (new) | `cli/commands/jupyterlite.py` | ✅ shipped |
| 6 | `tests/recordings/test_batch.py` (extended) | `recordings/processing/batch.py` | ✅ shipped |

Existing fixture files worth reusing:

- `tests/conftest.py` — top-level fixtures; respects the WorkerApiServer port-collision guidance
  in CLAUDE.md memory.
- `tests/mcp/test_tools.py::course_tree` — minimal bilingual course tree.
- `tests/recordings/test_web.py::app` — FastAPI TestClient + mocked OBS.
- `tests/core/test_multi_target_course.py` — multi-target + DummyBackend fixture.
- `tests/cli/test_build_output.py` — `Console(record=True)` pattern.
- **New (PR 4)** `tests/cli/test_docker_command.py::captured_console` — fixture for intercepting
  module-level Rich Console output; copy-paste for any future tests on modules that bind a
  console at import.
- **New (PR 4)** `tests/cli/test_build_command.py::_invoke_build` — helper that supplies
  `ctx.obj` so the `build` command can be driven without wiring up the parent `clm` group.
- **New (PR 4)** `tests/infrastructure/api/test_server.py::FakeUvicornServer` — fake uvicorn for
  lifecycle testing of `WorkerApiServer` without opening a real socket.
