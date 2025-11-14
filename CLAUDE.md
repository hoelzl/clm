# CLX - AI Assistant Guide

This document provides a comprehensive overview of the CLX (Coding-Academy Lecture Manager eXperimental) codebase for AI assistants working with this repository.

## Project Overview

**CLX** is a course content processing system that converts educational materials (Jupyter notebooks, PlantUML diagrams, Draw.io diagrams) into multiple output formats. It manages course files, sections, topics, and performs various transformations through a worker-based architecture.

**Current Version**: 0.2.2
**License**: MIT
**Python Support**: 3.10, 3.11, 3.12
**Repository**: https://github.com/hoelzl/clx/

## Architecture Status: SQLite-Based (Migration ~75% Complete)

**IMPORTANT**: The project has successfully migrated to a SQLite-based architecture. The CLI now defaults to SQLite, making the system simpler and easier to use.

- **Current (Default)**: SQLite job queue + Direct/Docker worker execution
- **Legacy (Deprecated)**: RabbitMQ + FastStream backend (use `--use-rabbitmq` flag)

**Default Behavior**: `clx build` now uses SQLite backend automatically. No RabbitMQ setup required!

**Migration Status** (Phase 4 COMPLETE as of 2025-11-14):
- ✅ SQLite infrastructure fully implemented (47 passing tests)
- ✅ All workers migrated to SQLite-only
- ✅ SqliteBackend fully functional (15 passing tests)
- ✅ **CLI defaults to SQLite (Phase 4 complete!)**
- ⚠️ RabbitMQ infrastructure still in docker-compose (cleanup pending - Phases 5-6)

When making changes, use the SQLite-based approach. RabbitMQ support is deprecated and will be removed in a future version.

## Repository Structure

```
clx/
├── clx/                           # Core course processing package
│   ├── src/clx/
│   │   ├── course_files/          # File handlers (notebook, plantuml, drawio)
│   │   ├── operations/            # File processing operations
│   │   ├── utils/                 # Utilities (notebook, text, execution)
│   │   ├── course.py              # Main Course class
│   │   ├── course_spec.py         # Course specification parsing
│   │   └── section.py, topic.py   # Course structure
│   └── tests/                     # Core package tests
│
├── clx-cli/                       # Command-line interface
│   ├── src/clx_cli/
│   │   ├── main.py                # Click-based CLI entry point
│   │   ├── file_event_handler.py  # Watchdog file monitoring
│   │   └── git_dir_mover.py       # Git directory utilities
│   └── tests/                     # CLI integration tests
│
├── clx-common/                    # Shared infrastructure library
│   ├── src/clx_common/
│   │   ├── backends/              # Backend implementations
│   │   ├── database/              # SQLite job queue system
│   │   │   ├── schema.py          # Database schema
│   │   │   ├── job_queue.py       # Job queue operations
│   │   │   └── db_operations.py   # Cache operations
│   │   ├── messaging/             # Message payloads/results
│   │   ├── workers/               # Worker infrastructure
│   │   │   ├── worker_base.py     # Abstract Worker class
│   │   │   ├── pool_manager.py    # Worker pool management
│   │   │   └── worker_executor.py # Docker/Direct execution
│   │   ├── backend.py             # Backend interface
│   │   └── operation.py           # Operation base class
│   └── tests/                     # Infrastructure tests
│
├── clx-faststream-backend/        # Message broker backends
│   ├── src/clx_faststream_backend/
│   │   ├── sqlite_backend.py      # SQLite backend (NEW)
│   │   └── faststream_backend.py  # RabbitMQ backend (LEGACY)
│   └── tests/
│
├── services/                      # Worker services
│   ├── notebook-processor/        # Jupyter notebook processing
│   │   ├── src/
│   │   │   ├── notebook_worker.py     # Worker implementation
│   │   │   ├── notebook_processor.py  # Processing logic
│   │   │   └── output_spec.py         # Output formats
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   │
│   ├── plantuml-converter/        # PlantUML diagram conversion
│   │   ├── src/
│   │   │   ├── plantuml_worker.py     # Worker implementation
│   │   │   └── plantuml_converter.py  # Conversion logic
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   │
│   └── drawio-converter/          # Draw.io diagram conversion
│       ├── src/
│       │   ├── drawio_worker.py       # Worker implementation
│       │   └── drawio_converter.py    # Conversion logic
│       ├── Dockerfile
│       └── requirements.txt
│
├── docs/                          # Documentation
├── build-services.sh              # Build Docker images (Linux/macOS)
├── build-services.ps1             # Build Docker images (Windows)
├── push-services.sh               # Push to Docker Hub (Linux/macOS)
├── push-services.ps1              # Push to Docker Hub (Windows)
├── docker-compose.yaml            # Service orchestration
├── pyproject.toml                 # Root project config
├── tox.ini                        # Tox configuration
└── conftest.py                    # Pytest configuration
```

