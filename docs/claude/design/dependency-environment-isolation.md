# Design: dependency-environment isolation (three roles, three environments)

Status: in progress (2026-07-02). Wave 1 (packaging) and Wave 2a (JupyterLite
tool env) have shipped; Wave 2b (course-runtime kernel env) is still proposed.
Context: issue #516 follow-up.

## Problem

CLM's Direct execution mode runs everything in **one** Python environment —
clm's own venv. That single venv is asked to hold three kinds of dependency
that have nothing to do with one another:

| Role | Examples | Does clm `import` it? | Belongs in |
|---|---|---|---|
| **A. clm tooling** | nbconvert, jupytext, ipykernel, voiceover (whisper/cv2), recordings, mcp, slides, gcal, summarize (openai), replay glue | **yes** — clm source imports it | clm's venv |
| **B. course-runtime** | `[ml]`: torch, transformers, pandas, scikit-learn, fastai, langchain | **no** — zero imports in `src/clm/` | the notebook **kernel's** env |
| **C. subprocess tools** | `[jupyterlite]`/empack, drawio app, plantuml JAR, mitmproxy | **no** — clm shells out to a CLI | an isolated **tool** env |

Conflating them causes concrete harm:

- **Dependency-resolution collisions.** `[jupyterlite]` → `jupyterlite-xeus` →
  `empack` pins `click<8.2`, which collides with clm's own CLI (needs
  `click>=8.2`). Left in one resolution, uv pins the whole graph to Click 8.1.8
  and ~30 CLI tests fail on a freshly-synced worktree (issue #516 follow-up).
- **Install bloat.** `[ml]` drags a multi-GB PyTorch/CUDA stack into every
  environment that wanted only clm's own tooling, despite clm never importing a
  line of it.
- **Version-truth confusion.** The course-runtime versions a student sees are
  whatever happens to be co-resolved with clm, not an explicitly pinned set.

## Current architecture (evidence)

The good news: **the correct model already exists for the Docker path.** The
split is only missing from Direct mode.

- **Notebook execution** uses nbconvert's `ExecutePreprocessor`, which starts a
  real Jupyter kernel via `jupyter_client`'s `KernelManager`
  (`src/clm/workers/notebook/notebook_processor.py:586,639,653,1613`). The
  preprocessor is created with **no `kernel_name`**, so the kernel is chosen by
  the notebook's `kernelspec` metadata — a fixed per-language name
  (`python3`, `xcpp20`, `.net-csharp`, `deno`, `java`) stamped from
  `prog_lang_utils.py:16-99` (`notebook_processor.py:1222`). That kernelspec
  resolves **in whatever environment the worker runs in**.
- **Direct worker** is spawned as `sys.executable -m clm.workers.notebook`
  (`src/clm/infrastructure/workers/worker_executor.py:704`) — clm's own venv —
  so the `python3` kernel is clm's own ipykernel. Course `import pandas` must be
  satisfied by clm's venv. **There is no interpreter/kernel override anywhere**
  (`worker_executor.py:41-64` injects only Jinja/logging env).
- **Docker worker** already isolates: a micromamba `/opt/conda` image with the
  course-runtime stack baked in (lite/full variants,
  `docker/notebook/Dockerfile:211-351`) and clm installed **`--no-deps`** on top
  (`:365`). That *is* "a separate venv for the notebook worker," done right.
- **JupyterLite** is already a distinct out-of-process phase: a dedicated
  operation that runs after the notebook jobs
  (`src/clm/core/operations/build_jupyterlite_site.py:1-8`) and shells out to
  `sys.executable -m jupyterlite_core build`
  (`src/clm/workers/jupyterlite/builder.py:70-86`). clm never imports
  jupyterlite/empack — it only invokes the CLI. Its kernels run in the browser
  (WASM), a third environment entirely.

## Goals / non-goals

**Goals**
- clm's published/synced environment holds only Role A (what clm imports).
- Role B (course-runtime) lives in a course-owned environment the notebook
  kernel is pointed at — in Direct mode, not just Docker.
- Role C (subprocess tools) run from isolated tool environments so their
  transitive constraints never enter clm's resolution.
- The `empack`/Click collision becomes structurally impossible, not
  conflict-managed.

**Non-goals**
- Changing the Docker path (already correct).
- Changing browser (WASM) JupyterLite kernel packaging.
- Forcing multi-venv setup on solo devs who are happy with Docker mode for
  heavy course decks.

## Proposed design

### Role A — unchanged
clm's tooling extras (`notebook`, `plantuml`, `drawio`, `voiceover`,
`recordings`, `mcp`, `slides`, `gcal`, `summarize`, `replay`, `dev`, `tui`,
`web`) stay in clm's environment and in `[all]` / the default `uv sync` group.

