# CLX - AI Assistant Guide

This document provides a comprehensive overview of the CLX (Coding-Academy Lecture Manager eXperimental) codebase for AI assistants working with this repository.

## Project Overview

**CLX** is a course content processing system that converts educational materials (Jupyter notebooks, PlantUML diagrams, Draw.io diagrams) into multiple output formats. It manages course files, sections, topics, and performs various transformations through a worker-based architecture.

**Current Version**: 0.4.0
**License**: MIT
**Python Support**: 3.11, 3.12, 3.13
**Repository**: https://github.com/hoelzl/clx/

## Architecture Status: Consolidated Single Package (Phase 7 COMPLETE)

**IMPORTANT**: The project has been fully consolidated into a single unified package with a clean four-layer architecture.

- **Architecture**: SQLite job queue + Direct/Docker worker execution
- **Package Structure**: Single `clx` package with `core`, `infrastructure`, `workers`, and `cli` subpackages
- **Installation**: Simple `pip install -e .` from repository root
- **Testing**: Run `pytest` from repository root for unit tests, `pytest -m ""` for all tests (unit, integration, e2e)

**Recent Changes**:
- ✅ **v0.4.0: Workers integrated into main package**
  - Workers moved from separate `services/` packages to `src/clx/workers/`
  - Optional dependencies for each worker: `[notebook]`, `[plantuml]`, `[drawio]`
  - New `[all-workers]` and `[ml]` dependency groups
  - Four-layer architecture (core, infrastructure, workers, cli)

## Repository Structure

```
clx/                               # Repository root
├── src/clx/                       # CLX package source (v0.4.0)
│   ├── __version__.py             # Version information
│   ├── __init__.py                # Package init with convenience imports
│   ├── py.typed                   # PEP 561 type marker
│   │
│   ├── core/                      # Core course processing (domain logic)
│   │   ├── course.py              # Main Course class
│   │   ├── course_file.py         # Base file class
│   │   ├── course_spec.py         # Course specification parsing
│   │   ├── section.py, topic.py   # Course structure
│   │   ├── dir_group.py           # Directory group handling
│   │   ├── course_files/          # File type handlers
│   │   │   ├── notebook_file.py   # Jupyter notebooks
│   │   │   ├── plantuml_file.py   # PlantUML diagrams
│   │   │   └── drawio_file.py     # Draw.io diagrams
│   │   ├── operations/            # File operations
│   │   │   ├── process_notebook.py
│   │   │   ├── convert_plantuml_file.py
│   │   │   └── convert_drawio_file.py
│   │   └── utils/                 # Course utilities
│   │       ├── notebook_utils.py
│   │       ├── text_utils.py
│   │       └── execution_utils.py
│   │
│   ├── infrastructure/            # Infrastructure (runtime support)
│   │   ├── backend.py             # Backend interface
│   │   ├── operation.py           # Operation base class
│   │   ├── backends/              # Backend implementations
│   │   │   ├── sqlite_backend.py  # SQLite backend (primary)
│   │   │   ├── local_ops_backend.py # Local operations backend
│   │   │   └── dummy_backend.py   # Dummy backend for testing
│   │   ├── database/              # SQLite job queue system
│   │   │   ├── schema.py          # Database schema
│   │   │   ├── job_queue.py       # Job queue operations
│   │   │   └── db_operations.py   # Cache operations
│   │   ├── messaging/             # Message payloads/results
│   │   │   ├── base_classes.py    # Payload, Result base classes
│   │   │   ├── notebook_classes.py, plantuml_classes.py, drawio_classes.py
│   │   │   └── correlation_ids.py
│   │   ├── workers/               # Worker management
│   │   │   ├── worker_base.py     # Abstract Worker class
│   │   │   ├── pool_manager.py    # Worker pool management
│   │   │   └── worker_executor.py # Docker/Direct execution
│   │   ├── logging/               # Logging utilities
│   │   ├── services/              # Service registry
│   │   └── utils/                 # Infrastructure utilities
│   │
│   ├── workers/                   # Worker implementations (NEW in v0.4.0)
│   │   ├── notebook/              # Notebook processing worker
│   │   │   ├── __main__.py        # Worker entry point
│   │   │   ├── notebook_worker.py # Worker implementation
│   │   │   ├── notebook_processor.py  # Core processing logic
│   │   │   ├── output_spec.py     # Output format specifications
│   │   │   ├── templates_*/       # Language-specific templates
│   │   │   └── utils/             # Notebook utilities
│   │   ├── plantuml/              # PlantUML conversion worker
│   │   │   ├── __main__.py        # Worker entry point
│   │   │   ├── plantuml_worker.py # Worker implementation
│   │   │   └── plantuml_converter.py  # Converter logic
│   │   └── drawio/                # Draw.io conversion worker
│   │       ├── __main__.py        # Worker entry point
│   │       ├── drawio_worker.py   # Worker implementation
│   │       └── drawio_converter.py    # Converter logic
│   │
│   └── cli/                       # Command-line interface
│       ├── main.py                # Click-based CLI entry point
│       ├── file_event_handler.py  # Watchdog file monitoring
│       └── git_dir_mover.py       # Git directory utilities
│
├── tests/                         # All tests (221 total)
│   ├── conftest.py                # Shared test fixtures
│   ├── core/                      # Core module tests (43 tests)
│   ├── infrastructure/            # Infrastructure tests (114 tests)
│   ├── cli/                       # CLI tests (15 tests)
│   └── e2e/                       # End-to-end tests (49 tests)
│
├── pyproject.toml                 # Package configuration (hatchling)
├── uv.lock                        # uv lock file
├── tox.ini                        # Tox configuration
├── LICENSE, README.md, CLAUDE.md
├── MIGRATION_GUIDE_V0.4.md        # Migration guide from v0.3.x
├── docker-compose.yaml            # Service orchestration
└── Phase documentation files
```

