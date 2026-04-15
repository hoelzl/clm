# JupyterLite Output Target

**Status**: Draft for discussion
**Author**: Claude (Opus 4.6)
**Date**: 2026-04-15
**Scope**: new output format `jupyterlite`; new worker `JupyterLiteBuilder`; student-local launcher; opt-in per course and per output target.

---

## 1. Goals

1. Produce a **deployable JupyterLite static site** from the notebook-format output that CLM already builds, so students can run course notebooks in the browser with no Python install.
2. Support **two delivery modes** from the same build output:
   a. Hosted (GitHub Pages, nginx, any static CDN).
   b. Local-first, zero-install — a student downloads the directory and runs a launcher that serves it on `http://localhost:<port>`.
3. **Opt-in only.** Courses that do not explicitly enable JupyterLite build exactly as they do today: same artifacts, same timings, no new dependencies required in the core install.
4. Keep the integration aligned with CLM's existing architecture — `<output-target>` spec element, `OutputTarget` runtime object, queued worker operation, per-`(language, kind)` artifact layout.

## 2. Non-Goals (v1)

- Non-Python kernels. C++ via xeus-cpp is plausible but deferred. C#/Java/TypeScript course targets do not emit JupyterLite at all.
- Runtime `%pip install` from the open internet as the primary package mechanism. v1 pre-stages wheels at build time; runtime install is available but unsupported by CLM's default config.
- COOP/COEP tuning for SharedArrayBuffer. v1 accepts the service-worker comms fallback and works on GitHub Pages out of the box.
- Per-student persistence beyond IndexedDB defaults. (Student edits survive until site data is cleared. Documented, not engineered around.)
- Merging multiple `output-target`s into one JupyterLite site. Each target that enables JupyterLite produces its own site.

## 3. Opt-in model (load-bearing)

Two independent gates. Both must be set for a single byte of JupyterLite output to be produced. Either absent ⇒ the build behaves identically to today.

### 3.1 Gate 1 — course-level config with per-target override

A new optional `<jupyterlite>` element may appear in **two places**, with
per-target overriding course-level:

- **Course-level** (child of `<course>`): defaults for every target that opts in.
- **Target-level** (child of `<output-target>`): overrides or replaces the
  course-level config for that one target.

Rationale: a course will typically ship JupyterLite for *shared* and *trainer*
targets but **not** for *speaker* targets (speaker notes aren't meant to be
run in a student browser). Per-target override lets the user keep a single
course-level default while suppressing or adjusting the config on individual
targets.

```xml
<course>
  ...
  <jupyterlite>                         <!-- course-level default -->
    <kernel>xeus-python</kernel>        <!-- or "pyodide"; no default, must be stated -->
    <wheels>
      <wheel>wheels/rich-13.7.1-py3-none-any.whl</wheel>
      <wheel>wheels/ipywidgets-8.1.5-py3-none-any.whl</wheel>
    </wheels>
    <environment>jupyterlite/environment.yml</environment>  <!-- xeus only, optional -->
    <launcher>true</launcher>            <!-- emit student launcher, default true -->
    <app-archive>offline</app-archive>   <!-- "offline" or "cdn", default "offline" -->
  </jupyterlite>

  <output-target name="student-playground">
    <path>output/playground</path>
    <formats><format>notebook</format><format>jupyterlite</format></formats>
    <kinds><kind>completed</kind></kinds>
    <!-- no target-level <jupyterlite>; uses course-level -->
  </output-target>

  <output-target name="trainer-playground">
    <path>output/trainer</path>
    <formats><format>notebook</format><format>jupyterlite</format></formats>
    <kinds><kind>code-along</kind></kinds>
    <jupyterlite>                       <!-- override: trainer wants extra wheels -->
      <kernel>xeus-python</kernel>
      <wheels>
        <wheel>wheels/rich-13.7.1-py3-none-any.whl</wheel>
        <wheel>wheels/ipywidgets-8.1.5-py3-none-any.whl</wheel>
        <wheel>wheels/pytest-8.3.3-py3-none-any.whl</wheel>
      </wheels>
    </jupyterlite>
  </output-target>

  <output-target name="speaker-only">
    <path>output/speaker</path>
    <formats><format>html</format></formats>  <!-- no jupyterlite; not needed here -->
    <kinds><kind>speaker</kind></kinds>
  </output-target>
  ...
</course>
```

