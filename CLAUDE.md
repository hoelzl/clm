# CLM - AI Assistant Guide

This document provides essential information about the CLM (Coding-Academy Lecture Manager eXperimental) codebase for AI assistants.

## Project Overview

**CLM** is a course content processing system that converts educational materials (Jupyter notebooks, PlantUML diagrams, Draw.io diagrams) into multiple output formats.

**Version**: 1.1.1 | **License**: MIT | **Python**: 3.11, 3.12, 3.13, 3.14

## Architecture

CLM uses a clean four-layer architecture with SQLite job queue and Direct/Docker worker execution:

```
clm/
├── core/           # Domain logic (Course, Section, Topic, CourseFile)
├── infrastructure/ # Job queue, worker management, backends
├── workers/        # Worker implementations (notebook, plantuml, drawio)
└── cli/            # Command-line interface
```

## Installation

```bash
# Core package only
pip install -e .

# With all workers (for direct execution mode)
pip install -e ".[all-workers,dev]"

# Everything (recommended for development/testing)
pip install -e ".[all]"
```

**Optional Dependencies**:
- `[notebook]`: Notebook processing worker
- `[plantuml]`: PlantUML conversion worker
- `[drawio]`: Draw.io conversion worker
- `[all-workers]`: All worker dependencies
- `[ml]`: ML/LLM packages (PyTorch, FastAI, LangChain, OpenAI, etc.)
- `[dev]`: Development tools (pytest, mypy, ruff)
- `[tui]`: TUI monitoring (`clm monitor`)
- `[web]`: Web dashboard (`clm serve`)
- `[all]`: All of the above

## Key Commands

```bash
clm build <course.yaml>         # Build/convert course
clm build --watch <course.yaml> # Watch mode with auto-rebuild
clm status                      # Show system status
clm info [topic]                # Show version-accurate docs (spec-files, commands, migration)
clm workers list                # List registered workers
clm docker list                 # List available Docker images
clm docker pull                 # Pull Docker images from Hub
clm monitor                     # TUI monitoring (requires [tui])
clm serve                       # Web dashboard (requires [web])
```

## Testing

```bash
pytest                # Fast unit tests only (default)
pytest -m integration # Include integration tests
pytest -m e2e         # Include e2e tests
pytest -m ""          # Run ALL tests
```

**Test Markers**:
- `@pytest.mark.integration`: Real workers, requires external tools
- `@pytest.mark.e2e`: Full course conversion
- `@pytest.mark.requires_plantuml`: Requires PlantUML JAR and Java
- `@pytest.mark.requires_drawio`: Requires DrawIO executable

## Repository Structure

```
clm/
├── src/clm/                    # CLM package source (v1.1.1)
│   ├── core/                   # Domain logic
│   │   ├── course.py           # Main Course class
│   │   ├── course_file.py      # Base file class
│   │   ├── course_spec.py      # Course specification parsing
│   │   ├── output_target.py    # Multiple output targets support
│   │   ├── course_files/       # File type handlers
│   │   └── operations/         # File operations
│   ├── infrastructure/         # Runtime support
│   │   ├── backends/           # SqliteBackend, LocalOpsBackend
│   │   ├── database/           # SQLite job queue
│   │   ├── messaging/          # Pydantic payloads/results
│   │   └── workers/            # Worker management
│   ├── workers/                # Worker implementations (v1.1.1)
│   │   ├── notebook/           # Notebook processing
│   │   ├── plantuml/           # PlantUML conversion
│   │   └── drawio/             # Draw.io conversion
│   └── cli/                    # Click-based CLI
│       └── info_topics/        # Markdown docs for `clm info` command
├── tests/                      # All tests
│   ├── core/                   # Core module tests
│   ├── infrastructure/         # Infrastructure tests
│   ├── cli/                    # CLI tests
│   └── e2e/                    # End-to-end tests
├── docs/                       # Documentation
│   ├── user-guide/             # User documentation
│   ├── developer-guide/        # Developer documentation
│   └── claude/                 # AI assistant working documents
└── pyproject.toml              # Package configuration
```

## Key Classes

### Core

- `Course` - Main course representation (`core/course.py`)
- `Section` - Course section (`core/section.py`)
- `Topic` - Individual topic (`core/topic.py`)
- `CourseFile` - Base class for all file types (`core/course_file.py`)
- `NotebookFile`, `PlantUmlFile`, `DrawioFile` - Concrete file handlers
- `OutputTarget` - Output directory configuration (`core/output_target.py`)