### Role B — course-runtime kernel environment (Direct mode)
Give the Direct notebook worker a way to launch its kernel from a **separate
interpreter**, mirroring what Docker already does. Two routes, cheapest first:

1. **Kernelspec + `JUPYTER_PATH` (little/no core code).** Register a `python3`
   kernelspec whose `argv[0]` points at a *course venv* (torch/pandas/…), and
   prepend that kernelspec dir to the worker's `JUPYTER_PATH`. The clm-env worker
   still drives nbconvert (Role A) while the *kernel subprocess* runs in the
   course venv. Mostly env plumbing in `worker_executor.py` + a small
   provisioning helper; `[ml]` leaves clm's venv entirely.
2. **First-class interpreter knob (proper feature).** Add a config option
   (`CLM_NOTEBOOK_KERNEL_PYTHON` / `clm.toml [jupyter].kernel_python`) that
   *drives* the route-1 provisioning. Makes course-runtime isolation an explicit,
   opt-in setting with an empty default (no behaviour change until set).

**Decision (see "Wave 2b — detailed design" below):** these are not two
competing routes — route 1 is the mechanism and route 2 is the interface over
it. We ship the config knob backed by kernelspec+`JUPYTER_PATH` provisioning,
and deliberately do **not** thread a raw interpreter into `KernelManager`
(that would add code to the hot execution path for no extra capability).

### Role C — subprocess tool environments
clm already shells out to JupyterLite; change the invocation from
`sys.executable -m jupyterlite_core build` to a **dedicated tool env** —
`uvx jupyterlite-core …` (or a managed, pinned venv). Then `jupyterlite-xeus` /
`empack` never enter clm's dependency graph, the `[jupyterlite]` extra and its
`[tool.uv] conflicts` fork are deleted, and JupyterLite versions independently.
This mirrors the existing precedent for `replay` (`pyproject.toml` comments
already describe an "isolated mitmdump tool env").

Audit the other Role-C tools (drawio app, plantuml JAR — already external; the
mitmproxy transport) and confirm none leak constraints into clm's resolution.

### Packaging endpoint
- clm's extras = Role A only.
- `[ml]` → documented as a *course* environment (provisioned for the kernel),
  not a clm extra people are expected to merge into clm's venv.
- `[jupyterlite]` extra removed once the build runs from a tool env.

## Migration / phasing

- **Wave 1 (shipped):** remove `[ml]`/`[jupyterlite]` from `[all]` and the
  default `uv sync` group; fork jupyterlite via `[tool.uv] conflicts` so clm's
  CLI gets modern Click; CI installs from the lock. Removes the immediate
  landmine without any worker-code change. (This PR.)
- **Wave 2a (shipped):** Role C — the JupyterLite build now shells out to a
  pinned `uvx` tool env (`src/clm/workers/jupyterlite/builder.py`); the
  `[jupyterlite]` extra and the `[tool.uv] conflicts` fork are deleted. clm's
  env only needs `uv` on PATH.
- **Wave 2b:** Role B — kernelspec/`JUPYTER_PATH` course-venv wiring exposed as a
  first-class config knob (see the detailed design below), then remove `[ml]` as
  a clm extra.

## Wave 2b — detailed design (Role B: course-runtime kernel env)

### The exact mechanism we exploit

