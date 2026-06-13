# CLM — Agent Orientation

This file is written for AI coding agents that need to understand the **CLM (Coding-Academy Lecture Manager)** codebase quickly. It assumes no prior knowledge of the project and focuses on the facts that are expensive to reconstruct from scattered files.

## Project overview

CLM is a Python course-content processing system. It converts educational source materials — Python/C++/C#/Java/TypeScript notebook sources (`.py`), PlantUML diagrams (`.puml`), and Draw.io diagrams (`.drawio`) — into multiple output formats (HTML slides, executed Jupyter notebooks, extracted source code) for multiple audiences (students, solutions, instructors).

- **Package name on PyPI**: `coding-academy-lecture-manager`
- **Version**: `1.13.0` (canonical source: `src/clm/__version__.py`)
- **License**: MIT
- **Python support**: `>=3.12` (CI tests 3.12 and 3.13; 3.14 is listed)
- **Repository**: https://github.com/hoelzl/clm
- **Entry point**: the `clm` CLI, implemented at `clm.cli.main:cli`

## Technology stack

- **Build backend**: `hatchling` (configured in `pyproject.toml`)
- **Package manager of choice**: `uv` (`.venv`, `uv.lock`, `[dependency-groups] dev`)
- **CLI framework**: `click`, with lazy-loaded command groups (`clm.cli._lazy_group`)
- **Core libraries**: `pydantic` / `pydantic-settings`, `attrs`, `loguru`, `rich`, `httpx`, `fastapi`, `uvicorn`, `watchdog`
- **Type checking**: `mypy`
- **Linting / formatting**: `ruff`
- **Testing**: `pytest`, `pytest-asyncio`, `pytest-xdist`, `pytest-timeout`, `pytest-cov`, `hypothesis`
- **Pre-commit**: `pre-commit` with two hook stages (`pre-commit` and `pre-push`)
- **Job orchestration**: SQLite (no message broker), content-addressed result cache
- **Workers**: subprocess/direct mode or Docker containers
- **CI / release**: GitHub Actions (`ci.yml`, `release.yml`) with PyPI Trusted Publishing (OIDC)

## Code organization

The source lives under `src/clm/` in a four-layer architecture. Optional extensions sit alongside the core.

```
src/clm/
├── core/                  # Domain logic (no infrastructure dependencies)
│   ├── course.py, section.py, topic.py, course_file.py, course_spec.py
│   ├── course_files/      # NotebookFile, PlantUmlFile, DrawioFile, DataFile, ImageFile, …
│   ├── operations/        # Build operations (process_notebook, convert_plantuml, …)
│   └── utils/             # Notebook, text, execution helpers
├── infrastructure/        # Runtime support
│   ├── backends/          # SQLite backend, local-ops backend, dummy backend
│   ├── database/          # Schema, job queue, cache, worker events, telemetry
│   ├── llm/               # LLM client, prompts, summary cache
│   ├── messaging/         # Job/result payloads
│   ├── services/          # Subprocess tooling, service registry
│   ├── workers/           # WorkerBase, pool manager, executor, lifecycle
│   └── http_replay_mitm/  # Out-of-process HTTP replay transport (mitmproxy)
├── workers/               # Concrete worker implementations
│   ├── notebook/          # Jupyter notebook execution/conversion
│   ├── plantuml/          # PlantUML → PNG/SVG
│   ├── drawio/            # Draw.io → PNG/SVG/PDF
│   └── jupyterlite/       # Browser-based JupyterLite site builder
├── notebooks/             # Slide source parsing / writing / polishing
├── slides/                # Slide authoring tools (sync, validate, normalize, split/unify)
├── voiceover/             # Video-to-speaker-notes pipeline
├── recordings/            # Recording workflow (local ONNX, Auphonic cloud, OBS)
├── mcp/                   # Model Context Protocol server for AI-assisted authoring
├── cohort_calendar/       # Cohort viewing calendar rendering and Google Calendar push
├── web/                   # Optional web dashboard / API
├── cli/                   # Click-based command-line interface
│   ├── main.py            # Top-level `clm` group + lazy dispatch
│   ├── commands/          # One module per command/group
│   └── info_topics/       # Version-accurate docs for `clm info <topic>`
└── utils/                 # Shared utilities
```

Tests mirror the source layout under `tests/`. Important test directories:

```
tests/
├── core/                  # Core domain tests
├── infrastructure/        # Backends, workers, database
├── cli/                   # CLI tests
├── workers/               # Worker tests
├── notebooks/             # Slide parser/writer/polish tests
├── recordings/            # Recording workflow tests
├── voiceover/             # Voiceover pipeline tests
├── slides/                # Slide authoring tests
├── e2e/                   # End-to-end course builds
├── integration/           # Integration tests
└── conftest.py            # Shared fixtures and pytest configuration
```

