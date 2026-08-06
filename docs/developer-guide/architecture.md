# CLM Architecture

This document describes the current architecture of the CLM system (v1.25.0).

The layering described here is **enforced, not aspirational**: since the #802
re-layering (2026-08-06, PRs #804–#813) the import graph matches this document,
and import-linter contracts in CI and pre-commit keep it that way. See
[How the layering is enforced](#how-the-layering-is-enforced).

## Overview

CLM is a course content processing system that converts educational materials
(Jupyter notebooks, PlantUML diagrams, Draw.io diagrams) into multiple output
formats using a worker-based architecture orchestrated by an SQLite job queue.

**Key characteristics**:

- Single unified Python package with integrated workers
- SQLite-based job queue (no message broker)
- Direct file system access (no serialization overhead for file content)
- Worker pools (Docker containers or direct host processes)
- Three constrained layers (`core` ← `infrastructure` ← `workers`) with the
  CLI and optional extension packages as unconstrained consumers on top

## The Layered Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                    TOP OF THE STACK (unconstrained)                 │
│                                                                    │
│  clm.cli — Click command-line interface                            │
│  Extensions — clm.notebooks, clm.slides, clm.snapshot, clm.mcp,    │
│    clm.voiceover, clm.recordings, clm.release, clm.cohort_calendar,│
│    clm.web                                                         │
│                                                                    │
│  May import anything below. Nothing below may import them.          │
└──────────────────────────────┬─────────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────────┐
│                 clm.workers (Worker Implementations)                │
│                                                                    │
│  notebook/   — notebook processing + execution (multi-kernel)      │
│  plantuml/   — PlantUML → PNG/SVG                                  │
│  drawio/     — Draw.io → PNG/SVG/PDF                               │
│  jupyterlite/ — JupyterLite static-site bundling                   │
│                                                                    │
│  May import infrastructure and core.                                │
└──────────────────────────────┬─────────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────────┐
│                  clm.infrastructure (Engines)                       │
│                                                                    │
│  backends/  — SqliteBackend, LocalOpsBackend, DummyBackend         │
│  database/  — schema + migrations, JobQueue, caches, heartbeats    │
│  workers/   — pool manager, worker base/executor, lifecycle,       │
│               image-identity fingerprinting                        │
│  api/       — worker API server/client (Docker mode)               │
│  http_replay_mitm/ — mitmproxy cassette record/replay + staging    │
│  llm/       — LLM clients (OpenRouter, Ollama) + cache             │
│  services/  — subprocess execution with retry (subprocess_tools)   │
│  logging/, utils/, config, error_categorizer, …                    │
│                                                                    │
│  May import core only.                                              │
└──────────────────────────────┬─────────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────────┐
│                      clm.core (Domain + Contracts)                  │
│                                                                    │
│  Domain model — Course, Section, Topic, CourseFile, DirGroup,      │
│    CourseSpec, OutputTarget, execution dependencies                │
│  Contract seam — Operation, Backend ABC, messaging/ payloads,      │
│    build_data_classes + BuildReporterProtocol, build_profiling     │
│  operations/ — per-file-type processing operations                 │
│  slide_text/ — percent-format slide-file model                     │
│  utils/ + vocabulary — path/output naming, prog-lang tables, tags, │
│    workshop scope, deck markers, sidecar layout, voiceover         │
│    companion paths, worker-identity registry                       │
│                                                                    │
│  Imports NOTHING clm-internal above it.                             │
└────────────────────────────────────────────────────────────────────┘
```

Two properties distinguish this from the usual layered-architecture diagram:

1. **The bottom three layers are constrained; the top is not.** `clm.cli` and
   the extension packages may import each other and anything below — there is
   deliberately no ordering among them. The contracts only guarantee that
   `core`, `infrastructure`, and `workers` never import upward.
2. **The contract seam lives in core.** The `Operation` hierarchy, the
   `Backend` ABC, and the Pydantic payload schemas that cross the worker
   process boundary are *contracts and data*, so they live at the bottom of
   the stack where every layer may import them (Phase 8 S2 of #802).
   Infrastructure keeps the *engines* that implement them.

### How the layering is enforced

Three [import-linter](https://import-linter.readthedocs.io/) contracts, defined
in `pyproject.toml` under `[tool.importlinter]`:

1. **"Layered: core below infrastructure below workers"** — a `layers`
   contract over `clm.workers` / `clm.infrastructure` / `clm.core`.
2. **"Constrained layers never import the CLI"** — `core`, `infrastructure`,
   and `workers` are forbidden from importing `clm.cli`.
3. **"Constrained layers never import extension packages"** — the same three
   layers are forbidden from importing `clm.cohort_calendar`, `clm.mcp`,
   `clm.notebooks`, `clm.recordings`, `clm.release`, `clm.slides`,
   `clm.snapshot`, `clm.voiceover`, and `clm.web`.

Run locally with `uv run lint-imports`; CI's lint job and the pre-commit hook
both run it. import-linter's graph (grimp) sees function-body and
`TYPE_CHECKING` imports, so lazy imports cannot dodge the contracts.

`tests/test_architecture_contracts.py` adds what import-linter cannot see:

- a **string-import guard** — `importlib.import_module("clm...")` /
  `__import__("clm...")` in a constrained layer would be invisible to the
  import graph, so the test forbids stringified clm imports there outright;
- a **`Backend` surface pin** — the exact set of abstract methods, so the
  contract changes only deliberately, in the same commit as every
  implementation;
- **worker payload schema pins** — every field each worker deserializes
  (Docker images can lag the host, so a renamed field silently strands
  lagging workers).

**Compatibility surface**: `from clm.infrastructure import Backend, Operation`
still works. `clm/infrastructure/__init__.py` lazily re-exports both names
from their real home in `clm.core` (PEP 562), pinned by
`tests/cli/test_cli_startup.py`. New code should import from
`clm.core.backend` / `clm.core.operation` directly.

### Layer 1: `clm.core` (Domain Model and Contracts)

**Purpose**: the course domain model, the contracts every other layer programs
against, and the shared vocabulary — importing nothing clm-internal above it.

**Domain model**:

- `Course` — course representation; owns sections, output targets, and the
  stage-driven processing loop (`process_all` / `process_stage` /
  `process_file`), each parameterized by a `Backend`
- `Section`, `Topic` — course structure
- `CourseFile` (abstract) with concrete subclasses in `core/course_files/`:
  `NotebookFile`, `PlantUmlFile`, `DrawioFile`, `DataFile`, `ImageFile` and
  friends
- `CourseSpec` (`course_spec.py`) — XML spec parsing
- `OutputTarget` / `execution_dependencies.py` — see
  [Multiple Output Targets](#multiple-output-targets)
- `core/operations/` — the per-file-type operations (`process_notebook`,
  `convert_plantuml_file`, `convert_drawio_file`,
  `convert_source_output_file`, `copy_file`, `copy_dir_group`, `delete_file`,
  `build_jupyterlite_site`)

**Contract seam** (descended from infrastructure in #802/S2):

- `operation.py` — the `Operation` hierarchy (`Operation`, `NoOperation`,
  `Concurrently`)
- `backend.py` — the abstract `Backend` a build executes against; its
  abstract surface is pinned by `tests/test_architecture_contracts.py`
- `messaging/` — the Pydantic payload/result schemas that cross the worker
  process boundary (`base_classes`, `notebook_classes`, `plantuml_classes`,
  `drawio_classes`, `jupyterlite_classes`, `correlation_ids`, `routing_keys`)
- `build_data_classes.py` — build reporting data classes plus the structural
  `BuildReporterProtocol` backends report against (the CLI's concrete
  `BuildReporter` implements it)
- `build_profiling.py`, `http_replay_trace.py` — dependency-free diagnostic
  leaves consumed on both sides of the core/infrastructure seam

**Shared vocabulary** (mostly under `core/utils/` — descended in #802/A6/S1):

- `utils/path_utils.py` — `Lang`, `Format`, `Kind`, `OutputSpec`,
  `output_specs()`, output-path construction, skip/ignore tables, slide-family
  detection
- `utils/prog_lang_utils.py` — programming-language tables and
  comment-token resolution
- `tags.py`, `workshop_scope.py`, `deck_markers.py` — slide tag sets, the
  workshop detector, the `no-compile` header marker
- `sidecar_layout.py` — where authoring sidecars (voiceover companions, HTTP
  cassettes) live; reads `CLM_SIDECAR_LAYOUT` and `[tool.clm]` in
  pyproject.toml
- `voiceover_companions.py` — voiceover companion path family
- `worker_identity.py` — see the inversion note below

**Slide-text model** (`core/slide_text/`, descended in #802/S5): the
percent-format `.py` slide-file model shared by the build pipeline and the
authoring extensions — `slide_parser`, `raw_cells` (lossless cell
primitives), `anchor_primitives`, `pairing` (DE/EN adjacency), and
`voiceover_merge`. The `clm.notebooks` and `clm.slides` extensions build on
top of it.

**The worker-identity inversion** (#802/S4): core operations stamp the
execution-environment identity into worker payloads at build time (it is a
cache-key component), but *computing* an identity is infrastructure work
(Docker image resolution, direct-mode binary fingerprinting).
`core/worker_identity.py` is the seam: `clm build` / `clm cache explain`
record post-override identities through
`infrastructure/workers/image_identity.py`, which also registers itself as the
fallback provider (eagerly at its own import, lazily via
`clm.infrastructure.__init__`); core reads via
`effective_worker_image_identity()` and never imports upward.

### Layer 2: `clm.infrastructure` (Engines)

**Purpose**: the engines behind the core contracts — job orchestration,
worker management, backend implementations, external-process machinery.

**Backends** (`backends/`):

- `SqliteBackend` — the one concrete production backend: turns operations
  into jobs on the SQLite queue and waits for workers to complete them
- `LocalOpsBackend` — a *partial* base handling local file operations
  (copy/delete); it leaves the `execute_operation` / `wait_for_completion`
  dispatch pair abstract, and e2e tests subclass it to fill them in
- `DummyBackend` — test-only; never instantiated in `src/` (slated to move to
  `tests/` under #802/A12)

**Database** (`database/`):

- `schema.py` — table definitions, schema versioning, and migrations. The
  canonical schema lives there, not in this document; the tables are `jobs`
  (the queue), `results_cache` (job-level skip cache), `workers`
  (registration + health), `worker_events` (lifecycle log), 
  `worker_heartbeats` (per-cell activity beacons surfaced by `clm monitor` /
  `clm status`), and `schema_version`
- `job_queue.py` — claiming (mode-tagged, session-owned), status updates
  with worker fencing, retry accounting
- `executed_notebook_cache.py` and friends — see
  [Caching Strategy](#caching-strategy)
- `journal_mode.py` — the journal-mode policy (below)

**Journal mode**: every connection to a CLM database must configure itself
through `journal_mode.configure_connection()`. Local databases get **WAL**
(readers and writers don't block each other — what makes concurrent workers
viable) with relaxed synchronous settings; databases on **network shares get
rollback journaling** with full fsync, because SQLite's WAL shared-memory
index is not coherent across machines and can corrupt the database. The
policy lives in one module because `journal_mode` is a persistent property of
the database *file* — one stray connection choosing the wrong mode would undo
the safe choice for everyone.

**Worker management** (`workers/`): `pool_manager` (start/stop/scale worker
pools), `worker_base` (the worker loop: claim, heartbeat, process, report),
`worker_executor` (spawning direct-mode processes with the right
environment), `lifecycle_manager`, `process_reaper` / orphan detection,
`image_identity` (worker-image identity fingerprinting), kernel-env
provisioning, and Windows job-object handling.

**Worker API** (`api/`): the HTTP server/client pair used in Docker mode —
loopback-bound, token-authenticated — through which containerized workers
reach the job queue and the executed-notebook cache.

**HTTP replay** (`http_replay_mitm/`): mitmproxy-based record/replay of
kernel HTTP traffic (`proxy_manager`, `http_replay_cassette`,
`cassette_format` / `vcr_format`, `trace_log`), plus `cassette_staging` for
merging worker-written staging files into canonical cassettes. **Sweeping
orphaned `*.staging-*` files is the entry points' job, not `Course`'s**
(#802/S3): `clm build` sweeps in its pre-stage hook and watch mode sweeps in
`FileEventHandler`, before core processing runs.

**Other engines**: `llm/` (OpenRouter/Ollama clients, prompt templates,
summary cache), `services/subprocess_tools.py` (subprocess execution with
timeout/crash retry — see [Subprocess crash retry](#subprocess-crash-retry)),
`error_categorizer.py` (build-error classification), `config.py`
(`ClmConfig`), `logging/`, `notebook_serialization.py`, `web_security.py`,
and `utils/` (`find_project_root`, atomic file writes, ANSI stripping).

**Why SQLite instead of a message broker?** No separate broker
infrastructure, plain SQL for monitoring, built into Python, direct file
access, easy debugging. The cost is a single-host design with lower write
concurrency — the right trade for local course building; CLM removed its
earlier RabbitMQ/FastStream architecture in v0.3.0 (see
[Migration History](#migration-history)).

### Layer 3: `clm.workers` (Worker Implementations)

**Purpose**: the four worker implementations. Each extends the
infrastructure worker base, deserializes its pinned payload schema, and runs
as `python -m clm.workers.<name>`.

| Worker | Converts | External requirements | Extra |
|---|---|---|---|
| `notebook/` | `.py` percent-format sources → executed notebooks, HTML, extracted code | Jupyter kernels for execution (see below) | `[notebook]` |
| `plantuml/` | `.puml` → PNG/SVG | Java + PlantUML JAR (`PLANTUML_JAR`) | `[plantuml]` |
| `drawio/` | `.drawio` → PNG/SVG/PDF | Draw.io desktop app (`DRAWIO_EXECUTABLE`); Xvfb when headless on Linux | `[drawio]` |
| `jupyterlite/` | built notebook trees → deployable JupyterLite static site | `uv` on PATH (build runs in an isolated `uvx` tool env) | none (not an extra) |

**Notebook worker**: multi-kernel execution (Python, C++ via xeus; C#, Java,
TypeScript templates exist but are converted unexecuted), language variants
(de/en), output kinds (code-along, completed, speaker). The course-runtime
ML/data-science stack is **not** a clm extra — it ships as
`course-runtime-requirements.txt` and belongs in a separate course venv the
Direct-mode kernel runs in (`clm provision kernel-env`), or in the Docker
worker image.

**JupyterLite worker**: site-level bundler consuming already-built notebook
output. `builder.build_site()` assembles a lite-dir
(`lite_dir.assemble_lite_dir()`), shells out to `jupyter lite build` in a
pinned `uvx` tool env, and emits a student launcher (`launch.py` or bundled
miniserve binaries), `README-offline.md`, optional branding, and a
deterministic `jupyterlite-manifest.json` for content-addressed caching.
Jobs are barrier-scheduled after the notebook-format jobs for the same
`(target, language, kind)` tuple. Opt-in per course via a `<jupyterlite>`
spec block; preview with `clm jupyterlite preview`.

**Worker execution modes**:

1. **Direct mode** (default): workers run as host subprocesses managed by the
   pool manager. Fast startup, easy debugging; requires the external tools
   and worker extras installed on the host (`pip install -e ".[all-workers]"`).
2. **Docker mode**: workers run in containers started by `clm build` via the
   Docker SDK, reaching the queue through the worker API. No host tool
   installs; better isolation; requires a Docker daemon. Build files live in
   `docker/`.

Both modes share one jobs database; claiming is mode-tagged and
session-owned so direct and Docker builds cannot steal each other's jobs.

### Top of the Stack: `clm.cli`

**Entry point**: the `clm` command (`clm.cli.main:cli`), a Click application.
`main.py` is the single manifest assembling the top level; commands load
lazily through `_lazy_group.py` (`LazyGroup`), which keeps CLI startup fast —
never re-add eager command imports.

**Command module layout** (issue #310): the file layout under
`src/clm/cli/commands/` mirrors the command tree, so finding a command's
definition is mechanical:

- `clm <cmd>` (flat) → `commands/<cmd>.py` (e.g. `clm build` → `build.py`)
- `clm <group> <cmd>` → `commands/<group>/<cmd>.py` for package groups
  (`slides/`, `course/`, `export/`, `query/`; dashes become underscores), or
  `commands/<group>.py` when the whole group is one cohesive module
  (`calendar.py`, `db.py`, `git.py`, `harvest.py`, `voiceover.py`, ...)

**Where build orchestration lives — honestly**: `clm build`'s engine is
`main_build()` in `cli/commands/build.py` (~2,800 lines; the Click command
itself starts near the end of the file). It resolves paths and options,
initializes the databases, records worker identities, starts worker pools,
sweeps orphaned cassette staging files, drives `Course.process_all()` against
a backend, and applies exit-code policy from the returned `BuildSummary`.
Extracting this into a callable core-level API — so MCP, the web studio, and
tests can run builds without going through Click — is **#802/A4, still
open**. Until it lands, programmatic builds go through the CLI layer.

**Watch mode**: `file_event_handler.py` uses `watchdog` to monitor the course
tree, debounces changes, sweeps cassette staging orphans, and triggers
incremental rebuilds via `Course.process_file()`.

**Version-accurate docs**: `cli/info_topics/*.md` back the `clm info`
command; downstream course-repo agents rely on them (see the maintenance
rule in `AGENTS.md`).

### Top of the Stack: Extension Packages

Optional, self-contained packages under `src/clm/<module>/`, installed via
extras. They may import anything below them; the constrained layers never
import them (enforced — contract 3). Entry points and purpose:

#### `clm.notebooks` (slide utilities)

Thin layer over `clm.core.slide_text` for percent-format `.py` slide files;
used by the voiceover pipeline and the `clm.slides` tools. `slide_writer`
inserts/updates notes cells in existing files; `polish` is LLM-powered notes
cleanup (requires `[summarize]`). The parsing model itself
(`slide_parser`, `raw_cells`, …) lives in `clm.core.slide_text` since
#802/S5.

#### `clm.slides` (authoring tools)

CLI-facing tooling for AI-assisted slide authoring. Powers
`clm course resolve-topic`, `clm slides search`, `clm validate`,
`clm slides normalize`, `clm slides language-view`,
`clm slides suggest-sync`, `clm slides assign-ids`, `clm slides coverage`,
`clm slides split` / `unify`, `clm slides sync`, `clm slides tidy`,
`clm voiceover extract` / `inline`, and `clm slides rules`.

Key entry points: `search.search_slides`, `spec_validator.validate_spec`,
`validator.validate_file`, `normalizer.normalize_file`,
`language_tools.get_language_view` / `suggest_sync`,
`voiceover_tools.extract_voiceover` / `inline_voiceover` /
`merge_voiceover_text` (used by the build pipeline),
`authoring_rules.get_authoring_rules`, `assign_ids.assign_ids_in_file` /
`assign_ids_in_directory`, `coverage.check_coverage_in_file` /
`check_coverage_in_directory` (LLM-driven, backed by `CoverageCache`),
`split.split_file` / `unify.unify_files` (bidirectional bilingual ↔
split-by-language converters with byte-identical round-trip),
`sync.sync_pair` (cross-language LLM-driven proposal generator with
`SyncCache` / `SyncSnapshotCache`), and `tidy.plan_tidy` / `apply_tidy`
(bulk sidecar relocation between subdirectory and flat layouts).

#### `clm.snapshot` (build-output verification harness)

Byte-level compare of two CLM build trees, exposed as
`clm build --snapshot DIR` (capture) and `clm build --verify-against DIR`
(compare) — library-only, no `clm snapshot` subcommand. Entry points:
`verify_against`, `verify_against_targets` (per-target compare for specs
with `<output-targets>`), `VerifyReport`, `normalize_for_compare`. `.html`
is skipped by default because rendered HTML contains live-kernel output;
`--include-html` opts in with hex-address normalization, `--strict-verify`
byte-compares everything. This harness underlies the golden e2e build suite
(`tests/e2e/test_e2e_golden_build.py`).

#### `clm.mcp` (Model Context Protocol server)

Exposes the `clm.slides` tools over stdio MCP transport so AI agents can
drive slide authoring. Entry points: `server.create_server(data_dir)` /
`run_server(data_dir)` and the `tools.handle_*` async handlers. Started
with `clm mcp`. Requires `[mcp]`.

#### `clm.voiceover` (video → speaker notes)

Video-to-speaker-notes pipeline used by the `clm harvest` command group
(`report`/`task`/`accept`, `autopilot`, `transcribe`/`detect`/`identify`, …).
The CLI surface moved from `clm voiceover` to `clm harvest` in the epic-#546
cutover, but the Python package keeps its `clm.voiceover` name. Pluggable
transcription backends (faster-whisper default, Cohere, Granite). Requires
`[voiceover]`.

- `transcribe` — Whisper ASR with backend Protocol
- `keyframes` — frame extraction and transition detection
- `matcher` — OCR + fuzzy matching for slide identification
- `aligner` — transcript-to-slide assignment with backtracking

#### `clm.recordings` (video recording management)

Managed video recording workflow: five-step audio pipeline
(extract → DeepFilterNet3 ONNX → FFmpeg filters → AAC → mux), pluggable
processing backends (`OnnxAudioFirstBackend`, `ExternalAudioFirstBackend`,
`AuphonicBackend` via `make_backend()`), watcher-driven automation,
per-course JSON state with slide-version provenance stamping, and an HTMX
web dashboard (`recordings.web.create_app`). `clm recordings drift`
compares stamped `slide_digest`s against the build provenance manifest.
Requires `[recordings]`.

#### `clm.release` (per-topic solution release)

Solution-release orchestration for promoting completed topics to student
cohorts, each frozen against later course edits (issue #208). Pure stdlib +
`attrs`, no extra. Cohorts are declared in the spec's `<release-channels>`
block; per-topic release state lives in a plain-text ledger
(`ledger`), per-cohort freeze records in `frozen_manifest`
(`.clm-released.<stream>.json`), and `sync` promotes released topics into
cohort repos located via the build **provenance manifest**
(`.clm-manifest.json` — written by `clm build` at each output root by
default; suppressed for `--snapshot`, `--verify-against`, `--only-sections`,
and errored builds). Driven by `clm release` (`add`/`week`/`status`/`sync`)
and `clm git --channel`.

#### `clm.cohort_calendar` (cohort viewing calendar)

Projects a course's certification schedule (the course-relative plan from
`clm export schedule`) onto a cohort's real calendar dates (issue #283) —
`projection`, `render`, `status`, and `google_sync` for Google Calendar
push. Driven by the `clm calendar` command group.

#### `clm.web` (web studio)

FastAPI application behind `clm serve`: the `/studio/` deck editor
(token-authenticated Studio API + WebSocket, watchdog-driven live reload)
for browser- and mobile-based slide editing. Requires `[web]`.

## Build Flow

What actually happens on `clm build course.xml` (orchestrated by
`main_build()` in `cli/commands/build.py` — see the CLI section):

```
clm build course.xml
  │
  ├─ parse spec → CourseSpec → Course.from_spec()   (core)
  ├─ init jobs DB + cache DB, configure journal mode (infrastructure)
  ├─ record worker-image identities                  (infrastructure → core registry)
  ├─ start worker pools (direct subprocesses or Docker containers)
  ├─ sweep orphaned cassette staging files           (entry point's job)
  │
  ├─ Course.process_all(backend)                     (core, against Backend)
  │    for each execution stage:
  │      for each output target:
  │        for each file: get_processing_operation() → Operation
  │          └─ operation.execute(backend)
  │               ├─ SqliteBackend: enqueue job (SQLite, status=pending)
  │               │    workers claim (mode-tagged, session-owned),
  │               │    read input from disk, process, write output,
  │               │    report completed/failed + results_cache entry
  │               └─ local ops (copy/delete): LocalOpsBackend directly
  │      wait_for_completion() barrier between stages
  │
  └─ BuildSummary → exit-code policy, provenance manifest, reports
```

Stages order execution so outputs that reuse cached execution run after the
outputs that populate the cache (see
[Multiple Output Targets](#multiple-output-targets)). Workers communicate
results through the jobs DB (direct mode) or the worker API (Docker mode);
file content itself always moves through the filesystem, never through the
queue.

## Caching Strategy

> **Canonical reference: [`caching.md`](caching.md).** The summary below is a
> quick orientation; the full picture — the *three* caches, their distinct keys,
> what invalidates them, the retention policy, and the interactions that have
> produced real bugs (#321, #577, #579, #580) — lives in the caching guide.

**Content-based caching**: each file's content is hashed (SHA-256), folding in
the template fingerprint (incl. the clm version), the worker-image identity,
and sibling files. Cache hit → skip processing; miss → process and store.

**Three caches across two DB files**:

- `results_cache` (`clm_jobs.db`) — the job-level scheduling short-circuit,
  keyed `(output_file, content_hash)`
- `processed_files` (`clm_cache.db`) — the operation result cache, keyed
  `(file_path, content_hash, output_metadata)`
- `executed_notebooks` (`clm_cache.db`) — the execution cache, keyed by the
  kind-agnostic `execution_cache_hash()`; Recording/Speaker HTML is its
  producer

Diagnose unexpected rebuilds with `clm cache explain <deck> --spec <course.xml>`.

## Configuration

**Canonical reference: [`configuration.md`](../user-guide/configuration.md)**
— the complete environment-variable and config-file reference. Do not trust
ad-hoc env-var lists elsewhere (including in old revisions of this document).

Known non-uniformity, tracked in #802:

- **Three parallel config mechanisms** (A7, open): the Pydantic `ClmConfig`
  hierarchy (`infrastructure/config.py`), `core/sidecar_layout.py`'s own
  `[tool.clm]` pyproject reader, and raw `os.environ` reads scattered through
  the codebase (including `build.py`'s `_resolve_*` family).
- **The jobs-DB path has two env names and two defaults** (A8, open): host
  code uses `CLM_JOBS_DB_PATH` (default `clm_jobs.db`); worker code uses bare
  `DB_PATH` with the container default `/db/jobs.db`. It works because the
  worker executor always injects the value — any other spawn path would
  silently poll an empty queue.

## Testing Strategy

**Canonical reference: [`testing.md`](testing.md)** — the full marker list and
strategy. Quick orientation:

- `pytest` — fast suite (~72s, runs on the pre-push hook); excludes `slow`,
  `integration`, `e2e`, `db_only`, and `docker`
- `pytest -m "not docker"` — pre-release local gate
- `pytest -m ""` — everything (Docker tests are CI-validated only)

Architecture-relevant suites:

- `tests/test_architecture_contracts.py` — the string-import guard, the
  `Backend` surface pin, and the worker payload schema pins (see
  [enforcement](#how-the-layering-is-enforced))
- `tests/e2e/test_e2e_golden_build.py` — golden double-build byte-identity
  over the reference specs, on the `--snapshot`/`--verify-against` harness;
  the refactor safety net for any pipeline change
- `tests/build/test_pipeline_unmocked.py` — real `Course` + real
  `SqliteBackend` + temp DB in the fast suite: stage flow and a real worker
  round-tripping a job through the queue

## Multiple Output Targets

Courses can define multiple output directories with selective content
generation (delayed solution release, language-specific distributions,
instructor packages).

- `OutputTargetSpec` (`core/course_spec.py`) parses `<output-target>` XML
  elements: name, path, and optional `kinds` / `formats` / `languages`
  filters (`None` means "all" — backward compatible).
- `OutputTarget` (`core/output_target.py`) is the runtime form: resolved
  absolute root plus `frozenset` filters with `should_generate(lang, fmt,
  kind)`.
- `ExecutionDependencyResolver` (`core/execution_dependencies.py`) handles
  outputs that depend on cached execution from *other* outputs: HTML
  `completed` reuses the execution cache populated by HTML `speaker`, so a
  target requesting only `completed` gets an **implicit** speaker execution
  (cache-populating, output-suppressed). The `EXECUTION_REQUIREMENTS` /
  `CACHE_PROVIDERS` tables in that module are the authoritative statement of
  which `(format, kind)` pairs populate or reuse the cache.

The processing loop in `Course.process_all()` iterates stages × targets ×
files, filtering each file's output specs through the target. CLI:
`clm build course.xml --targets students,solutions` selects targets;
`clm course targets course.xml` lists them; `--output-dir` overrides the
spec's targets with a single default target.

## Operational Lessons

Hard-won constraints that shape the code; kept here because each one cost a
real debugging session.

### Signal handling and asyncio cleanup

The CLI registers SIGINT/SIGTERM handlers that raise `KeyboardInterrupt`. A
signal arriving *during* `asyncio.run()`'s cleanup — after handlers are
restored but before the call returns — surfaces as a spurious
`KeyboardInterrupt` after a successful build. `build.py` therefore tracks
build completion and suppresses a late `KeyboardInterrupt` when the build
already succeeded. Relatedly, **never log from a signal handler**: handlers
can interrupt the logging system mid-write and trigger
`RuntimeError: reentrant call inside <_io.BufferedWriter>`. Handlers set
flags and raise; logging happens in the cleanup code that runs afterward.

### Worker orphan detection

Workers store their parent PID at startup, poll parent liveness (signal-0
check, cross-platform), and exit gracefully when the parent dies; `atexit`
handlers provide emergency cleanup, and the `workers` table tracks
`parent_pid` for diagnostics. Without this, a crashed `clm build` left
workers running indefinitely.

### Subprocess crash retry

Electron-based Draw.io can crash transiently (V8 garbage-collection races).
`infrastructure/services/subprocess_tools.py` provides `RetryConfig` +
`run_subprocess`: timeouts always retry with doubling timeout;
non-zero exits retry only when `retry_on_crash=True` (the Draw.io converter
enables it: 3 attempts, 2s delay); `FileNotFoundError`/`PermissionError`
never retry. Default is `retry_on_crash=False`, preserving
non-raising behavior for ordinary non-zero exits.

## Migration History

CLM has evolved through three architectures:

- **v0.1.x–v0.2.x**: four separate packages, RabbitMQ + FastStream broker,
  Prometheus/Grafana monitoring, message serialization overhead.
- **v0.3.x** (Nov 2025): single unified package, SQLite job queue, direct
  file access, no broker; 8 Docker services reduced to 3.
- **v0.6.x onward**: workers integrated into the main package
  (`clm.workers`) behind optional extras. The `[ml]` extra that once pulled
  the course-runtime stack was removed (Wave 2b) in favor of
  `course-runtime-requirements.txt` + a separate course venv.
- **v1.25.x / #802** (Aug 2026): the four-layer import structure made real —
  contract seam descended into `clm.core`, cassette sweeping moved to the
  entry points, worker identity inverted, slide-text model descended, and the
  whole stack put under import-linter contracts (PRs #804–#813).

Details: `docs/archive/migration-history/`.

## Known Deviations and Pending Work

The layering above is fully enforced, but #802 tracks remaining structural
work. Keep this list honest — update it when an item lands:

- **A4** — build orchestration still lives in `cli/commands/build.py`;
  extraction into a callable core API is the next step (unblocked by the
  contract descent)
- **A5** — voiceover merge/propagation logic still in
  `cli/commands/voiceover.py` (with private-symbol imports) instead of
  `clm.voiceover`
- **A7 / A8** — config unification and the jobs-DB path split (see
  [Configuration](#configuration))
- **A9** — ~12 cross-module underscore-private imports remain among the
  unconstrained top-of-stack modules
- **A12** — `docker`, `fastapi`, `uvicorn`, `watchdog` are unconditional
  dependencies; `DummyBackend` ships in the package though only tests use it

## References

- **AGENTS.md** (imported by `CLAUDE.md`) — session-start orientation for AI
  assistants
- **`clm info commands`** / **`clm info spec-files`** — version-accurate CLI
  and spec references
- `pyproject.toml` `[tool.importlinter]` — the enforced layer contracts
- `docs/claude/design/phase8-a1-a3-core-decoupling.md` — the re-layering
  design the moves followed
- `docs/claude/adversarial-review-2026-07-24.md` — the review that found the
  previous revision of this document describing an architecture that did not
  exist
- **Migration History** — `docs/archive/migration-history/`
- **Source Code** — `src/clm/`

---

**Last Updated**: 2026-08-06
**Version**: 1.25.0
