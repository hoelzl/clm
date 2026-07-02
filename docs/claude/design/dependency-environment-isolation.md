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
2. **First-class interpreter knob (proper feature).** Add a config/spec option
   (e.g. `CLM_NOTEBOOK_KERNEL_PYTHON`, or a per-course spec attribute) that the
   worker threads into the `KernelManager` / kernelspec selection. Makes
   course-runtime isolation an explicit, per-course setting. Requires new code +
   tests; today no such override exists.

Recommended: ship route 1 first (it removes `[ml]` from clm's env with minimal
risk), design route 2 as the durable interface.

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
- **Wave 2b:** Role B — kernelspec/`JUPYTER_PATH` course-venv wiring (route 1),
  then the interpreter knob (route 2). Remove `[ml]` as a clm extra.

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
