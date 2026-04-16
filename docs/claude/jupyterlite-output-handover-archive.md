<!-- HANDOVER-ARCHIVE — companion to jupyterlite-output-handover.md -->

# Handover Archive: JupyterLite Output Target

> ⚠️ **RETIRED HANDOVER CONTENT — NOT ACTIVE**
>
> This document archives details of phases that have been completed and
> retired from the active handover. It must **not** be used with
> `/resume-feature`, `/implement-next-phase`, or similar commands that
> expect an active work plan — those commands should read the active
> handover document instead.
>
> **Active handover**: [jupyterlite-output-handover.md](./jupyterlite-output-handover.md)

---

## Retired on 2026-04-16 (Phase 2)

### Phase 2 — `JupyterLiteBuilder` worker [DONE]

Branch: `claude/jupyterlite-phase1` (not yet merged). Phase 2 landed on top
of Phase 1's commit — branch name is vestigial.

**Accomplished**: a course with at least one target listing
`<format>jupyterlite</format>` plus an effective `<jupyterlite>` config
produces a deployable static site under
`<target>/<course-dir>/<slides>/JupyterLite/<kind>/_output/index.html`.
`jupyter lite build` is driven by a dedicated queue-based worker;
existing courses that don't opt in build byte-identical output.

**Files added**:
- `src/clm/workers/jupyterlite/__init__.py` — package marker with
  opt-in docstring.
- `src/clm/workers/jupyterlite/lite_dir.py` — pure-Python assembler:
  `collect_notebook_tree`, `populate_files`, `populate_wheels`,
  `populate_environment`, `write_jupyter_lite_config`,
  `assemble_lite_dir`, `hash_manifest`, `sha256_of_file`. No dependency
  on `jupyterlite-core` so it runs in the fast test suite.
- `src/clm/workers/jupyterlite/builder.py` — `BuildArgs` /
  `BuildResult` dataclasses; `build_site()` drives a temp lite-dir →
  `jupyter lite build` subprocess → manifest write → Phase-2 stub
  `launch.py` emission. `_run_jupyter_lite_build` adds
  `--disable-addons` on the CLI for the kernel **not** selected (see
  gotcha below).
- `src/clm/workers/jupyterlite/jupyterlite_worker.py` —
  `JupyterLiteWorker(Worker)` plus `main()` entry point; mirrors the
  PlantUML worker's SQLite/API dual-mode pattern. Lazy-imports the
  builder module so the worker class is safe to construct without the
  `[jupyterlite]` extra installed.
- `src/clm/workers/jupyterlite/__main__.py` — enables
  `python -m clm.workers.jupyterlite`.
- `src/clm/core/operations/build_jupyterlite_site.py` —
  `BuildJupyterLiteSiteOperation` (service_name=`jupyterlite-builder`).
  `payload()` walks the notebook tree, resolves wheel + environment
  paths relative to the course root, and builds a deterministic cache
  manifest. Dispatches via `backend.execute_operation(self, payload)`.
- `src/clm/infrastructure/messaging/jupyterlite_classes.py` —
  `JupyterLitePayload` and `JupyterLiteResult` Pydantic models.
  `content_hash()` mixes in kernel + `jupyterlite-core` version +
  `emit_launcher` so a builder upgrade invalidates the cache.
- `tests/workers/jupyterlite/{test_lite_dir,test_builder,test_registration,test_enable_workers,test_jupyterlite_integration}.py`
  — 33 new tests covering lite-dir assembly, the builder with a fake
  subprocess, registration invariants, the CLI auto-enable helper, and
  an integration test (marked `integration`, skipped when
  `jupyterlite-core` is missing) that runs the real CLI end-to-end.
- `tests/core/operations/test_build_jupyterlite_site.py` —
  operation/payload unit tests including an `AsyncMock` dispatch
  assertion.

**Files modified**:
- `pyproject.toml` — new `[jupyterlite]` extra pinning
  `jupyterlite-core>=0.7,<0.9`, `jupyterlite-pyodide-kernel>=0.7,<0.9`,
  `jupyterlite-xeus>=4.0,<5.0`, and `jupyter-server>=2.12` (the last
  is load-bearing; see gotcha below). Added to both `[all]` and the
  PEP-735 `dev` group.
- `src/clm/core/course.py` — removed the Phase-1 "not yet
  implemented" warning; added `Course.process_jupyterlite_for_targets`
  called from `process_all` after the stage loop and dir-group step.
  Emits one barrier-scheduled job per `(target, language, kind)`
  tuple, using the same `submit → wait_for_completion` pattern as
  `process_stage_for_target`.
- `src/clm/core/utils/text_utils.py` — added
  `"jupyterlite": Text(de="JupyterLite", en="JupyterLite")` so
  `OutputSpec` path construction works for the new format. Iterator
  `output_specs()` was deliberately **not** extended with a fourth
  branch — JupyterLite emits no per-file `OutputSpec`.
