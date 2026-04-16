# Handover: JupyterLite Output Target

## 1. Feature Overview

Adds a fourth output format to CLM — **`jupyterlite`** — producing a
deployable JupyterLite static site from the notebook-format output that CLM
already builds. Students can run course notebooks in the browser via a hosted
site (GitHub Pages / nginx / CDN) or locally via a bundled Python launcher
that serves the site on `http://localhost:<port>`. The feature is **strictly
opt-in**: existing courses continue to build byte-identical output with no
new dependencies installed.

**Status**: Phases 1–2 complete on branch `claude/jupyterlite-phase1`.
Phase 3 is next.

**Design doc**: `docs/claude/design/jupyterlite-output.md` (authoritative;
this handover references it but does not duplicate it).

**Archive**: Detailed descriptions of completed phases have been moved to
[jupyterlite-output-handover-archive.md](./jupyterlite-output-handover-archive.md).

**User constraint that shaped the design**: *"jupyter-lite format is opt-in
only, so that existing courses are built without jupyterlite support unless
it is explicitly enabled."*

**Scope caps agreed in v1**:
- Python notebook targets only. No C++/C#/Java/TypeScript JupyterLite output.
- Default kernel recommendation: **xeus-python with preinstalled wheels**
  (reproducible, no runtime network required). Pyodide also supported.
- No COOP/COEP tuning — service-worker comms fallback is accepted.
- No multi-target site merging — each opted-in target builds its own site.

## 2. Design Decisions

### Treat JupyterLite as a site-bundler, not a per-file format

JupyterLite is a **site-level** artifact — it consumes a tree of `.ipynb`
files and produces one deployable static site. Modeling it as a peer of
`html`/`notebook`/`code` at the `OutputSpec` cell-filtering level would
misrepresent the tool.

**Chosen**: a new worker `JupyterLiteBuilder` whose operation runs **once per
`(output_target, language, kind)`**, consuming the already-built notebook
tree. No new `OutputSpec` subclass. Existing `CompletedOutput` /
`CodeAlongOutput` / `SpeakerOutput` rules are reused unchanged.

**Rejected**: extending `NotebookProcessor._create_using_nbconvert` with a
JupyterLite branch. That would duplicate the site-level aggregation logic
into per-file work and couple it to `nbconvert`, which JupyterLite does not
use.

### Two-gate opt-in (load-bearing)

Both gates required; either absent ⇒ build behaves identically to today.

**Gate 1** — a `<jupyterlite>` config block declaring kernel and preinstalled
wheels, present at **either** course level (as a child of `<course>`) **or**
target level (as a child of `<output-target>`). Target-level **replaces**
course-level wholesale (not field-merge) for that one target. If any target
requests `jupyterlite` and neither level provides config, validation **fails**
with a pointer to `clm info jupyterlite`.

Why two levels: the typical course wants JupyterLite for *shared* and
*trainer* targets but **not** for *speaker* targets, and may also want a
different wheel set for trainer vs. student. Per-target override supports
both cases cleanly — speaker targets simply don't list the format; trainer
targets can override the wheel list.

**Gate 2** — explicit `<format>jupyterlite</format>` per target. Critically,
the `formats=None` default in `OutputTarget` is **redefined** from "all of
`VALID_FORMATS`" to a literal `{"html", "notebook", "code"}`. This is the
single point where a backwards-compat trap could bite — a regression test in
Phase 1 pins the default format set so any future change is deliberate.

**Rejected**: a boolean `enable-jupyterlite` attribute on `<course>` without
per-target listing. Too coarse — a user may want to publish only `completed`
notebooks as JupyterLite while keeping `speaker` HTML-only.

**Rejected**: field-wise merge of target-level over course-level. Harder to
reason about than wholesale replacement and the override is rare enough that
copying the full block is acceptable.

### `[jupyterlite]` optional extra, included in `[all]`

`jupyterlite-core`, `jupyterlite-pyodide-kernel`, `jupyterlite-xeus` live
behind a new `[jupyterlite]` extra. The extra is **also added to `[all]`**
(matching `[voiceover]`, `[recordings]`, etc.) so a developer running
`pip install -e ".[all]"` gets everything. The build-time opt-in gates are
independent of install-time dependencies — installing the package does not
cause any course to produce JupyterLite output. A build that reaches a
JupyterLite operation without the extra installed fails with the standard
CLM "missing extra" message.

### Student launcher as plain Python, not bundled binary

`launch.py` is a ~60-line Python file that starts
`ThreadingHTTPServer` with a `SimpleHTTPRequestHandler` subclass that
forces `application/wasm` for `.wasm` (Windows Python guesses wrong
otherwise), picks a free port, and opens the browser.