Direct-mode notebook execution runs in clm's own venv, but the *kernel* it
launches is selected indirectly, and that indirection is the whole opening:

1. `TrackingExecutePreprocessor` is constructed with **no `kernel_name`**
   (`src/clm/workers/notebook/notebook_processor.py:1613`). nbconvert therefore
   falls back to the notebook's `metadata.kernelspec.name`.
2. That name is stamped by `kernelspec_for(payload.prog_lang)`
   (`notebook_processor.py:1222`) from a fixed per-language table
   (`src/clm/workers/notebook/utils/prog_lang_utils.py:64-68`) — for Python it is
   the literal string **`python3`**.
3. `jupyter_client` resolves `python3` by scanning kernelspec directories. The
   dirs named by the **`JUPYTER_PATH`** env var are searched *before* the user
   and system data dirs, and the first `kernels/python3/kernel.json` found wins.

So: if the worker process has `JUPYTER_PATH` pointing at a directory that
contains `kernels/python3/kernel.json` whose `argv[0]` is a **course-venv**
interpreter, then nbconvert (Role A, clm's venv) drives the run while the
**kernel subprocess** (Role B) executes in the course venv. `import pandas`
is satisfied by the course venv; `[ml]` never has to be in clm's venv.

No change to `notebook_processor.py` is required — this is pure environment
plumbing, mirroring what the Docker image already does (conda `/opt/conda` +
clm installed `--no-deps`, `docker/notebook/Dockerfile`).

### Why not thread an interpreter into `KernelManager` directly

The original doc floated a "route 2" that threads a raw interpreter path into
the `KernelManager`/`ExecutePreprocessor`. We reject that: it means overriding
`kernel_name` and/or subclassing the manager to rewrite `argv`, i.e. real code
on the hot execution path, for no capability the kernelspec+`JUPYTER_PATH`
approach lacks. The durable, first-class interface is a **config knob that
drives the kernelspec provisioning**, not a new kernel-launch code path.

### Config surface

Add one field to `JupyterConfig` (`src/clm/infrastructure/config.py:234`):

```python
kernel_python: str = Field(
    default="",
    description="Path to the interpreter that runs the Python notebook kernel "
                "in Direct mode. Empty = use clm's own environment (today's "
                "behaviour). Set to a course venv to isolate course-runtime "
                "packages ([ml] etc.) from clm's environment.",
)
```

Resolution order (already the house style — env > `clm.toml [jupyter]` >
default): env var **`CLM_NOTEBOOK_KERNEL_PYTHON`** overrides
`clm.toml [jupyter].kernel_python`, default empty.

- **Empty (default):** no provisioning, no `JUPYTER_PATH` injection — byte-for-
  byte today's behaviour. This is the safety valve: Wave 2b is inert until a
  user opts in.
- **Set:** provision a `python3` kernelspec pointing at that interpreter and
  prepend its root to the worker's `JUPYTER_PATH`.

Scope: **Python only.** C++/C#/Java/TS kernels (`xcpp20`, `.net-csharp`,
`java`, …) are external toolchains, not clm's venv, and are out of scope — the
provisioning writes only the `python3` kernelspec and leaves every other name
to resolve as it does today.

### Wiring (where the code goes)

1. **Provisioning helper** — a small module (e.g.
   `src/clm/infrastructure/workers/kernel_env.py`) with
   `provision_course_kernel(python_exe: Path) -> Path` that:
   - validates `python_exe` exists and that `ipykernel` is importable from it
     (a cheap `python_exe -c "import ipykernel"` check → actionable error if
     missing);
   - writes `<clm-data>/kernel-envs/<hash-of-python>/kernels/python3/kernel.json`
     with
     `argv = [str(python_exe), "-m", "ipykernel_launcher", "-f", "{connection_file}"]`,
     `display_name`, `language: "python"`;
   - returns the **root** dir (the one *containing* `kernels/`), which is what
     `JUPYTER_PATH` wants.