## Package Structure

### Single Unified Package: clx (v0.4.0)

The CLX package is now a single unified package with four main subpackages representing a clean architecture:

#### 1. `clx.core` - Domain Logic

**Purpose**: Core course processing logic

**Key Classes**:
- `Course` - Main course representation
- `Section` - Course section management
- `Topic` - Individual topic handling
- `CourseFile` - Base class for all file types
- `NotebookFile` - Jupyter notebook handler
- `DrawioFile` - Draw.io diagram handler
- `PlantUmlFile` - PlantUML diagram handler

**Key Modules**:
- `course_files/` - File type handlers (notebook, plantuml, drawio)
- `operations/` - File processing operations
- `utils/` - Utilities (notebook, text, execution)

**Dependencies**: None (domain layer has no infrastructure dependencies)

#### 2. `clx.infrastructure` - Infrastructure Support

**Purpose**: Runtime infrastructure for job orchestration and worker management

**Key Components**:
- **Job Queue System**: SQLite-based job orchestration
  - Tables: `jobs`, `results_cache`, `workers`
  - Operations: submit, poll, update status, cache results
- **Worker Management**: Worker pools, executors, progress tracking
- **Message Definitions**: Pydantic models for all service payloads/results
- **Backends**: SqliteBackend (primary), LocalOpsBackend, DummyBackend

**Key Modules**:
- `backends/` - Backend implementations (sqlite, local_ops, dummy)
- `database/` - SQLite job queue (schema, job_queue, db_operations)
- `messaging/` - Message payloads and results
- `workers/` - Worker management (worker_base, pool_manager, worker_executor)
- `logging/`, `services/`, `utils/`

**Dependencies**: `pydantic~=2.8.2`, `attrs`

#### 3. `clx.workers` - Worker Implementations (NEW in v0.4.0)

**Purpose**: Worker implementations for processing different file types

**Worker Modules**:
- `notebook/` - Jupyter notebook processing
  - Supports: Python, C++, C#, Java, TypeScript
  - Templates for each language
  - Output formats: HTML, slides, Python script
  - **Optional dependency**: Install with `pip install -e ".[notebook]"`
- `plantuml/` - PlantUML diagram conversion
  - Output formats: PNG, SVG
  - **Optional dependency**: Install with `pip install -e ".[plantuml]"`
  - **External dependency**: PlantUML JAR + Java
- `drawio/` - Draw.io diagram conversion
  - Output formats: PNG, SVG, PDF
  - **Optional dependency**: Install with `pip install -e ".[drawio]"`
  - **External dependency**: Draw.io desktop app

**Key Features**:
- Optional dependencies for each worker type
- Direct execution mode (subprocess) or Docker mode

#### 4. `clx.cli` - Command-Line Interface

**Purpose**: CLI tool for running course conversions

**Entry Point**: `clx` command (via `clx.cli.main:cli`)

**Main Features**:
- Course conversion
- File watching with watchdog
- Worker management

**Key Files**:
- `main.py` - Click-based CLI entry point
- `file_event_handler.py` - Watchdog file monitoring
- `git_dir_mover.py` - Git directory utilities

**Dependencies**: `click`, `watchdog`, `tabulate`, `docker`, `rich`