**Merge semantics**: target-level `<jupyterlite>` **replaces** course-level
wholesale (not field-wise merge). Simpler to reason about; avoids "did I
override or inherit this wheel list?" ambiguity. If a user wants to reuse most
course-level settings, they can copy the block — target-level is rare enough
that verbosity is acceptable.

**Validation rule**: if any `<output-target>` lists `jupyterlite` in its
`<formats>` and *neither* course-level *nor* target-level `<jupyterlite>` is
present, **`clm validate` and `clm build` fail** with a clear error pointing
at `clm info jupyterlite`. This prevents silent mis-builds while allowing a
speaker target to cleanly opt out by simply not listing the format.

### 3.2 Gate 2 — explicit per-target format listing

The format string `jupyterlite` is added to `VALID_FORMATS`, but critically: the `formats=None` default ("all formats") is redefined as **`{"html", "notebook", "code"}` — not including `jupyterlite`**. The only way a target produces JupyterLite is by listing `<format>jupyterlite</format>` explicitly. A target that omits `<formats>` entirely behaves exactly as today.

```xml
<output-target name="online-playground">
  <path>output/playground</path>
  <formats>
    <format>notebook</format>
    <format>jupyterlite</format>       <!-- required; no implicit inclusion -->
  </formats>
  <kinds><kind>completed</kind></kinds>
  <languages><language>en</language></languages>
</output-target>
```

### 3.3 Dependency opt-in

A new optional extra `[jupyterlite]` carries `jupyterlite-core`,
`jupyterlite-pyodide-kernel`, and `jupyterlite-xeus`. The launcher needs no
extra runtime deps (stdlib `http.server` is sufficient). Users who never touch
JupyterLite and do not install the convenience `[all]` bundle never install
any of this.

`[jupyterlite]` **is included in `[all]`** for developer convenience, matching
the pattern used by `[voiceover]`, `[recordings]`, etc. This means `pip
install -e ".[all]"` gives a developer everything needed to build, validate,
and test JupyterLite targets without a separate install step. The opt-in
*build-time* gates in §3.1 and §3.2 are unaffected — installing the package
does not cause any course to produce JupyterLite output.

If a build hits a JupyterLite operation without the extra installed, it fails
with the standard CLM "missing extra" error pointing at `pip install -e
".[jupyterlite]"` (or `".[all]"`).

## 4. Architecture

### 4.1 Where it plugs into CLM

JupyterLite is **not** a peer of `html`/`notebook`/`code` at the per-file cell-filtering level. It is a **site bundler that consumes already-built notebook output**. The integration reflects that:

| Concern | Treatment |
|---|---|
| Cell filtering per kind | Reuses existing `CompletedOutput` / `CodeAlongOutput` / `SpeakerOutput` rules. No new `OutputSpec` subclass. |
| File-level artifact | Reuses the `.ipynb` produced by the `notebook` format. If a target requests `jupyterlite` but not `notebook`, the notebook artifact is generated into a scratch dir internally; only the JupyterLite site is published. |
| Bundling | A new `JupyterLiteBuilder` worker runs **once per `(output_target, language, kind)` tuple**, after the notebook operations for that tuple have completed. |
| Dispatch key | `service_name = "jupyterlite-builder"`, registered as a new `job_type` in the SQLite queue. |

### 4.2 New worker: `JupyterLiteBuilder`

Location: `src/clm/workers/jupyterlite/`.

Operation: `BuildJupyterLiteSiteOperation` with payload