**Why not ship a `miniserve` binary per OS?** The audience is programming
students — they have Python. Zero bytes of binary bloat and zero
cross-platform packaging headache for v1. Left as a future
`<launcher>miniserve</launcher>` option in the spec.

### Barrier scheduling over job-level `depends_on`

JupyterLite jobs depend on the notebook jobs for the same
`(target, language, kind)`. Chose to express this in the **build coordinator**
(planner emits JupyterLite job only after the notebook jobs finish) rather
than adding a `depends_on` column to the `jobs` table. Smaller surface area,
matches existing per-file → per-site flow patterns.

## 3. Phase Breakdown

### Phase 1 — Spec plumbing and validation [DONE]

`VALID_FORMATS` gained `jupyterlite`; `ALL_FORMATS` was renamed to
`DEFAULT_FORMATS` and made a literal three-set, decoupled from
`VALID_FORMATS`. `JupyterLiteConfig` is parsed at course and target level
with wholesale-replacement precedence. Cross-validation fails when a target
requests `jupyterlite` without an effective config. `clm info jupyterlite`
published. Stub dispatch logs "not yet implemented". 3204 tests pass.
Full details: [archive](./jupyterlite-output-handover-archive.md).

### Phase 2 — `JupyterLiteBuilder` worker [DONE]

`[jupyterlite]` extra published (adds `jupyterlite-core`, both kernel
addons, and `jupyter-server`). New queue-based worker at
`src/clm/workers/jupyterlite/` drives `jupyter lite build` via the
`BuildJupyterLiteSiteOperation` (service_name
`jupyterlite-builder`) with full SQLite + API dispatch wiring.
`Course.process_jupyterlite_for_targets` runs after the stage loop,
emitting one barrier-scheduled job per `(target, language, kind)`
tuple. A CLI helper (`enable_jupyterlite_workers_if_needed`) flips
`worker_config.jupyterlite.count` to 1 when a course opts in so the
lifecycle manager auto-starts the worker. 3237 fast-suite tests pass
(+33); integration test produces a real site end-to-end. Phase 1 stub
warning removed. Full details and gotchas:
[archive](./jupyterlite-output-handover-archive.md).

### Phase 3 — Launcher, branding, polish [TODO] ← **next**

- `launch.py` emitter (wasm MIME fix).
- `README-offline.md` emitter (IndexedDB persistence caveat documented).
- Optional `<branding>` block → `overrides.json` (theme, logos).
- Playwright headless integration test (Linux CI).
- `clm jupyterlite preview <target>` CLI convenience command.

**Acceptance**: student can unzip directory, run `python launch.py`, use
notebooks. CI smoke test green.

### Phase 4 — Documentation and release [TODO]

- `docs/user-guide/jupyterlite.md` — user guide.
- `docs/developer-guide/architecture.md` — add `JupyterLiteBuilder` section.
- `CHANGELOG.md` entry.
- Version bump. `pytest -m "not docker"` green + CI green on tag, per
  release rules.

## 4. Current Status

**Completed (through Phase 2)**:
- Phase 1 — spec plumbing and validation (commit `4e19ca3`).
- Phase 2 — `JupyterLiteBuilder` worker, build-planner wiring, CLI
  auto-enable, `[jupyterlite]` extra. Full details:
  [archive](./jupyterlite-output-handover-archive.md).
- 3237 fast-suite tests pass (+33 over Phase 1). Integration test
  (`tests/workers/jupyterlite/test_jupyterlite_integration.py`,
  marked `integration`) verifies a real `jupyter lite build` produces
  `_output/index.html`.

**In progress**: nothing — awaiting kickoff of Phase 3.

**Branch**: `claude/jupyterlite-phase1` holds both phases' work. Not
yet merged to master. Commits from Phase 2 are still unstaged at
handover time — see `git status` on the branch.

**Blockers**: none.

**Open questions for Phase 3**:
- Whether to ship a prebuilt `miniserve` binary per OS as an
  alternative to `launch.py`. Design doc §4.4 leaves room for it;
  decision can wait until Playwright smoke test proves the Python
  launcher is sufficient.
- Whether to share one Pyodide runtime across `(kind)` variants via
  symlinks to shrink disk footprint. Deferred to v2 per design §6.
- Whether Phase 3's Playwright integration should run cross-browser
  or just Chromium. Linux CI only either way.

**Resolved by user (2026-04-16)**:
- `<jupyterlite>` placement: both course-level and per-target, with
  per-target overriding wholesale.