## Build and installation commands

The project is built with `hatchling` and developed with `uv`:

```bash
# Clone and enter the repo
git clone https://github.com/hoelzl/clm.git
cd clm

# Recommended: install with uv in editable mode + all dev/optional dependencies
uv sync                       # installs the [dependency-groups] dev group automatically

# Or with pip
pip install -e ".[all]"

# Verify
clm --help
```

Important optional dependency groups in `pyproject.toml`:

- `[notebook]`, `[plantuml]`, `[drawio]` — worker-specific dependencies
- `[all-workers]` — all three workers
- `[recordings]`, `[voiceover]`, `[slides]`, `[summarize]` — extended features
- `[mcp]` — MCP server
- `[gcal]` — Google Calendar push
- `[replay]` — out-of-process HTTP replay (mitmproxy, PyYAML, filelock)
- `[jupyterlite]` — JupyterLite site builder
- `[ml]` — PyTorch / FastAI / transformers stack (heavy, excluded from CI tests)
- `[dev]` — pytest, ruff, mypy, pre-commit, etc.
- `[tui]`, `[web]` — optional TUI / web dashboard
- `[all]` — everything except `[ml]` is intentionally omitted because it is huge

The `dev` dependency group is auto-synced by `uv sync` / `uv run` and mirrors `[project.optional-dependencies] dev` plus the extras needed for the fast test suite.

### Useful commands

```bash
# Run linting and formatting
uv run ruff check src/ tests/
uv run ruff format src/ tests/

# Run type checking
uv run mypy src/clm

# Run the fast test suite (default pytest excludes slow/integration/e2e/docker)
pytest

# Run the full local suite excluding Docker tests
pytest -m "not docker"

# Run all tests including Docker tests
pytest -m ""

# Build Docker worker images
./build-services.sh              # all workers (notebook lite + full, plantuml, drawio)
./build-services.sh notebook:lite
./build-services.sh plantuml

# Quick rebuild using cached stages (after clm docker build --cache-stages)
clm docker build-quick
```

## Runtime architecture

CLM uses an SQLite-based job queue and worker pools. There is no RabbitMQ/Kafka-style message broker.

1. `clm build course.xml` parses the spec, scans files, and creates jobs.
2. Each job is hashed (SHA-256) and checked against the SQLite `results_cache`.
3. Cache misses are inserted into the `jobs` table with status `pending`.
4. Workers poll the queue, read input files directly from disk, process them, and write outputs.
5. Completed jobs update the cache; failed jobs are retried up to `max_attempts`.

Worker execution modes:

- **Direct mode** (default for local dev): workers run as OS subprocesses. Requires external tools:
  - Java + PlantUML JAR (`PLANTUML_JAR`)
  - Draw.io desktop app (`DRAWIO_EXECUTABLE`)
- **Docker mode** (isolated, CI): workers run in containers built from `docker/notebook/`, `docker/plantuml/`, `docker/drawio/`.

Key databases:

- `clm_cache.db` — content-addressed result cache
- `clm_jobs.db` — job queue, worker registry, lifecycle events
- `clm_telemetry.db` — per-deck kernel crash/flake history (optional, default next to cache DB)

The SQLite journal mode is `DELETE` (not WAL) for cross-platform compatibility with Docker volume mounts on Windows.

## Development conventions

### Code style

- **Formatter / linter**: `ruff` (line length 100, target Python 3.10 syntax in ruff config, runtime requires 3.12+)
- **Type checker**: `mypy` with `disallow_untyped_defs = false` in general, but type hints are required on public APIs
- **Import sorting**: `ruff` isort; `clm` is marked as first-party
- **Internal data structures**: `attrs @define`
- **Messages crossing CLI/worker boundaries**: `pydantic`
- **Logging**: use `logging.getLogger(__name__)`; never `print()` in library code
- **Async**: prefer `async`/`await` for I/O operations
- **Scripts**: prefer Python over bash; CLM is Windows-first and Python scripts run identically everywhere

### Pre-commit hooks

Install with `uv run pre-commit install`. This wires up two stages:

- **pre-commit**: `ruff check --fix`, `ruff format`, `mypy src/clm` (~3–5s)
- **pre-push**: fast pytest suite via `scripts/run_pytest_hook.py` (~72s, capped at 16 xdist workers)

The fast suite excludes `slow`, `db_only`, `integration`, `e2e`, and `docker` tests.