## Key Packages

### 1. clx (Core Package)

**Purpose**: Core course processing logic

**Key Classes**:
- `Course` - Main course representation
- `Section` - Course section management
- `Topic` - Individual topic handling
- `CourseFile` - Base class for all file types
- `NotebookFile` - Jupyter notebook handler
- `DrawioFile` - Draw.io diagram handler
- `PlantUmlFile` - PlantUML diagram handler

**Dependencies**: `clx-common==0.2.2`

### 2. clx-cli (Command Line Interface)

**Purpose**: CLI tool for running course conversions

**Entry Point**: `clx` command (via `clx_cli.main:cli`)

**Main Features**:
- Course conversion
- File watching with watchdog
- Backend selection (SQLite/RabbitMQ)
- Worker management

**Dependencies**: `clx`, `clx-faststream-backend`, `click`, `watchdog`

### 3. clx-common (Shared Infrastructure)

**Purpose**: Shared utilities and infrastructure

**Key Components**:
- **Job Queue System**: SQLite-based job orchestration
  - Tables: `jobs`, `results_cache`, `workers`
  - Operations: submit, poll, update status, cache results
- **Worker Management**: Worker pools, executors, progress tracking
- **Message Definitions**: Pydantic models for all service payloads/results
- **Backends**: Abstract backend interface + implementations

**Dependencies**: `pydantic~=2.8.2`

### 4. clx-faststream-backend (Message Processing)

**Purpose**: Backend implementations for job orchestration

**Implementations**:
- `SqliteBackend` - **NEW**: SQLite-based backend (preferred)
- `FastStreamBackend` - **LEGACY**: RabbitMQ-based backend

**Dependencies**: `faststream[rabbit]~0.5.19`, `attrs`, `clx-common`

## Worker Services

### notebook-processor

**Purpose**: Processes Jupyter notebooks

