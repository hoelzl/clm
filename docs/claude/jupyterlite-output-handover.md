# Handover: JupyterLite Output Target

## 1. Feature Overview

Adds a fourth output format to CLM — **`jupyterlite`** — producing a
deployable JupyterLite static site from the notebook-format output that CLM
already builds. Students can run course notebooks in the browser via a hosted
site (GitHub Pages / nginx / CDN) or locally via a bundled Python launcher
that serves the site on `http://localhost:<port>`. The feature is **strictly
opt-in**: existing courses continue to build byte-identical output with no
new dependencies installed.

**Status**: Phase 1 complete (commit `4e19ca3`). Phase 2 is next.

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

### Phase 2 — `JupyterLiteBuilder` worker [TODO] ← **next**

**Accomplishes**: end-to-end build. A minimal course with `jupyterlite`
enabled produces `_output/index.html` that loads in a browser and runs
`print("hello")`.

**Files**:
- `pyproject.toml` — new `[jupyterlite]` optional extra pinning
  `jupyterlite-core>=0.7,<0.9` + kernels. **Also add `jupyterlite` to the
  `[all]` extra** alongside `voiceover`, `recordings`, etc.
- `src/clm/workers/jupyterlite/__init__.py` — worker package.
- `src/clm/workers/jupyterlite/builder.py` — `BuildJupyterLiteSiteOperation`
  + `BuildJupyterLiteSitePayload`. `service_name = "jupyterlite-builder"`.
- `src/clm/workers/jupyterlite/lite_dir.py` — assembles the temporary
  `lite-dir/` (files, pypi, jupyter_lite_config.json, overrides.json).
- Worker registry — register new `job_type`.
- Build planner — emit JupyterLite jobs after notebook jobs per
  `(target, language, kind)` barrier.
- Cache layer — key on notebook-tree hash + wheel-set hash + kernel +
  jupyterlite-core version.

**Acceptance**: minimal course + `pip install -e ".[jupyterlite]"` produces
a site that loads in Chrome and executes one cell.

### Phase 3 — Launcher, branding, polish [TODO]

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

**Completed (through Phase 1)**:
- Design doc written and approved: `docs/claude/design/jupyterlite-output.md`.
- Spec plumbing, validation, info topic, stub dispatch — commit `4e19ca3`
  on branch `claude/jupyterlite-phase1`.
- 3204 fast-suite tests pass on the branch.

**In progress**: nothing — awaiting kickoff of Phase 2.

**Branch**: `claude/jupyterlite-phase1` exists and holds the Phase 1 work.
Not yet merged to master. Phase 2 can continue on the same branch (extending
it into a `jupyterlite` feature branch) or branch off master after merge —
user's call at kickoff.

**Blockers**: none.

**Open questions for Phase 2**:
- Exact `jupyterlite-core` version range — design pins `>=0.7,<0.9` but 0.8
  is still alpha. Re-check at the end of Phase 2 against the live PyPI
  release list.
- Whether to share one Pyodide runtime across `(kind)` variants via symlinks
  to shrink disk footprint. Deferred to v2 per design §6.
- Which `output_specs` mechanism to use for enqueuing JupyterLite jobs —
  Phase 1 left `jupyterlite` as a silent no-op in the format whitelist in
  `src/clm/infrastructure/utils/path_utils.py::output_specs` (lines
  277–284). Phase 2 adds a site-level dispatch **after** the per-file
  operations, not by adding a branch here. See design §4.1–§4.3 for the
  barrier scheduling recommendation.

**Resolved by user (2026-04-16)**:
- `<jupyterlite>` placement: **both** course-level and per-target, with
  per-target overriding wholesale. Typical pattern — course-level default,
  speaker target omits the format, trainer target may override wheel list.
- `[jupyterlite]` included in `[all]` for developer convenience. Build-time
  opt-in gates remain independent of install-time dependencies.

**Tests (Phase 1)**: 18 new tests added across
`tests/core/test_output_target.py` (parse precedence, wholesale replace,
`with_cli_filters` pass-through, opt-in regression pins) and
`tests/core/course_spec_test.py` (parse + cross-validation permutations).
See archive for details.

## 5. Next Steps

**Start Phase 2.** Prerequisites:

1. Confirm branch strategy (continue on `claude/jupyterlite-phase1` vs. new
   branch off master after merge).
2. Re-verify `jupyterlite-core` version availability on PyPI before pinning
   in `pyproject.toml` — the research sub-agent at design time could not
   directly fetch ReadTheDocs and the notes are only as fresh as 2026-04-15.
3. Install `jupyterlite-core` and `jupyter-lite-xeus` locally so the
   integration test in Phase 2 can shell out to `jupyter lite build`.

Implementation order within Phase 2 (mirrors design §5):

1. `pyproject.toml` — add `[jupyterlite]` extra pinning
   `jupyterlite-core>=0.7,<0.9`, `jupyterlite-pyodide-kernel`, and
   `jupyterlite-xeus`. Add `jupyterlite` to the `[all]` extra alongside
   `voiceover`, `recordings`, etc. (matches the user's 2026-04-16
   resolution.)
2. `src/clm/workers/jupyterlite/__init__.py` — worker package.
3. `src/clm/workers/jupyterlite/lite_dir.py` — unit-testable assembler:
   given a notebook tree, wheel list, kernel, and env-yml, produce a
   `lite-dir/` layout (`files/`, `pypi/`, `jupyter_lite_config.json`,
   optional `environment.yml`, optional `overrides.json`).