### Git workflow

- AI-generated branches: prefix with `claude/`
- `master` is checked out in the main repo; **never switch a worktree to `master`**. To refresh a worktree on latest master content:
  ```bash
  git fetch origin && git switch -C <worktree-branch> origin/master
  ```
- Commit, push, and open PRs autonomously for non-destructive changes
- If a hook rejects a commit, the commit did **not** happen — create a **new** commit; do not `--amend`
- Branch naming from `CONTRIBUTING.md`:
  - `feature/description`
  - `fix/description`
  - `docs/description`
  - `test/description`

### Documentation maintenance

When you change behavior that affects course repositories or the CLI, update the canonical version-accurate info topics consumed by `clm info <topic>`:

| Change affects | Update |
|---|---|
| Spec file format | `src/clm/cli/info_topics/spec-files.md` |
| CLI commands/options | `src/clm/cli/info_topics/commands.md` |
| Breaking changes / migrations | `src/clm/cli/info_topics/migration.md` |

Use `{version}` placeholders in info topics; do not hardcode version numbers.

### Changelog

Do **not** edit `CHANGELOG.md`'s `[Unreleased]` section in a PR. Instead, add a fragment:

```bash
changelog.d/<pr-or-issue>-<slug>.<type>.md
```

where `<type>` is one of: `added`, `changed`, `deprecated`, `removed`, `fixed`, `security`.
Collect fragments at release time with `python scripts/collect_changelog.py X.Y.Z`.

## Testing instructions

Pytest configuration lives in `pyproject.toml` `[tool.pytest.ini_options]`.

Default `addopts`:

```toml
addopts = "-n auto --dist loadgroup -m 'not slow and not db_only and not integration and not e2e and not docker'"
```

So `pytest` runs the fast unit suite in parallel via `pytest-xdist`, using `--dist loadgroup` so tests marked `serial` stay on a single worker.

### Test markers

| Marker | Meaning |
|---|---|
| (no marker) | Fast unit tests |
| `@pytest.mark.slow` | Long-running tests (excluded everywhere by default) |
| `@pytest.mark.db_only` | Tests that use a real database but not real workers |
| `@pytest.mark.integration` | Tests requiring full worker setup / real subprocesses |
| `@pytest.mark.e2e` | Full end-to-end course conversions |
| `@pytest.mark.docker` | Tests requiring Docker daemon (CI-only in the `docker-test` job) |
| `@pytest.mark.recordings` | Recording-module tests |
| `@pytest.mark.serial` | Pin contention-prone tests to one xdist worker group |

### Common test commands

```bash
pytest                                  # fast unit tests (~72s)
pytest -m "not docker"                  # unit + integration + e2e, no Docker
pytest -m integration                   # only integration tests
pytest -m e2e                           # only e2e tests
pytest -m ""                            # everything (very slow; Docker tests need CI images)
pytest -n0                              # disable parallelism
pytest --cov=src/clm                    # with coverage
```

### Notes on markers

- `slow` is not run automatically anywhere; do not use it to mean "needs a real subprocess" — use `integration` for that.
- `serial` is for tests that contend on global resources (fixed ports, shared daemons, registration tables). It maps to a shared `xdist_group` in `tests/conftest.py`.
- The `replay` extra is included in the auto-synced `dev` group so HTTP-replay/cassette tests run in the fast suite; without it they `pytest.importorskip`-skip.

## Deployment and release process

Publishing is automated. The rules are:

1. **Never publish without updated documentation first** (CHANGELOG, README, info topics, developer/user guides as needed).
2. **Never publish if any local test fails.**
3. **Never publish unless CI has passed for the tagged commit.**

### Release steps

1. Update docs and collect changelog fragments:
   ```bash
   python scripts/collect_changelog.py X.Y.Z   # --dry-run to preview
   ```
2. Run local tests:
   ```bash
   uv run pytest -m "not docker"
   ```
3. Bump the version:
   ```bash
   uv run bump-my-version bump patch   # or minor / major
   ```
   This commits `Bump version X.Y.Z → A.B.C` across `src/clm/__version__.py`, `pyproject.toml`, `README.md`, `CLAUDE.md`, `docs/developer-guide/architecture.md`, `docker/BUILDING.md`, `tests/workers/notebook/test_notebook_error_context.py`, and `uv.lock`. It does **not** create a local tag.
4. Push the bump commit to `master` (or merge via PR with a **merge commit**, not squash/rebase).
5. The `release.yml` workflow:
   - detects the bump commit,
   - waits for the `CI` workflow to be green,
   - creates and pushes the `vX.Y.Z` tag,
   - builds the wheel/sdist,
   - publishes to PyPI via OIDC Trusted Publishing,
   - creates the GitHub Release from the matching CHANGELOG section.