**Key Commands**:
- `clx build` - Build/convert a course
- `clx status` - Show system status (workers, job queue, health)
- `clx workers list` - List registered workers (uses tabulate for table formatting)
- `clx workers cleanup` - Clean up dead workers
- `clx monitor` - Real-time monitoring TUI (requires `[tui]` optional dependencies)
- `clx serve` - Web dashboard server (requires `[web]` optional dependencies)
- `clx start-services` / `clx stop-services` - Manage persistent workers
- `clx config` - Configuration management

### Installation

**Core package (minimal install):**
```bash
# From repository root (core dependencies only - no worker dependencies)
pip install -e .

# Or with uv
uv pip install -e .
```

**With worker dependencies (for direct execution mode):**
```bash
# Install all worker dependencies and dev tools
pip install -e ".[all-workers,dev]"  # All three workers and dev tools
```

**Optional Dependencies Summary**:
- `[notebook]`: IPython, nbconvert, jupytext, matplotlib, pandas, sklearn (for notebook worker)
- `[plantuml]`: aiofiles, tenacity (for PlantUML worker)
- `[drawio]`: aiofiles, tenacity (for Draw.io worker)
- `[all-workers]`: All worker dependencies combined
- `[ml]`: PyTorch, FastAI, transformers (for ML notebooks)
- `[tui]`: textual, rich (for `clx monitor` command)
- `[web]`: fastapi, uvicorn, websockets (for `clx serve` command)
- `[dev]`: pytest, mypy, ruff, pytest-asyncio, pytest-cov, httpx
- `[all]`: All of the above (use this for full development and testing)

**Important Notes**:
- Core package works without worker dependencies (can use Docker mode)
- For direct execution mode, install `all-workers` and `dev`
- External tools (PlantUML JAR, Draw.io app) still required for those workers

### Import Examples

```python
# Convenience imports (backward compatible)
from clx import Course, Section, Topic, CourseFile, CourseSpec

# Explicit imports from subpackages
from clx.core import Course, Section, Topic
from clx.core.course_files import NotebookFile, PlantUmlFile, DrawioFile
from clx.infrastructure.backend import Backend
from clx.infrastructure.backends import SqliteBackend
from clx.infrastructure.database import JobQueue
from clx.infrastructure.messaging import NotebookPayload
from clx.infrastructure.workers import WorkerBase
from clx.cli.main import cli

# Worker imports (NEW in v0.4.0)
from clx.workers.notebook.notebook_worker import NotebookWorker
from clx.workers.plantuml.plantuml_worker import PlantUmlWorker
from clx.workers.drawio.drawio_worker import DrawioWorker
```


## Worker Implementations (Integrated in v0.4.0)

Workers are now integrated into the main `clx` package under `clx.workers/`. The old `services/` directory contains only legacy build artifacts for Docker.

### clx.workers.notebook - Notebook Processing

**Location**: `src/clx/workers/notebook/`

**Purpose**: Processes Jupyter notebooks