- `[jupyterlite]` included in `[all]`.
- Phase 2 lived on `claude/jupyterlite-phase1` alongside Phase 1.
- Skip PyPI re-verify; full Phase 2 in one branch.

## 5. Next Steps

**Start Phase 3.** No prerequisites — the `[jupyterlite]` extra is
already installed in the dev env, and `jupyter lite build` is on
`PATH` via `uv run`.

Implementation order within Phase 3 (mirrors design §5):

1. `src/clm/workers/jupyterlite/builder.py::_emit_launcher` — replace
   the Phase 2 stub with a full `launch.py` that (a) picks a free
   port via `ThreadingHTTPServer`, (b) subclasses
   `SimpleHTTPRequestHandler` to force `application/wasm` for `.wasm`
   (Windows `mimetypes` guesses wrong), (c) opens the browser at
   `/lab/index.html`, (d) runs until Ctrl+C.
2. `README-offline.md` emitter — explains IndexedDB persistence, how
   to clear site data, that edits don't survive clearing, and how to
   deploy the directory on a LAN or USB stick.
3. Optional `<branding>` block in `<jupyterlite>` — parse in
   `JupyterLiteConfig.from_element`; map to `overrides.json`
   (`@jupyterlab/apputils-extension:themes` for light/dark and
   `@jupyterlab/jupyterlab-extension:metadata` for a logo). Add
   `TEXT_MAPPINGS` entries only if the dir structure needs
   bilingual naming (probably not).
4. Playwright smoke test — marked `@pytest.mark.e2e`, Linux CI only.
   Loads `_output/index.html`, waits for the kernel to spin up,
   evaluates one cell, asserts the output text. Skip on Windows dev
   boxes.
5. `clm jupyterlite preview <target>` CLI convenience — wraps
   launching the most recently built site. Place under
   `src/clm/cli/commands/jupyterlite.py` following the pattern of
   `clm voiceover`.

**Gotchas carried forward from Phase 2** (see archive for the full
list):

- **Don't re-enable both kernel addons**: `jupyter lite build --disable-addons`
  is load-bearing; see `builder._run_jupyter_lite_build`.
- **Phase-2 stub launcher** in `builder._emit_launcher` is the file
  Phase 3 replaces. The function already writes to
  `<output_dir>/launch.py` — just expand the body; don't wire a new
  emission point.
- **`jupyter-server`** is a required `[jupyterlite]` dep, not optional.
- **Opt-in worker wiring**: three-place guard (`get_all_worker_configs`,
  `should_start_workers`, `enable_jupyterlite_workers_if_needed`). A
  change to one must be mirrored in the others.
- `DEFAULT_FORMATS` (literal `frozenset({"html","notebook","code"})`)
  is the source of truth for the opt-in gate. Don't derive it from
  `VALID_FORMATS`.
- `clm info` reads from `src/clm/cli/info_topics/*.md` with `{version}`
  placeholders. Do not hardcode the version.
- Pre-commit hook runs ruff + mypy + fast tests. If it fails, fix and
  create a **new** commit — never `--amend` a rejected commit.
- **uv.lock regeneration gotcha** (`feedback_precommit_uvlock_regen.md`):
  when pre-commit keeps failing with "files were modified by this hook"
  but `uv run ruff check --fix` passes, commit `uv.lock` first.

## 6. Key Files & Architecture

**Already created in Phase 2** (extend or modify for Phase 3):
- `src/clm/workers/jupyterlite/__init__.py`
- `src/clm/workers/jupyterlite/lite_dir.py` — lite-dir assembler.
- `src/clm/workers/jupyterlite/builder.py` — `BuildArgs`,
  `BuildResult`, `build_site`, `_run_jupyter_lite_build`,
  `_emit_launcher` (Phase-2 stub; replace in Phase 3).
- `src/clm/workers/jupyterlite/jupyterlite_worker.py` — queue worker.
- `src/clm/workers/jupyterlite/__main__.py`
- `src/clm/core/operations/build_jupyterlite_site.py` —
  `BuildJupyterLiteSiteOperation`.
- `src/clm/infrastructure/messaging/jupyterlite_classes.py` —
  `JupyterLitePayload`, `JupyterLiteResult`.
- `src/clm/cli/info_topics/jupyterlite.md` (Phase 1).
- `tests/workers/jupyterlite/` — 5 test modules.
- `tests/core/operations/test_build_jupyterlite_site.py`.

**Will be created in Phase 3**:
- `docs/user-guide/jupyterlite.md` — user-facing guide (Phase 4
  finalizes, but a first draft can land here).