- `src/clm/cli/commands/build.py` — new
  `enable_jupyterlite_workers_if_needed(course, worker_config)` flips
  `worker_config.jupyterlite.count = 1` when any target requests the
  format, unless the operator already set a higher count.
- `src/clm/infrastructure/backends/sqlite_backend.py` —
  `"jupyterlite-builder": "jupyterlite"` added to
  `service_to_job_type`; `_get_output_metadata` handles the new job
  type; `wait_for_completion`'s result-caching switch has a
  `jupyterlite` branch that skips DB caching (the authoritative cache
  is `job_queue.add_to_cache` plus the site's `jupyterlite-manifest.json`
  on disk).
- `src/clm/infrastructure/config.py` —
  `WorkersManagementConfig.jupyterlite: WorkerTypeConfig` (default
  `count=None`, opt-in). `get_worker_config` accepts `"jupyterlite"`;
  `get_all_worker_configs` includes it **only** when count > 0 (the
  opt-in contract). No default Docker image — Docker mode is not
  supported in Phase 2.
- `src/clm/infrastructure/workers/config_loader.py` — added
  `"jupyterlite"` to the per-type CLI-override loop so
  `--jupyterlite-workers N` and equivalent env vars work the same way
  as for other types.
- `src/clm/infrastructure/workers/lifecycle_manager.py` —
  `should_start_workers` conditionally adds `"jupyterlite"` to the
  required-types list (only when count > 0).
- `src/clm/infrastructure/workers/worker_executor.py` — added
  `"jupyterlite": "clm.workers.jupyterlite"` to
  `DirectWorkerExecutor.MODULE_MAP`.
- `tests/infrastructure/workers/test_lifecycle_manager.py` — the
  mock `WorkersManagementConfig` fixture gained `jupyterlite.count =
  None` so `should_start_workers` doesn't trip on the new attribute.
- `.gitignore` — ignore `.jupyterlite.doit.db` (doit state cache that
  `jupyter lite build` drops in CWD).

**Acceptance** (met):
- Fast suite: 3237 tests pass (+33 from Phase 1 baseline of 3204).
- Integration test `test_jupyter_lite_build_produces_index_html`
  produces `index.html` via the real `jupyter lite build`. Skipped
  when `jupyterlite-core` is not importable; runs under
  `pytest -m integration`.
- `ruff check` / `mypy` clean on 232 source files.

**Implementation notes and gotchas from the session** (Phase 3+ readers):

- **Double-addon deadlock** (load-bearing): installing `[jupyterlite]`
  pulls in both `jupyterlite-xeus` and `jupyterlite-pyodide-kernel`,
  and each one's `post_build` hook raises when its kernel isn't
  active (xeus demands a conda env file; pyodide-kernel demands a
  wheelhouse). `_run_jupyter_lite_build` passes
  `--disable-addons <inactive-kernel-addon>` on the CLI to suppress
  the non-chosen addon. Config-level disabling via
  `LiteBuildConfig.disable_addons` in `jupyter_lite_config.json` does
  **not** reliably prevent `post_build` from firing on 0.7.x — don't
  switch to that path without verifying behavior on the bumped
  version.
- **`jupyter-server` is a hidden dependency**: `jupyterlite-core`'s
  `contents` addon raises "jupyter-server is not installed" without
  it. Pinned in the `[jupyterlite]` extra.
- **Opt-in worker wiring**: jupyterlite is the first worker type that
  is not auto-started. Three places enforce this: (1)
  `get_all_worker_configs` only appends it when count > 0, (2)
  `should_start_workers` mirrors the same guard, (3)
  `enable_jupyterlite_workers_if_needed` in the `clm build` flow is
  what flips count to 1 per course. Any change to one must be
  mirrored in the others.
- **Cache layering**: the default `execute_operation` DB cache flow
  assumes one output file per job; JupyterLite's "output" is a tree.
  The new branch in `wait_for_completion` just skips DB caching and
  relies on `job_queue.add_to_cache(output_file, content_hash, ...)`
  (the existence-of-`index.html` check in `execute_operation` picks
  this up on re-run). If Phase 3 wants proper cache invalidation
  across runs, hash-compare against the on-disk
  `jupyterlite-manifest.json`.
- **`ProcessNotebookOperation` pattern adopted**: the operation file
  mirrors `process_notebook.py` — `@frozen` attrs, async `execute`,
  `payload()` method that computes content hashes eagerly, and
  `service_name` property. Keep this pattern for Phase 3 additions.
- **Output path**: `<target.output_root>/<course-dir-name>/<Slides>/JupyterLite/<Kind>`
  (parallel to the existing `Notebooks/<Kind>` layout). Not the
  hypothetical `<language>/jupyterlite/` structure from design §4.5 —
  CLM's path convention embeds language in `course-dir-name`.

### Retired Status Snapshot (Phase 2)

- End-to-end build working with `pyodide` kernel on an empty notebook
  tree. Kernel executes cells in the browser; verified manually via
  the Phase-2 stub `launch.py`.