### Version management

Version strings are managed by `bump-my-version` (`[tool.bumpversion]` in `pyproject.toml`). The authoritative source is `src/clm/__version__.py`.

## Security considerations

- **Supply-chain safety**: `pyproject.toml` sets `[tool.uv].exclude-newer = "2026-05-28"` (updated via `scripts/update_exclude_newer.py`). PyTorch packages (`torch`, `torchvision`, `torchaudio`) are exempt because their CUDA index does not provide upload dates.
- **No secrets in source**: API keys (OpenAI, OpenRouter, Auphonic, Google, GitLab) are read from environment variables or config files; do not hardcode them.
- **HTTP replay isolation**: the `replay` extra uses an out-of-process `mitmproxy` (`mitmdump`) transport. The mitmproxy addon code is loaded by mitmproxy's own interpreter, not CLM's venv, and is intentionally absent from the main dependency set.
- **Trusted Publishing**: PyPI releases use GitHub OIDC, not long-lived API tokens.
- **Git token auth**: `CLM_GIT_TOKEN_AUTH=1` enables an ephemeral credential helper for HTTPS git operations; the token never appears in URLs, `.git/config`, or command lines.
- **Pre-commit hardening**: the pre-push hook clears leaking `GIT_*` environment variables before invoking pytest (`scripts/run_pytest_hook.py`).

## Environment and configuration

CLM loads settings from TOML config files and environment variables (Pydantic Settings). Priority (highest first):

1. Environment variables (`CLM_*`)
2. Project config (`.clm/config.toml` or `clm.toml`)
3. User config (`~/.config/clm/config.toml` / `%APPDATA%\clm\config.toml`)
4. System config (`/etc/clm/config.toml`)
5. Defaults

Key environment variables to know:

| Variable | Purpose |
|---|---|
| `CLM_PATHS__CACHE_DB_PATH` | Cache DB path (default `clm_cache.db`) |
| `CLM_PATHS__JOBS_DB_PATH` | Jobs DB path (default `clm_jobs.db`) |
| `PLANTUML_JAR` | Path to PlantUML JAR |
| `DRAWIO_EXECUTABLE` | Path to Draw.io executable |
| `CLM_LOG_LEVEL` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `CLM_ENABLE_TEST_LOGGING` | Enable logging during tests |
| `CLM_MAX_CONCURRENCY` | Max concurrent operations (default 50) |
| `CLM_MAX_WORKER_STARTUP_CONCURRENCY` | Max concurrent worker starts (default 10) |
| `CLM_HTTP_REPLAY_MODE` | Replay mode: `replay`, `once`, `new-episodes`, `refresh`, `disabled` |
| `CLM_CELL_TIMEOUT_SECONDS` / `CLM_HTTP_REPLAY_CELL_TIMEOUT_SECONDS` | Per-cell notebook timeouts |
| `CLM_RETENTION__*` | Database retention settings |
| `CLM_LLM__*` | LLM provider settings for `clm export summary` / `clm polish` |
| `CLM_GIT__REMOTE_TEMPLATE` | Git remote URL template |
| `CLM_GITLAB_TOKEN` / `CLM_GIT_TOKEN_AUTH` | GitLab API token / HTTPS git auth |
| `CLM_GOOGLE_CREDENTIALS` | Google credentials JSON for `clm calendar push` |

See `docs/user-guide/configuration.md` for the full list.

## Documentation map

When looking for authoritative information, prefer the canonical home:

| Topic | Location |
|---|---|
| CLI commands, flags, options (version-accurate) | `clm info commands` |
| Course spec XML format (version-accurate) | `clm info spec-files` |
| Breaking-change migrations | `clm info migration` |
| System architecture | `docs/developer-guide/architecture.md` |
| Testing strategy / test markers | `docs/developer-guide/testing.md` |
| Release procedure | `docs/developer-guide/releasing.md` |
| Docker image builds | `docker/BUILDING.md` |
| Installation & optional extras | `docs/user-guide/installation.md` |
| Configuration & env vars | `docs/user-guide/configuration.md` |
| Voiceover pipeline | `docs/user-guide/voiceover.md` |
| Recording workflow | `docs/user-guide/recordings.md` |
| Changelog fragments convention | `changelog.d/README.md` |
| Known issues / TODOs | `docs/claude/TODO.md` |
| AI assistant guardrails (session start) | `CLAUDE.md` |

---

Last updated: 2026-06-13