2. **Env injection** — in `worker_executor.py`, extend the notebook-worker env
   assembly next to `_notebook_worker_jupyter_env()` (`:41-64`, injected at
   `:701`; Docker path mirrors at `:318-379`). When `kernel_python` is set,
   compute the root via the helper and set
   `JUPYTER_PATH = <root><os.pathsep><existing JUPYTER_PATH>`. Direct mode only
   in Wave 2b; the Docker image already isolates, so skip injection there.
3. **No `notebook_processor.py` change.** The reaping/JobObject teardown that
   already kills kernel grandchildren (`_ReapingKernelManager`,
   `notebook_processor.py:586`; Windows JobObject in `worker_executor.py:741`)
   covers the course-venv kernel unchanged — it is still a subprocess of the
   worker.

### Provisioning command (setup friction mitigation)

A first-class CLI entry so users don't hand-write `kernel.json`:

- **MVP — register an existing interpreter:**
  `clm provision kernel-env --python <path-to-course-venv-python>` → runs the
  helper, prints the root dir and the `clm.toml`/env line to set. Zero venv
  management; the user brings their own course venv.
- **Follow-up — create it too:** `clm provision kernel-env --create --with ml`
  → `uv venv` a course env, install `ipykernel` + the requested course-runtime
  set, then register. Deferred; not needed to remove `[ml]` from clm's env.

(Command name to confirm at implementation — `clm provision …` vs folding under
an existing group. It must appear in `clm info commands`.)

### Migration / phasing (within 2b)

- **2b-1:** config knob + provisioning helper + `JUPYTER_PATH` wiring +
  `clm provision kernel-env` (register-existing MVP). Opt-in, empty default →
  no behaviour change. Ships with tests + docs. **`[ml]` stays a clm extra** for
  this step so nothing breaks for current users.
- **2b-2:** once the knob is proven, reposition `[ml]` as a *course* extra
  (installed into the course venv, not clm's). Update
  `docs/user-guide/installation.md`, the `clm info` topics, and the memory. This
  is the step that actually removes the multi-GB stack from clm's env.

### Testing

- **Unit:** helper writes `kernel.json` with the correct `argv`/`{connection_file}`;
  empty knob → `_notebook_worker_jupyter_env()`-adjacent injection is a no-op and
  `JUPYTER_PATH` is untouched; set knob → root is prepended (not appended) and
  survives an existing `JUPYTER_PATH`; missing `ipykernel` → actionable error.
- **Integration (gated, needs a second venv):** create a throwaway venv with a
  marker package that is **not** installed in clm's env, point
  `CLM_NOTEBOOK_KERNEL_PYTHON` at it, and assert a notebook that `import`s the
  marker executes green in Direct mode — proving the kernel really ran in the
  course venv. Skip when the fixture venv can't be built.

### Open questions / risks specific to 2b

- **`ipykernel` in the course venv is mandatory** (the kernel launcher lives
  there). Provisioning validates it; document it for the `--python` path.
- **http-replay interaction:** the course-venv kernel still inherits the worker
  env, so the mitmproxy bootstrap env vars (`worker_executor.py:98-133`,
  `:349-379`) still reach it — but the *replay bootstrap client-patching* must be
  importable/enabled in the course venv too. Verify with an ml+replay course
  before flipping `[ml]` out (2b-2), or document replay as clm-env-only for now.
- **Version-truth surfacing:** an explicit course venv is the point — see the
  Trade-offs note below.

## Trade-offs / risks

- **Setup friction.** Splitting reintroduces "which venv runs the kernel?" on a
  dev box — the very thing bundling made easy. Mitigate with a `clm` provisioning
  command for the kernel/tool envs, or uv workspaces / PEP-735 groups. The
  minimalist alternative remains valid: *Direct mode is dev-only; heavy course
  decks use Docker (already isolated).*
- **Kernel-interpreter feature is real code.** Route 2 needs new selection logic
  + tests; until it lands, route 1 (kernelspec) or Docker covers Role B.
- **Version skew.** A separate course venv means clm can't guarantee the course's
  package versions match students' — but that is *better* surfaced by an explicit
  course venv (or the Docker image, the real source of truth) than hidden inside
  clm's resolution.