**Capabilities**:
- Execute notebooks with various kernels (Python, C++, C#, Java, TypeScript)
- Convert to formats: HTML, slides, Python script, etc.
- Template support for different languages

**Installation**:
```bash
# Install notebook worker dependencies
pip install -e ".[notebook]"
```

**Python Dependencies**: IPython, nbconvert, jupytext, matplotlib, pandas, scikit-learn

**External Dependencies**: Python, IPython, Jupyter kernels for target languages

**Key Files**:
- `notebook_worker.py` - Worker implementation
- `notebook_processor.py` - Core processing logic
- `output_spec.py` - Output format specifications
- `templates_*/` - Language-specific templates

**Entry Point**: `python -m clx.workers.notebook`

### clx.workers.plantuml - PlantUML Conversion

**Location**: `src/clx/workers/plantuml/`

**Purpose**: Converts PlantUML diagrams to images

**Output Formats**: PNG, SVG

**Installation**:
```bash
# Install PlantUML worker dependencies
pip install -e ".[plantuml]"
```

**Python Dependencies**: aiofiles, tenacity

**External Dependencies**:
- Java Runtime Environment
- PlantUML JAR (plantuml-1.2024.6.jar)

**Environment Variable**: `PLANTUML_JAR` - Path to PlantUML JAR file

**Key Files**:
- `plantuml_worker.py` - Worker implementation
- `plantuml_converter.py` - Converter logic

**Entry Point**: `python -m clx.workers.plantuml`

### clx.workers.drawio - Draw.io Conversion

**Location**: `src/clx/workers/drawio/`

**Purpose**: Converts Draw.io diagrams to images

**Output Formats**: PNG, SVG, PDF

**Installation**:
```bash
# Install Draw.io worker dependencies
pip install -e ".[drawio]"
```

**Python Dependencies**: aiofiles, tenacity

**External Dependencies**:
- Draw.io desktop application
- Xvfb (for headless rendering on Linux)

**Environment Variables**:
- `DRAWIO_EXECUTABLE` - Path to Draw.io executable
- `DISPLAY` - X display (Unix/Linux only, not needed on Windows)

**Key Files**:
- `drawio_worker.py` - Worker implementation
- `drawio_converter.py` - Converter logic

**Entry Point**: `python -m clx.workers.drawio`

### Worker Execution Modes

Workers can run in two modes:

1. **Direct Execution Mode** (Default)
   - Workers run as subprocesses
   - Requires worker dependencies installed
   - Faster for development
   - Install with `pip install -e ".[all-workers]"`

2. **Docker Mode**
   - Workers run in Docker containers
   - No worker dependencies needed on host
   - Better isolation
   - Requires Docker daemon and images

## Testing Framework

### Quick Start: Automated Environment Setup

**For Claude Code on the web or new development environments**, use the automated setup script:

```bash
# Run the automated setup script
./.claude/setup-test-env.sh
```

This script automatically handles:
- Installing CLX package with all testing dependencies
- Installing worker service packages (notebook-processor, plantuml-converter, drawio-converter)
- Downloading and installing PlantUML JAR
- Downloading and installing DrawIO desktop application
- Starting Xvfb for headless rendering
- Setting environment variables (PLANTUML_JAR, DISPLAY, DRAWIO_EXECUTABLE)
- Verifying the complete environment is working

**After setup completes**, you can run all tests including integration and e2e tests.

**Manual Setup**: If you prefer manual setup or the automated script fails, see sections below for detailed instructions.

### Testing in Claude Code Web and Constrained Environments

**TL;DR**: Tests automatically skip when tools are unavailable. Just run `./.claude/setup-test-env.sh` and then `pytest -m ""`.

#### What Works Out of the Box

✅ **PlantUML Tests** - PlantUML JAR (22MB) is committed directly in the repository, so PlantUML tests work immediately in Claude Code Web without any downloads.

✅ **Auto-Skip** - Tests automatically detect available tools and skip tests that require missing tools with clear messages.

⚠️ **DrawIO Tests** - DrawIO .deb (98MB) is in Git LFS and may timeout during download in Claude Code Web. These tests will be automatically skipped if DrawIO is unavailable.

#### Tool Availability Detection

The test framework automatically detects available external tools at test session start:

```
======================================================================
External Tool Availability:
  PlantUML: ✓ Available
  DrawIO:   ✗ Not available
  Xvfb:     ✗ Not running
======================================================================
```

**Tests are automatically skipped** when required tools are not available. You'll see clear skip messages like:

```
SKIPPED [1] tests/test_drawio.py:42: DrawIO not available - set DRAWIO_EXECUTABLE or install DrawIO
```

#### Diagnostic Tool

Check what tools are available and which tests will run:

```bash
./.claude/diagnose-test-env.sh
```

This script reports:
- Which external tools are available (PlantUML, DrawIO, Xvfb)
- Which test categories will work
- Recommended test commands for your environment
- Setup instructions for missing tools

#### Test Markers for Tool Requirements

Tests use granular markers to declare tool requirements:

- `@pytest.mark.requires_plantuml` - Requires PlantUML JAR and Java
- `@pytest.mark.requires_drawio` - Requires DrawIO executable (platform-aware)
  - **Windows**: Only needs DrawIO executable (native GUI, no DISPLAY needed)
  - **Unix/Linux/Mac**: Needs DrawIO + DISPLAY environment variable
    - Works with **real displays** (e.g., DISPLAY=:0 on desktop)
    - Works with **Xvfb** (e.g., DISPLAY=:99 in Docker/headless)
  - Automatically adapts to your platform - no manual configuration needed!
- `@pytest.mark.requires_xvfb` - **[DEPRECATED]** Use `requires_drawio` instead

**These markers automatically skip tests** when tools are unavailable. You don't need to specify anything - just run `pytest -m ""` and tests will skip as needed.

**Important**: The `requires_drawio` marker is platform-aware:
- **Windows**: Tests run if DrawIO is installed (no DISPLAY needed)
- **Unix/Linux desktop**: Tests run using system display (no Xvfb needed)
- **Unix/Linux headless with Xvfb**: Tests run using Xvfb virtual display
- **Unix/Linux headless without Xvfb**: Tests are automatically skipped

#### Typical Claude Code Web Workflow

```bash
# 1. Run setup script (PlantUML will work, DrawIO may timeout - that's OK)
./.claude/setup-test-env.sh

# 2. Check what's available
./.claude/diagnose-test-env.sh

# 3. Run tests (tests will be skipped automatically if services are missing)
pytest                # Unit tests
pytest -m integration # Integration tests
pytest -m e2e         # E2E tests
pytest -m ""          # All tests
```

**Expected Result**: Most tests pass, tests with unavailable services are skipped with clear messages. This is normal and correct behavior.

### Pytest Configuration

**Test Markers**:
```python
# Category markers (defined in pyproject.toml)
markers = [
    "slow: mark tests as slow to run",
    "integration: mark tests as integration tests requiring full worker setup",
    "e2e: mark tests as end-to-end tests that test full course conversion",
]

# Tool requirement markers (auto-registered in conftest.py)
# These automatically skip tests when tools are unavailable:
    "requires_plantuml: mark test as requiring PlantUML JAR and Java",
    "requires_drawio: mark test as requiring DrawIO executable "
                      "(Unix/Linux: also needs DISPLAY; Windows: no DISPLAY needed)",
    "requires_xvfb: [DEPRECATED] use requires_drawio instead",
```

**Default Behavior**:
- Skips slow, integration, and e2e tests (unless explicitly requested)
- Auto-skips tests marked with `requires_*` when tools are unavailable

### Running Tests

**Prerequisites**: Environment must be set up with all dependencies (use `./.claude/setup-test-env.sh` or manual setup below)

**Running tests**:
```bash
# Fast unit tests only (default)
pytest

# Include integration tests
pytest -m integration

# Include e2e tests
pytest -m e2e

# Run all tests
pytest -m ""

# Run with logging enabled
CLX_ENABLE_TEST_LOGGING=1 pytest -m e2e

# Run specific test file
pytest tests/test_course.py
```

### Test Organization

- **Unit tests**: Fast, mocked dependencies, no markers
- **Integration tests**: Real workers, SQLite database, `@pytest.mark.integration`
  - **Requires Xvfb** if testing DrawIO converter
- **E2E tests**: Full course conversion, `@pytest.mark.e2e`
  - **Requires Xvfb** if course includes Draw.io diagrams
- **Slow tests**: Long-running tests, `@pytest.mark.slow`

### Test Logging

Automatic logging for tests with `e2e` or `integration` markers.

**Environment Variables**:
- `CLX_ENABLE_TEST_LOGGING=1` - Enable logging for all tests
- `CLX_LOG_LEVEL=DEBUG` - Set log level (DEBUG, INFO, WARNING, ERROR)
- `CLX_E2E_PROGRESS_INTERVAL=5` - Progress update interval (seconds)
- `CLX_E2E_LONG_JOB_THRESHOLD=30` - Long job warning threshold (seconds)

## Development Workflow

### Initial Setup

**Automated Setup (Recommended for Claude Code on the web):**

```bash
# Clone repository
git clone https://github.com/hoelzl/clx.git
cd clx

# Run automated setup script
./.claude/setup-test-env.sh

# This installs everything needed for development and testing:
# - CLX package with all dependencies ([all] extra)
# - External tools (PlantUML, DrawIO)
# - Xvfb for headless rendering
# - Environment variables
```

### Running the CLI

```bash
# Build/convert a course (uses SQLite backend)
clx build /path/to/course.yaml

# Watch for file changes and auto-rebuild
clx build /path/to/course.yaml --watch

# Additional options
clx build /path/to/course.yaml --output-dir /path/to/output --log-level INFO
```

### Building Docker Images

**IMPORTANT**: Always build from the repository root directory.

```bash
# Linux/macOS - Build all services
./build-services.sh

# Build specific service
./build-services.sh notebook-processor
./build-services.sh plantuml-converter
./build-services.sh drawio-converter

# Windows PowerShell
.\build-services.ps1
.\build-services.ps1 notebook-processor
```

**Requirements**:
- Docker BuildKit must be enabled
- See `BUILD.md` for detailed build documentation

### Running Services with Docker Compose

**Note**: docker-compose.yaml uses the SQLite-based architecture without any message broker.

```bash
# Start all worker services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down

# Rebuild and restart
docker-compose up -d --build
```

### Worker Management

```bash
# Diagnose worker issues
python diagnose_workers.py

# Clean up stale workers
python cleanup_workers.py
```

## Code Conventions

### Python Style

- **Type hints**: Use extensively, especially in public APIs
- **Async/await**: Preferred for I/O-bound operations
- **Dataclasses**: Use `@dataclass`, `@define` (attrs), or Pydantic models
  - Pydantic for message payloads/results
  - Attrs for internal data structures
  - dataclass for simple value objects

### Package Structure

- **src layout**: All packages use `src/package_name/` structure
- **Namespace separation**: Each package has its own namespace
- **No relative imports** across package boundaries

### Logging

```python
import logging

logger = logging.getLogger(__name__)

# Use appropriate log levels
logger.debug("Detailed information for debugging")
logger.info("General information")
logger.warning("Warning messages")
logger.error("Error messages with context")
```

### Configuration

Use environment variables for configuration:

```python
import os

PLANTUML_JAR = os.getenv("PLANTUML_JAR", "plantuml.jar")
DRAWIO_EXECUTABLE = os.getenv("DRAWIO_EXECUTABLE", "drawio")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
DB_PATH = os.getenv("DB_PATH", "clx_jobs.db")
CLX_MAX_CONCURRENCY = int(os.getenv("CLX_MAX_CONCURRENCY", "50"))
```

**Important Environment Variables**:
- `PLANTUML_JAR` - Path to PlantUML JAR file
- `DRAWIO_EXECUTABLE` - Path to Draw.io executable
- `LOG_LEVEL` - Logging level (DEBUG, INFO, WARNING, ERROR)
- `DB_PATH` - Path to SQLite job queue database
- `CLX_MAX_CONCURRENCY` - Maximum concurrent operations (default: 50)
  - Controls how many operations can run simultaneously
  - Prevents resource exhaustion on Windows and low-spec systems
  - Recommended values:
    - Windows low-spec/VMs: 25
    - Default (most systems): 50
    - High-performance Linux/macOS: 75-100
  - Set to unlimited at your own risk (may cause ZMQ errors on Windows)
- `CLX_MAX_WORKER_STARTUP_CONCURRENCY` - Maximum concurrent worker starts (default: 10)
  - Controls how many workers can start simultaneously during pool startup
  - **New in v0.3.1**: Workers now start in parallel for faster startup (3-10x speedup)
  - Prevents overwhelming Docker daemon and system resources
  - Recommended values:
    - Low-spec VM (2 cores, 4GB): 5
    - Standard (4 cores, 8GB): 10 (default)
    - High-performance (8+ cores): 15-20
    - Docker Desktop (Windows): 5-8
    - Docker Desktop (Mac M1/M2): 10-15
    - Linux server: 20-30

## Common Tasks

### Adding a New Operation

1. Create operation class in `clx/src/clx/operations/`
2. Inherit from `Operation` base class
3. Implement `execute()` method
4. Add corresponding backend implementation
5. Add message payload/result in `clx-common/src/clx_common/messaging/`
6. Add tests in `clx/tests/operations/`

### Adding a New File Type

1. Create file class in `clx/src/clx/course_files/`
2. Inherit from `CourseFile`
3. Implement required methods
4. Register in file type detection logic
5. Add tests

### Adding a New Worker Service

1. Create service directory in `services/`
2. Implement worker inheriting from `WorkerBase`
3. Create Dockerfile with BuildKit cache mounts
4. Add requirements.txt
5. Add message payload/result in `clx-common/src/clx_common/messaging/`
6. Update `docker-compose.yaml`
7. Add tests with appropriate markers

### Making Architectural Changes

When making significant architectural changes:

1. Document the design in `docs/claude/design/` or similar
2. Create incremental migration plan if needed
3. Update tests to validate new behavior
4. Archive old design documents in `docs/archive/` when complete
5. Update architecture documentation in `docs/developer-guide/architecture.md`

## Important Notes and Gotchas

### Docker Builds

- **Always build from root directory**: Docker builds need access to `clx/`
- **BuildKit required**: Cache mounts won't work without BuildKit
- **Cache mounts**: Speed up builds significantly; don't use `--no-cache-dir` with pip

### Database

- **SQLite databases** (two separate databases):
  - `clx_jobs.db` - Job queue database (stores jobs, workers, events, results_cache tables)
  - `clx_cache.db` - Cache database (stores processed_files table with pickled results)
- **Why two databases**:
  - Different lifetimes (job queue is ephemeral, cache persists)
  - Different access patterns (job queue is write-heavy, cache is read-heavy)
  - Reduced lock contention for better concurrency
- **Thread safety**: SQLite has WAL mode enabled for concurrent access
- **Connection pooling**: Not needed, lightweight connections

### Worker Execution

- **Two modes**: Docker (isolated) and Direct (subprocess)
- **Direct mode**: Faster for development, requires external tools installed locally
- **Docker mode**: Production-ready, self-contained
- **Environment variables**: Required for worker configuration in direct mode
- **Parallel startup** (v0.3.1+): Workers start concurrently for 3-10x faster startup
  - Configurable via `CLX_MAX_WORKER_STARTUP_CONCURRENCY` (default: 10)
  - 16 workers: ~12s (parallel) vs ~48s (sequential)

### Testing

- **Default test run excludes integration/e2e**: Use `-m integration` or `-m e2e` explicitly
- **Database cleanup**: Tests create temporary databases, cleaned up in fixtures
- **Worker lifecycle**: Integration tests start/stop real workers
- **Async tests**: Automatically handled with `asyncio_mode = "auto"`

### External Tools

- **PlantUML**: Requires Java, set `PLANTUML_JAR` environment variable
- **Draw.io**: Optional, set `DRAWIO_EXECUTABLE` if available
- **Auto-detection**: System attempts to find tools automatically

### Git Workflow

- **Branch naming**: Use `claude/` prefix for AI-generated branches
- **Commit messages**: Descriptive, follow conventional commits style
- **Testing before commit**: Run `ruff check src/ tests/` and `ruff format src/ tests/`, run all tests (`pytest -m ""`)

## Documentation

### Key Documents

- `docs/developer-guide/building.md` - Docker build guide with BuildKit caching
- `docs/developer-guide/architecture.md` - Current system architecture
- `docs/developer-guide/testing.md` - Testing and logging configuration
- `docs/developer-guide/direct_worker_execution.md` - Direct worker execution guide
- `docs/developer-guide/IMPLEMENTATION_SUMMARY.md` - Technical implementation details
- `docs/archive/migration-history/` - Historical architecture migration documents
- `docs/archive/phases/` - Phase-by-phase migration summaries

### API Documentation

Currently no auto-generated API docs. Refer to:
- Docstrings in source code
- Type hints for function signatures
- Test files for usage examples

## Version Management

**Current Version**: 0.4.0

**Bumping Version**:
```bash
# Install bumpversion
pip install bump2version

# Bump patch version (0.2.2 -> 0.2.3)
bumpversion patch

# Bump minor version (0.2.2 -> 0.3.0)
bumpversion minor

# Bump major version (0.2.2 -> 1.0.0)
bumpversion major
```

Configuration in `.bumpversion.cfg`

## Troubleshooting

### Tests Failing

1. Check if external tools are available (PlantUML, Draw.io)
2. Verify database is not locked (close other connections)
3. Check worker processes aren't orphaned (`diagnose_workers.py`)
4. Enable logging: `CLX_ENABLE_TEST_LOGGING=1 pytest -v`

### Build Failures

1. Ensure building from root directory
2. Check BuildKit is enabled: `docker buildx version`
3. Verify Dockerfiles start with `# syntax=docker/dockerfile:1`
4. Check cache isn't full: `docker buildx prune`

### Worker Issues

1. Check worker registration in database
2. Verify external tool paths (environment variables)
3. Check worker logs for errors
4. Use `diagnose_workers.py` for automated diagnostics

### Import Errors

1. Ensure packages installed in development mode: `pip install -e .`
2. Check you're in the correct Python environment
3. Verify all dependencies installed: `pip install -r requirements.txt`

## Architecture Decisions

The project has successfully completed its architecture evolution:
- ✅ **Integrated workers**: Workers now part of main package (v0.4.0)
- ✅ **Unified package**: Consolidated from 4 packages to 1 (v0.3.1)
- ✅ **Pure SQLite**: Removed RabbitMQ/FastStream completely
- ✅ **Direct file access**: No message serialization overhead
- ✅ **Flexible dependencies**: Optional extras for workers, ML, UI features
- ✅ **Simpler debugging**: Streamlined architecture, comprehensive logging
- ✅ **Faster testing**: Reduced test complexity

**Current Focus**: Stability, performance optimization, and feature enhancements on the integrated worker architecture.

## Documentation Guidelines for AI Assistants

### Documentation Structure

The CLX documentation is organized to serve different audiences:

**Root Level** (essential files only):
- `README.md` - User-facing introduction and quick start
- `CLAUDE.md` - AI assistant guide (this file)
- `CONTRIBUTING.md` - Developer getting started guide
- `LICENSE` - Project license

**docs/** folder structure:
```
docs/
├── user-guide/              # End-user documentation
│   ├── README.md            # User guide overview
│   ├── installation.md      # Installation instructions
│   ├── quick-start.md       # 5-minute tutorial
│   ├── configuration.md     # Course configuration options
│   └── troubleshooting.md   # Common issues and solutions
│
├── developer-guide/         # Developer/contributor documentation
│   ├── README.md            # Developer guide overview
│   ├── architecture.md      # System architecture
│   ├── building.md          # Building Docker services
│   ├── testing.md           # Testing guidelines
│   ├── direct_worker_execution.md      # Direct worker mode
│   └── IMPLEMENTATION_SUMMARY.md       # Technical details
│
├── claude/                  # AI assistant working documents
│   ├── README.md            # Index and context for AI documents
│   ├── requirements/        # Feature requirements and analysis
│   ├── design/              # Design documents and decisions
│   ├── implementation/      # Implementation notes
│   ├── analysis/            # Technical analysis and investigations
│   └── *.md                 # Audit reports, phase summaries, etc.
│
└── archive/                 # Historical documents
    ├── migration-history/   # Architecture migration docs (2025-11)
    │   └── README.md        # Context and index
    └── phases/              # Phase-by-phase migration docs
        └── README.md        # Phase summaries
```

**`.claude/`** folder (configuration only):
- `settings.json` - Claude Code hooks and permissions
- `sessionStart` - SessionStart hook script
- `setup-test-env.sh` - Environment setup for Claude Code web
- `diagnose-test-env.sh` - Test environment diagnostics
- `README.md` - Documentation about Claude hooks

### When to Update Documentation

**User-Facing Changes**:
When adding or modifying features that affect end users:
1. Update `docs/user-guide/` as appropriate
2. Add examples to `docs/user-guide/quick-start.md`
3. Update `docs/user-guide/configuration.md` for new options
4. Add troubleshooting tips to `docs/user-guide/troubleshooting.md`
5. Update root `README.md` if it affects the quick start

**Architecture Changes**:
When changing system architecture or adding infrastructure:
1. Update `docs/developer-guide/architecture.md`
2. Update this file (`CLAUDE.md`) for AI assistant context
3. Update `CONTRIBUTING.md` if workflow changes

**New Development Documents**:
When creating requirements, design docs, or implementation plans:
1. **Active Work**: Place in `docs/claude/` directory (e.g., `docs/claude/requirements/`, `docs/claude/design/`)
2. **Completed Work**: Move to `docs/developer-guide/` if still relevant
3. **Historical**: Move to `docs/archive/` with context README

### Documentation Maintenance

**Keep Documentation Current**:
- Update `CLAUDE.md` when project structure changes
- Update developer guide when architecture evolves
- Update user guide when features change
- Archive historical documents, don't delete them

**Documentation Style**:
- **User docs**: Focus on what and how, not why or internals
- **Developer docs**: Include architecture, design decisions, internals
- **CLAUDE.md**: Comprehensive technical reference for AI assistants
- **README.md**: Brief, welcoming, quick-start focused

**Avoid Documentation Bloat**:
- Don't create new markdown files in root directory
- Don't create separate docs for minor features (add to existing)
- Don't duplicate information (link to canonical source)
- Archive outdated docs, don't leave them in root

### Archiving Guidelines

When archiving historical documents:

1. **Add Context**: Create or update README.md in archive folder explaining:
   - What the documents are about
   - When they were created and why
   - Why they're being archived
   - Where to find current information

2. **Preserve History**: Don't delete, archive with context

3. **Update References**: Update any documents that link to archived files

**Example Archive README**:
```markdown
# Historical Documents

This folder contains documents from [time period] about [topic].

## Background
[Explain what was happening and why these docs were created]

## Documents
- `DOC.md` - [Brief description]

## See Also
- Current documentation: `docs/developer-guide/architecture.md`

**Date Archived**: YYYY-MM-DD
```

### Cross-References

When documenting features:
- **User Guide** ← link to detailed config, troubleshooting
- **Developer Guide** ← link to architecture, implementation details
- **CLAUDE.md** ← comprehensive reference, link to all docs

### Documentation Checklist

When completing a task:
- [ ] Updated relevant user documentation?
- [ ] Updated developer documentation for architecture changes?
- [ ] Updated CLAUDE.md if project structure changed?
- [ ] Archived historical documents with context?
- [ ] Removed any new files from root that belong in docs/?
- [ ] Updated README.md if quick start affected?

---

**Last Updated**: 2025-11-19 (Reorganized .claude folder - moved docs to docs/claude/)
**Repository**: https://github.com/hoelzl/clx/
**Issues**: https://github.com/hoelzl/clx/issues