```python
@define
class BuildJupyterLiteSitePayload:
    course_root: Path
    notebook_tree: Path           # dir of already-built .ipynb files
    output_dir: Path              # .../jupyterlite/<kind>/
    language: str                 # "de" | "en"
    kind: str                     # "completed" | "code-along" | "speaker"
    kernel: Literal["xeus-python", "pyodide"]
    wheels: list[Path]
    environment_yml: Path | None
    app_archive: Literal["offline", "cdn"]
    emit_launcher: bool
```

Steps performed by the worker:

1. Assemble a temporary `lite-dir/` with
   - `files/` ← symlinks or hardlinks into `notebook_tree` (preserves subdirectory structure → becomes the site's file tree).
   - `pypi/` ← the listed wheels.
   - `jupyter_lite_config.json` ← templated from the course-level `<jupyterlite>` block (kernel choice, piplite URLs, PyPI fallback off by default).
   - `overrides.json` ← optional theme/branding pulled from a future `<branding>` block.
   - For xeus: `environment.yml` copied in.
2. Shell out to `jupyter lite build --lite-dir <tmp> --output-dir <output_dir>`.
3. If `emit_launcher=True`, write `launch.py` and `README-offline.md` into `output_dir` (see §4.4).
4. Emit a build manifest (`jupyterlite-manifest.json`) listing input notebooks, kernel, wheel set, and build time — used by CLM's cache layer to skip rebuilds when nothing changed.

### 4.3 Scheduling against existing notebook jobs

A JupyterLite job has a dependency on all notebook-format jobs for the same `(target, language, kind)`. Two implementation options:

- **(A) Barrier in the build coordinator.** The existing build planner emits JupyterLite jobs only after the corresponding notebook jobs finish. Zero changes to the generic queue.
- **(B) Job-level dependency field.** Add `depends_on` to the `jobs` row. More general but larger surface area.

**Recommendation: (A).** It's the smallest change and matches how per-file → per-site flows naturally express themselves in the current planner.

### 4.4 Student launcher

Goal: a student unzips the site directory and runs one thing.

The launcher is a single `launch.py` that:

1. Picks a free port.
2. Starts `http.server.ThreadingHTTPServer` with a `SimpleHTTPRequestHandler` subclass that forces `application/wasm` for `.wasm` (needed on older Windows Python that guesses wrong).
3. Opens `http://localhost:<port>/lab/index.html` in the default browser.
4. Runs until Ctrl+C.

`python launch.py` works on any machine with Python ≥ 3.9 — which the target audience already has because they're in a programming course. We do **not** bundle a static-server binary in v1; the design leaves room for a `<launcher>miniserve</launcher>` option that ships an OS-specific binary alongside `launch.py` later, but v1 keeps the launcher to a single ~60-line Python file with no dependencies.

### 4.5 Output layout

```
<target.path>/
  <language>/
    jupyterlite/
      <kind>/
        _output/           # jupyter lite build output — deployable as-is
          index.html
          lab/
          files/
          api/
          ...
        launch.py          # if emit_launcher=true
        README-offline.md
        jupyterlite-manifest.json
```

This sits next to the existing `html/`, `notebook/`, `code/` format directories under each `(target, language)` pair — no conflicts with current layout.

## 5. Phased implementation plan

### Phase 1 — Spec plumbing and validation (no build work yet)

- Add `"jupyterlite"` to `VALID_FORMATS` in `core/course_spec.py`.
- **Change the semantics of `formats=None`** in `OutputTarget.from_spec()` to expand to `{"html", "notebook", "code"}` explicitly, rather than all of `VALID_FORMATS`. Add a regression test that proves existing courses produce identical format sets.
- Add `CourseSpec.jupyterlite: JupyterLiteConfig | None` parsed from the optional course-level `<jupyterlite>` element.
- Add `OutputTargetSpec.jupyterlite: JupyterLiteConfig | None` parsed from the optional target-level `<jupyterlite>` element.
- Add `OutputTarget.effective_jupyterlite_config()` returning the target-level config if set, else the course-level config (wholesale replacement, not field-merge).
- Add cross-validation: any target requesting `jupyterlite` whose `effective_jupyterlite_config()` is `None` ⇒ validation error with a pointer to `clm info jupyterlite`.
- New info topic: `src/clm/cli/info_topics/jupyterlite.md`.
- Update `spec-files.md` info topic.

Exit criteria: existing courses build byte-identical output. A course with `<jupyterlite>` + a target requesting `jupyterlite` validates but does not yet produce the site (the format is recognized and routed to a stub that logs "not yet implemented"). Full test suite green.

### Phase 2 — `JupyterLiteBuilder` worker

- New optional extra `[jupyterlite]` in `pyproject.toml`, **and add it to the `[all]` bundle** alongside `[voiceover]`, `[recordings]`, etc.
- New worker package `src/clm/workers/jupyterlite/` with `BuildJupyterLiteSiteOperation`, payload, and a `NotebookBuilder` that wraps `jupyter lite build`.
- Register `"jupyterlite-builder"` as a job type in the worker registry.
- Build planner: emit the barrier-scheduled JupyterLite job per `(target, language, kind)` when the format is requested.
- Cache key: notebook-tree hash + wheel-set hash + kernel + jupyterlite-core version.

Exit criteria: a minimal course with one section and `jupyterlite` format produces a site whose `_output/index.html` loads in a browser and runs a `print("hello")` cell end-to-end.

### Phase 3 — Student launcher, branding, polish

- `launch.py` emitter with the wasm-MIME fix.
- `README-offline.md` with the persistence caveat spelled out.
- Optional `<branding>` block (logos, theme) → `overrides.json`.
- Integration test: headless browser (Playwright) loads `_output/index.html`, executes a known notebook cell, asserts output.
- CLI: `clm jupyterlite preview <target>` convenience command — wraps launching the most recently built site.

Exit criteria: a student can download a zip, run `python launch.py`, and use the notebooks. CI runs the headless smoke test on Linux.

### Phase 4 — Documentation and release

- `docs/user-guide/jupyterlite.md` — user-facing guide (opt-in, kernel choice tradeoffs, deployment recipes for GH Pages/nginx, offline wheel curation).
- `docs/developer-guide/architecture.md` — JupyterLiteBuilder section.
- CHANGELOG entry.
- Version bump + release per existing rules (`pytest -m "not docker"` green, CI green on tag).

## 6. Risks and open questions

| Risk | Mitigation |
|---|---|
| `jupyterlite-core` 0.7 → 0.8 churn during implementation. | Pin to `jupyterlite-core>=0.7,<0.9`. Revisit at the end of Phase 2. |
| Site size (~80–120 MB with Pyodide) shipped per `(language, kind)`. | Option to share one Pyodide runtime across kinds via symlink. Deferred; v1 accepts duplication and documents it. |
| Students on corporate networks blocked from IndexedDB. | Not solvable by CLM. Documented in `README-offline.md`. |
| Windows `python -m http.server` MIME-guessing of `.wasm`. | Launcher subclasses `SimpleHTTPRequestHandler` to force the MIME. Covered by a unit test. |
| Opt-in gate accidentally broken, causing surprise rebuilds. | Regression test in Phase 1 enumerates a reference course spec and asserts its format set is exactly `{html, notebook, code}`. Any PR that changes default-format semantics must update that test. |

## 7. Summary

JupyterLite fits CLM cleanly as a site-bundler worker that consumes notebook-format output, gated by two independent opt-ins (a course-level `<jupyterlite>` block and an explicit `<format>jupyterlite</format>` per target). The format defaults are tightened so "no JupyterLite config" means "no JupyterLite output, ever." Total effort estimate: ~1–2 weeks across four phases, with Phase 1 + Phase 2 being the non-trivial parts and Phases 3–4 being mostly polish and documentation.