- 3237 fast-suite tests pass.
- Branch `claude/jupyterlite-phase1` holds the full diff; not yet
  merged to master. User chose to continue on the existing branch
  rather than cut a new `phase2` branch.

### Retired Session Notes (Phase 2)

- `jupyterlite-core` 0.7.4 resolved via `uv sync` under the 14-day
  exclude-newer window. No need to revisit the pin yet.
- The `--disable-addons` CLI flag is preferred over the config-file
  field because the config-file path was silently ineffective in
  0.7.x (`post_build` still fired). If 0.8+ stabilizes that, the
  config-only path is cleaner.
- User preference confirmed: stay on `claude/jupyterlite-phase1`,
  don't re-verify PyPI for `jupyterlite-core` range, full Phase 2 in
  one branch.

---

## Retired on 2026-04-16

### Phase 1 — Spec plumbing and validation [DONE]

Commit: `4e19ca3` on branch `claude/jupyterlite-phase1`.

**Accomplished**: JupyterLite is recognized by the spec parser and validator,
but produces no output yet. Existing courses build byte-identical artifacts.

**Files**:
- `src/clm/core/course_spec.py` — add `"jupyterlite"` to `VALID_FORMATS`
  (line 272); add `JupyterLiteConfig` dataclass; parse optional
  `<jupyterlite>` child on `<course>` root **and** on each
  `<output-target>`.
- `src/clm/core/output_target.py` — change `OutputTarget.from_spec()` so
  `formats=None` expands to `{"html", "notebook", "code"}` explicitly,
  **not** `VALID_FORMATS`. This is the opt-in gate. Also add
  `effective_jupyterlite_config()` returning target-level if set, else
  course-level (wholesale replacement, not field-merge).
- `src/clm/core/course.py` (or wherever course-level validation lives) —
  cross-validate: target with `jupyterlite` format ⇒
  `effective_jupyterlite_config()` must not be `None`.
- `src/clm/cli/info_topics/jupyterlite.md` — new info topic.
- `src/clm/cli/info_topics/spec-files.md` — document the new format and the
  `<jupyterlite>` block.
- `tests/core/` — regression test pinning the default format set to
  `{"html", "notebook", "code"}`; validation tests for the cross-check.

**Acceptance** (met): all existing tests green; new tests green; a
hand-crafted spec with `<jupyterlite>` + a target requesting the format
passes validation but emits a "not yet implemented" stub on build.

**Implementation notes from the session** (useful context for Phase 2):

- `ALL_FORMATS` was renamed to **`DEFAULT_FORMATS`** (literal frozenset) and
  decoupled from `VALID_FORMATS`. Callers in `src/clm/core/output_target.py`,
  `tests/core/test_output_target.py`, and
  `tests/core/test_multi_target_course.py` were updated. The test at
  `tests/core/test_output_target.py:TestOutputTargetConstants` pins
  `DEFAULT_FORMATS == {html, notebook, code}` and also asserts
  `DEFAULT_FORMATS < VALID_FORMATS` with `jupyterlite` in the difference —
  this is the load-bearing regression test for the opt-in gate.
- `JupyterLiteConfig.from_element()` validates `<kernel>` (required; must be
  `xeus-python` or `pyodide`) and `<app-archive>` (must be `offline` or
  `cdn`). Empty/missing `<launcher>` defaults to `True`.
- `OutputTarget` gained two fields (`jupyterlite`, `course_jupyterlite`) and
  a new method `effective_jupyterlite_config()`. `with_cli_filters()` and
  `from_spec()` propagate both through. `Course.from_spec` passes
  `spec.jupyterlite` as `course_jupyterlite` when constructing targets.
- Cross-validation lives in `CourseSpec.validate()` right after the existing
  duplicate-name/path check. Error message points users at
  `clm info jupyterlite`.
- Phase-1 "stub worker dispatch": `output_specs` in
  `src/clm/infrastructure/utils/path_utils.py` enumerates formats via an
  explicit whitelist (`if "html" in effective_formats`, etc.), so
  `jupyterlite` falls through silently without producing any `OutputSpec`.
  The only visible change is a `logger.warning` in `Course.from_spec` noting
  that the site builder is "not yet implemented (tracked for Phase 2)".
- `TOPICS` dict in `src/clm/cli/commands/info.py` now has four entries;
  `tests/cli/test_info.py::test_topics_registry_complete` pins the count.
- **Don't reintroduce `ALL_FORMATS`** — it's gone by design.

### Retired Status Snapshot

- Design investigation (CLM format architecture, JupyterLite capabilities) —
  complete.
- Design doc written and approved: `docs/claude/design/jupyterlite-output.md`.
- Opt-in model specified: two-gate (course-level config block + explicit
  per-target format listing) with `formats=None` default tightened.
- Tests: 3204 passing (18 new Phase-1 tests).

### Retired Session Notes

None — all session notes from the original handover remained relevant to
Phase 2+ and were preserved in the active handover.