### Infrastructure

- `Backend` - Backend interface (`infrastructure/backend.py`)
- `SqliteBackend` - Primary backend (`infrastructure/backends/sqlite_backend.py`)
- `JobQueue` - Job queue operations (`infrastructure/database/job_queue.py`)
- `WorkerBase` - Abstract worker class (`infrastructure/workers/worker_base.py`)
- `PoolManager` - Worker pool management (`infrastructure/workers/pool_manager.py`)
- `ClmConfig` - Main config with pydantic-settings (`infrastructure/config.py`)
- `GitConfig` - Git remote template config (`infrastructure/config.py`)
- `run_subprocess` - Subprocess execution with retry (`infrastructure/services/subprocess_tools.py`)
- `RetryConfig` - Configurable retry behavior for subprocesses

### Workers

- `NotebookWorker` - Entry point: `python -m clm.workers.notebook`
- `PlantUmlWorker` - Entry point: `python -m clm.workers.plantuml`
- `DrawioWorker` - Entry point: `python -m clm.workers.drawio`

## Import Examples

```python
# Convenience imports
from clm import Course, Section, Topic, CourseFile, CourseSpec

# Explicit imports
from clm.core import Course, Section, Topic
from clm.core.course_files import NotebookFile, PlantUmlFile, DrawioFile
from clm.core.output_target import OutputTarget
from clm.infrastructure.backends import SqliteBackend
from clm.infrastructure.database import JobQueue
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `PLANTUML_JAR` | Path to PlantUML JAR file |
| `DRAWIO_EXECUTABLE` | Path to Draw.io executable |
| `LOG_LEVEL` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `CLM_MAX_CONCURRENCY` | Max concurrent operations (default: 50) |
| `CLM_GIT__REMOTE_TEMPLATE` | Git remote URL template (e.g., `git@github.com-cam:Org/{repo}.git`) |

## Recent Features

### Git Amend and Force Push (v1.1.1+)

The `clm git` commands support `--amend` and `--force-with-lease` for iterative
workflows (e.g., tweaking slides during dry-runs):

```bash
clm git commit <spec> --amend              # Amend, keep previous message
clm git commit <spec> --amend -m "new msg" # Amend with new message
clm git push <spec> --force-with-lease     # Safe force push
clm git sync <spec> --amend               # Amend + force push (one step)
clm git sync <spec> --force-with-lease -m "msg"  # Normal commit + force push
```

- `-m` is optional when `--amend` is used (reuses previous message via `--no-edit`)
- `--amend` on `sync` implies `--force-with-lease` and skips the "remote is ahead" check
- Without `-m` or `--amend`, `commit` and `sync` produce a usage error

### Git Remote URL Template (v1.1.1+)

The git remote URL can be customized via a template with placeholders:

```bash
# Via environment variable or .env file
CLM_GIT__REMOTE_TEMPLATE="git@github.com-cam:Coding-Academy-Munich/{repo}.git"
```

Available placeholders: `{repository_base}`, `{repo}`, `{slug}`, `{lang}`, `{suffix}`.
Can also be set in TOML config (`[git] remote_template`) or course spec XML (`<remote-template>`).

### `clm info` Command (v1.1.1)

Version-accurate documentation for agents and users. Topics live in `src/clm/cli/info_topics/*.md`
and use `{version}` placeholders replaced at output time.

### Multiple Output Targets (v0.4.x)

Courses can define multiple output directories with selective content generation:

```xml
<output-targets>
    <output-target name="students">
        <path>./output/students</path>
        <kinds><kind>code-along</kind></kinds>
        <formats><format>html</format><format>notebook</format></formats>
    </output-target>
    <output-target name="solutions">
        <path>./output/solutions</path>
        <kinds><kind>completed</kind></kinds>
    </output-target>
</output-targets>
```

### Shared Image Storage

Images are now stored once and symlinked/copied to output directories, eliminating duplication.

### Output Kinds and Formats

| Kind | Description |
|------|-------------|
| `code-along` | Notebooks with code cells cleared |
| `completed` | Notebooks with all solutions |
| `speaker` | Notebooks with speaker notes |

| Format | Description |
|--------|-------------|
| `html` | HTML slides |
| `notebook` | Jupyter notebook (.ipynb) |
| `code` | Extracted source code |

## Worker Execution Modes

1. **Direct Mode** (Default): Workers run as subprocesses, faster for development
2. **Docker Mode**: Workers run in containers, better isolation

## Database Architecture

Two separate SQLite databases:
- `clm_jobs.db` - Job queue (jobs, workers, events, results_cache)
- `clm_cache.db` - Cache (processed_files with pickled results)

Uses DELETE journal mode (not WAL) for cross-platform Docker compatibility.

## Code Conventions

- **Type hints**: Required for all public APIs
- **Async/await**: Preferred for I/O operations
- **Dataclasses**: Use attrs `@define` for internal structures, Pydantic for messages
- **Logging**: Use `logging.getLogger(__name__)`

## Documentation

| Document | Location | Purpose |
|----------|----------|---------|
| User Guide | `docs/user-guide/` | End-user documentation |
| Developer Guide | `docs/developer-guide/` | Development documentation |
| Spec File Reference | `docs/user-guide/spec-file-reference.md` | Course XML format |
| Architecture | `docs/developer-guide/architecture.md` | System design |
| Known Issues | `docs/claude/TODO.md` | Bugs and planned improvements |

### Agent-Facing Info Topics (`clm info`)

The `clm info <topic>` command outputs version-accurate markdown documentation
that agents in downstream course repositories use to understand CLM's current
behavior. The content lives in `src/clm/cli/info_topics/*.md`.

**When you change CLM behavior that affects how course repositories are structured
or built, you MUST update the corresponding info topic.** Examples:

- Spec file format changes (new/renamed/removed elements, attribute changes) → update `spec-files.md`
- CLI command changes (new commands, changed options, removed flags) → update `commands.md`
- Breaking changes or migration steps → update `migration.md`

Downstream agents run `clm info spec-files` to learn the current spec format
before editing course XML files. If the info topics are stale, those agents will
produce incorrect output. Keeping these files current is as important as updating
tests.

The files use `{version}` placeholders that are replaced with the installed
CLM version at output time — do not hardcode version numbers.

## Versioning

Uses [bump-my-version](https://github.com/callowayproject/bump-my-version). Config in `[tool.bumpversion]` in `pyproject.toml`.

```bash
bump-my-version bump patch   # e.g., 1.2.3 → 1.2.4
bump-my-version bump minor   # e.g., 1.2.3 → 1.3.0
bump-my-version bump major   # e.g., 1.2.3 → 2.0.0
```

Automatically updates version in 7 files, creates a commit, and tags.

## Releasing

**IMPORTANT**: Before publishing a release, you **MUST** run the local test suite and verify CI passes.

### Step 1: Run local tests (unit + integration + e2e, excluding Docker)

Docker-marked tests require CI-built images (`lite-test`, `test` tags) that are not
available locally. Run local tests excluding Docker tests:

```bash
uv run pytest -m "not docker"
```

All non-Docker tests must pass before proceeding.

### Step 2: Bump version, build, and push to CI

```bash
# Bump the version (creates commit + tag)
uv run bump-my-version bump patch  # or minor/major

# Build the package
uv build

# Push commit and tags to trigger CI
git push && git push --tags
```

### Step 3: Verify CI passes

Wait for the GitHub Actions CI pipeline to complete. The CI runs the full test suite
including Docker tests (it builds `lite-test` images from scratch).

```bash
# Check CI status
gh run list --limit 5
gh run view <run-id>
```

### Step 4: Publish to PyPI (only after CI passes)

```bash
uv publish
```

**Rules for Claude Code**:

- Never publish a release if any local test fails
- Never publish if CI has not passed for the tagged commit
- Use `pytest -m "not docker"` for local testing (Docker tests are validated in CI)
- If tests fail, fix the issues first and re-run before retrying
- The `uv build` output goes to `dist/` (sdist + wheel)

## Git Workflow

- Branch prefix: `claude/` for AI-generated branches
- **Pre-commit hooks**: Install with `uv run pre-commit install` (runs ruff and mypy automatically)
- Manual checks: `uv run ruff check src/ tests/` and `uv run ruff format src/ tests/`
- Run all tests before pushing: `pytest -m ""`

## Troubleshooting

### Common Issues

1. **Tests failing**: Check external tools (PlantUML, Draw.io), enable logging with `CLM_ENABLE_TEST_LOGGING=1`
2. **Worker issues**: Run `python diagnose_workers.py`
3. **Import errors**: Ensure `pip install -e .` in correct environment

### Known Issues

See `docs/claude/TODO.md` for current bugs and planned improvements.

---

**Repository**: https://github.com/hoelzl/clm/ | **Issues**: https://github.com/hoelzl/clm/issues

**Last Updated**: 2026-03-05