- `src/clm/cli/commands/jupyterlite.py` — `clm jupyterlite preview`.
- `tests/workers/jupyterlite/test_launcher.py` — unit tests for the
  full `launch.py` emitter (wasm MIME, port selection on-free-port,
  handler subclass).
- `tests/workers/jupyterlite/test_playwright.py` — e2e smoke
  (marked `@pytest.mark.e2e`, Linux-only).

**Entry points and connections** (current):
- Spec XML → `OutputTargetSpec.from_element()` →
  `OutputTarget.from_spec()` → `Course.process_all` runs the stage
  loop → `Course.process_jupyterlite_for_targets` emits one
  `BuildJupyterLiteSiteOperation` per `(target, language, kind)` →
  `backend.execute_operation` queues a job with
  `service_name="jupyterlite-builder"` →
  `enable_jupyterlite_workers_if_needed` (in `clm build`) has already
  flipped `worker_config.jupyterlite.count=1` so the lifecycle
  manager has a worker ready → `JupyterLiteWorker.process_job`
  dequeues, calls `build_site()`, which assembles lite-dir, shells
  out to `jupyter lite build --disable-addons <inactive-kernel>`,
  writes `_output/` + Phase-2 stub `launch.py` + manifest at
  `<target.output_root>/<course-dir>/<Slides>/JupyterLite/<Kind>/`.

**Patterns to continue**:
- `service_name` property on operations as the dispatch key (existing
  pattern — `"notebook-processor"` etc.).
- `attrs @define` / `@frozen` for internal dataclasses; Pydantic only at
  worker/CLI boundary.
- Python over bash for scripts (Windows-first project; per memory).
- `logging.getLogger(__name__)` — no `print()` in library code.
- Optional extras pattern for heavy deps (cf. `[voiceover]`, `[recordings]`).
- Lazy imports in CI-run code paths so core install doesn't need the extra.

## 7. Testing Approach

**Phase 1** — pure unit tests in `tests/core/`:
- Regression test pinning default format set.
- Parse tests for `<jupyterlite>` at course level (present / absent / malformed).
- Parse tests for `<jupyterlite>` at target level (present / absent / malformed).
- `effective_jupyterlite_config()` precedence tests: course-only,
  target-only, both (target wins), neither.
- Cross-validation tests: target requests `jupyterlite` with no effective
  config ⇒ error; with effective config ⇒ passes.
- Target-spec tests for explicit `<format>jupyterlite</format>` listing.

**Phase 2** — unit + integration in `tests/workers/jupyterlite/`:
- Unit: lite-dir assembler produces expected layout given fixtures.
- Integration (marked `@pytest.mark.integration`, excluded from fast suite):
  end-to-end build of a minimal course with `[jupyterlite]` extra installed.
  Skipped if `jupyter lite` not on PATH.

**Phase 3** — Playwright smoke test (marked `@pytest.mark.e2e`, Linux CI
only): loads `_output/index.html` headlessly, executes a cell, asserts
output.

**How to run**:
- Fast suite (pre-commit, default): `pytest`
- Pre-release: `pytest -m "not docker"`
- Everything: `pytest -m ""`
- JupyterLite-only while developing: `pytest tests/workers/jupyterlite/ -v`

**Coverage target**: Phases 1–2 landed with the full fast suite green
(3237 passing). Phase 3 must keep that number steady or growing;
launcher/Playwright tests add coverage, they don't replace existing
tests.

## 8. Session Notes

- The user confirmed v1 scope constraints (Python only, xeus-python default,
  shared Pyodide deferred, no COOP/COEP) and added the opt-in requirement
  explicitly. Design doc reflects both.
- **Opt-in is the dominant design constraint.** Every architectural
  decision in Phases 1–2 was evaluated against "does this change the
  output of any course that doesn't opt in?" Phase 3 must keep that bar.
- CLM is a Windows-first project. The Phase-2 stub launcher does **not**
  yet include the `.wasm` MIME fix; Phase 3's full `launch.py` must add
  it and be tested on Windows specifically — Python's `mimetypes` on
  Windows reads from the registry and may guess `.wasm` wrong.
- User prefers Python over bash for tooling wrappers (confirmed memory).
  The launcher and any build helpers should be `.py` files, not `.sh`.
- `jupyterlite-core` 0.7.4 resolved in Phase 2. No need to revisit the
  pin until Phase 3 is done.
- Phase 2 discovered that `jupyterlite-xeus` and
  `jupyterlite-pyodide-kernel` both run `post_build` hooks regardless
  of which kernel the course picked, and the CLI `--disable-addons`
  flag is the only reliable way to silence the idle one. If 0.8+ fixes
  the config-file path, revisit `builder._run_jupyter_lite_build`.