4. `src/clm/workers/jupyterlite/builder.py` —
   `BuildJupyterLiteSiteOperation` + `BuildJupyterLiteSitePayload`.
   `service_name = "jupyterlite-builder"`. Operation shells out to
   `jupyter lite build` and writes a build manifest for cache keying.
5. Worker registry — register the new `job_type`. Pattern to follow:
   `service_to_job_type` in `src/clm/infrastructure/backends/sqlite_backend.py`.
6. Build planner — emit the barrier-scheduled JupyterLite job per
   `(target, language, kind)` after the notebook jobs for that tuple
   complete. Design §4.3 recommends option (A): coordinate in the planner,
   do not add `depends_on` to the `jobs` table.
7. Cache layer — key on notebook-tree hash + wheel-set hash + kernel +
   `jupyterlite-core` version so rebuilds are skipped when nothing changed.
8. Drop the Phase 1 "not yet implemented" warning in `Course.from_spec` —
   the real builder now runs.

**Gotchas carried forward from Phase 1**:

- `DEFAULT_FORMATS` (literal `frozenset({"html","notebook","code"})`) is the
  source of truth for the opt-in gate. Do **not** derive it from
  `VALID_FORMATS` and do **not** resurrect the old `ALL_FORMATS` name.
- `clm info` reads from `src/clm/cli/info_topics/*.md` with `{version}`
  placeholders. Do not hardcode the version.
- `output_specs` in `src/clm/infrastructure/utils/path_utils.py` lines
  277–284 has a three-branch format whitelist (`html`, `notebook`, `code`).
  Phase 2 should **not** add a `jupyterlite` branch there — JupyterLite is
  a site-level bundler and emits no per-file `OutputSpec`. Dispatch happens
  at the build-planner level instead.
- Pre-commit hook runs ruff + mypy + fast tests. If it fails, fix and create
  a **new** commit — never `--amend` a rejected commit. Branch prefix is
  `claude/` per CLAUDE.md.
- **uv.lock regeneration gotcha** saved in memory
  (`feedback_precommit_uvlock_regen.md`): when pre-commit keeps failing with
  "files were modified by this hook" but `uv run ruff check --fix` passes
  manually, commit `uv.lock` first to break the cycle.

## 6. Key Files & Architecture

**Will be created**:
- `src/clm/workers/jupyterlite/__init__.py` — worker package init.
- `src/clm/workers/jupyterlite/builder.py` — `BuildJupyterLiteSiteOperation`.
- `src/clm/workers/jupyterlite/lite_dir.py` — lite-dir assembler.
- `src/clm/workers/jupyterlite/launcher.py` — `launch.py` emitter.
- `src/clm/cli/info_topics/jupyterlite.md` — version-accurate info topic.
- `docs/user-guide/jupyterlite.md` — user-facing guide (Phase 4).
- `tests/workers/jupyterlite/` — unit + integration tests.

**Will be modified**:
- `src/clm/core/course_spec.py` — `VALID_FORMATS`, `JupyterLiteConfig`,
  course-level parse.
- `src/clm/core/output_target.py` — `formats=None` default tightening.
- `src/clm/core/course.py` — cross-validation.
- `src/clm/cli/info_topics/spec-files.md` — document new format + block.
- `pyproject.toml` — new `[jupyterlite]` extra.
- `docs/developer-guide/architecture.md` — `JupyterLiteBuilder` section.
- `CHANGELOG.md` — release note.

**Entry points and connections**:
- Spec XML → `OutputTargetSpec.from_element()` → `OutputTarget.from_spec()`
  → build planner → (barrier after notebook jobs) → `BuildJupyterLiteSite`
  job enqueued with `service_name="jupyterlite-builder"` → worker dequeues,
  assembles lite-dir, shells out to `jupyter lite build`, writes
  `_output/` + optional `launch.py` into
  `<target>/<language>/jupyterlite/<kind>/`.

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

**Coverage target**: Phase 1 must land with the regression test green and
the full existing fast suite unchanged in pass/fail count.

## 8. Session Notes

- The user confirmed v1 scope constraints (Python only, xeus-python default,
  shared Pyodide deferred, no COOP/COEP) and added the opt-in requirement
  explicitly. Design doc reflects both.
- **Opt-in is the dominant design constraint.** Every architectural
  decision in Phase 1 must be evaluated against "does this change the
  output of any course that doesn't opt in?" If the answer is anything
  other than a confident "no," stop and reconsider.
- CLM is a Windows-first project. Test the launcher's wasm MIME fix on
  Windows specifically — Python's `mimetypes` on Windows reads from the
  registry and may guess `.wasm` wrong.
- User prefers Python over bash for tooling wrappers (confirmed memory).
  The launcher and any build helpers should be `.py` files, not `.sh`.
- The research sub-agent could not directly fetch `readthedocs` pages
  (403). Claims about JupyterLite behavior in the design doc came from
  WebSearch excerpts of those pages + GitHub issues. Re-verify version
  and API details at the start of Phase 2 against the live docs.
- `jupyterlite-core` is mid-upgrade from 0.7 → 0.8 (alpha) as of
  2026-04-15. Pin to `>=0.7,<0.9` and revisit end of Phase 2.