**Capabilities**:
- Execute notebooks with various kernels (Python, C++, C#, Java, TypeScript)
- Convert to formats: HTML, slides, PDF, Python script, etc.
- Template support for different languages

**External Dependencies**: Python, IPython, Jupyter

**Key Files**: `notebook_worker.py`, `notebook_processor.py`, `output_spec.py`

### plantuml-converter

**Purpose**: Converts PlantUML diagrams to images

**Output Formats**: PNG, SVG

**External Dependencies**:
- Java Runtime Environment
- PlantUML JAR (plantuml-1.2024.6.jar)

**Environment Variable**: `PLANTUML_JAR` - Path to PlantUML JAR file

**Key Files**: `plantuml_worker.py`, `plantuml_converter.py`

### drawio-converter

**Purpose**: Converts Draw.io diagrams to images

**Output Formats**: PNG, SVG, PDF

**External Dependencies**:
- Draw.io desktop application
- Xvfb (for headless rendering in Docker)

**Environment Variable**: `DRAWIO_EXECUTABLE` - Path to Draw.io executable

**Key Files**: `drawio_worker.py`, `drawio_converter.py`

## Testing Framework

### Pytest Configuration

**Test Markers** (defined in `pyproject.toml`):
```python
markers = [
    "slow: mark tests as slow to run",
    "broker: mark test that require a broker to run",
    "integration: mark tests as integration tests requiring full worker setup",
    "e2e: mark tests as end-to-end tests that test full course conversion",
]
```

**Default Behavior**: Skips slow, broker, integration, and e2e tests

### Running Tests

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
pytest clx/tests/test_course.py
```

### Test Organization

- **Unit tests**: Fast, mocked dependencies, no markers
- **Integration tests**: Real workers, SQLite database, `@pytest.mark.integration`
- **E2E tests**: Full course conversion, `@pytest.mark.e2e`
- **Slow tests**: Long-running tests, `@pytest.mark.slow`
- **Broker tests**: Require RabbitMQ, `@pytest.mark.broker`

### Test Logging

Automatic logging for tests with `e2e` or `integration` markers.

**Environment Variables**:
- `CLX_ENABLE_TEST_LOGGING=1` - Enable logging for all tests
- `CLX_LOG_LEVEL=DEBUG` - Set log level (DEBUG, INFO, WARNING, ERROR)
- `CLX_E2E_PROGRESS_INTERVAL=5` - Progress update interval (seconds)
- `CLX_E2E_LONG_JOB_THRESHOLD=30` - Long job warning threshold (seconds)

## Development Workflow

### Initial Setup

```bash
# Clone repository
git clone https://github.com/hoelzl/clx.git
cd clx

# Install in development mode (from root)
pip install -e clx-common/
pip install -e clx/
pip install -e clx-faststream-backend/
pip install -e clx-cli/

# Or install all at once
pip install -e .

# Verify installation
python verify_installation.py
```

### Running the CLI

```bash
# Build/convert a course (uses SQLite backend by default)
clx build /path/to/course.yaml

# Watch for file changes and auto-rebuild
clx build /path/to/course.yaml --watch

# Use RabbitMQ backend (DEPRECATED - for backward compatibility only)
clx build /path/to/course.yaml --use-rabbitmq

# Additional options
clx build /path/to/course.yaml --output-dir /path/to/output --log-level INFO
```

**Note**: The default backend is now SQLite - no RabbitMQ setup required!

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

```bash
# Start all services (RabbitMQ, workers, monitoring)
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

**Loguru** is available in `clx_common.logging` for enhanced logging.

### Error Handling

- Always include context in error messages
- Use correlation IDs for tracking requests
- Preserve tracebacks when re-raising exceptions

```python
try:
    result = process_file(file_path)
except Exception as e:
    logger.error(f"Failed to process {file_path}: {e}", exc_info=True)
    raise
```

### Configuration

Use environment variables for configuration:

```python
import os

PLANTUML_JAR = os.getenv("PLANTUML_JAR", "plantuml.jar")
DRAWIO_EXECUTABLE = os.getenv("DRAWIO_EXECUTABLE", "drawio")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
DB_PATH = os.getenv("DB_PATH", "clx_jobs.db")
```

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

### Creating a Migration

When migrating from RabbitMQ to SQLite:

1. Check `MIGRATION_PLAN.md` for current phase
2. Update both backend implementations initially
3. Add feature flags for testing
4. Gradually phase out RabbitMQ code
5. Update tests to use SQLite backend

## Important Notes and Gotchas

### Build System

- **Always build from root directory**: Docker builds need access to `clx-common/`
- **BuildKit required**: Cache mounts won't work without BuildKit
- **Cache mounts**: Speed up builds significantly; don't use `--no-cache-dir` with pip

### Database

- **SQLite databases**:
  - `clx_jobs.db` or `jobs.db` - Job queue
  - `clx_cache.db` - Results cache (gitignored)
- **Thread safety**: SQLite has WAL mode enabled for concurrent access
- **Connection pooling**: Not needed, lightweight connections

### Worker Execution

- **Two modes**: Docker (isolated) and Direct (subprocess)
- **Direct mode**: Faster for development, requires external tools installed locally
- **Docker mode**: Production-ready, self-contained
- **Environment variables**: Required for worker configuration in direct mode

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
- **Testing before commit**: Run at least unit tests (`pytest`)

## Documentation

### Key Documents

- `BUILD.md` - Docker build guide with BuildKit caching
- `ARCHITECTURE_PROPOSAL.md` - Architecture simplification proposal
- `MIGRATION_PLAN.md` - RabbitMQ to SQLite migration plan
- `PHASE*_*.md` - Phase-specific documentation
- `E2E_LOGGING.md` - End-to-end logging configuration
- `docs/IMPLEMENTATION_SUMMARY.md` - Direct worker execution
- `docs/direct_worker_execution.md` - User guide for direct workers

### API Documentation

Currently no auto-generated API docs. Refer to:
- Docstrings in source code
- Type hints for function signatures
- Test files for usage examples

## Version Management

**Current Version**: 0.2.2

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

## Future Direction

The project is moving toward:
- **Simplified architecture**: Reduce from 4 packages to fewer
- **Remove infrastructure overhead**: Eliminate RabbitMQ, monitoring stack
- **Direct file access**: No message serialization
- **Easier debugging**: Simpler architecture, better logging
- **Better testing**: Faster tests, easier setup

Prefer SQLite-based implementations and direct worker execution when contributing new features.

---

**Last Updated**: 2025-11-13
**Repository**: https://github.com/hoelzl/clx/
**Issues**: https://github.com/hoelzl/clx/issues
