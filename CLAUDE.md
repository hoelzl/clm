# CLX - AI Assistant Guide

This document provides a comprehensive overview of the CLX (Coding-Academy Lecture Manager eXperimental) codebase for AI assistants working with this repository.

## Project Overview

**CLX** is a course content processing system that converts educational materials (Jupyter notebooks, PlantUML diagrams, Draw.io diagrams) into multiple output formats. It manages course files, sections, topics, and performs various transformations through a worker-based architecture.

**Current Version**: 0.3.0
**License**: MIT
**Python Support**: 3.10, 3.11, 3.12
**Repository**: https://github.com/hoelzl/clx/

## Architecture Status: Consolidated Single Package (Phase 7 COMPLETE)

**IMPORTANT**: The project has been fully consolidated into a single unified package with a clean three-layer architecture.

- **Architecture**: SQLite job queue + Direct/Docker worker execution
- **Package Structure**: Single `clx` package with `core`, `infrastructure`, and `cli` subpackages
- **Installation**: Simple `pip install -e .` from repository root
- **Testing**: Run `pytest` from repository root (221 tests total)

**Migration Status** (Phase 7 COMPLETE as of 2025-11-15):
- ✅ **Phase 7: Package consolidation complete**
  - Consolidated 4 packages (clx, clx-common, clx-cli, clx-faststream-backend) into single `clx` package
  - Modern packaging with hatchling and pyproject.toml at repository root
  - 171/172 unit tests passing (99.4%)
  - Package moved to repository root following Python best practices
- ✅ Phase 5: docker-compose.yaml simplified - RabbitMQ & monitoring removed
- ✅ Phase 4: CLI defaults to SQLite
- ✅ SQLite infrastructure fully implemented

**Default Behavior**: `clx build` uses SQLite backend. No RabbitMQ setup required!

## Repository Structure

```
clx/                               # Repository root
├── src/clx/                       # CLX package source (v0.3.0)
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
│   │   │   └── faststream_backend.py  # RabbitMQ backend (legacy)
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
├── services/                      # Worker services (separate packages)
│   ├── notebook-processor/
│   ├── plantuml-converter/
│   └── drawio-converter/
│
├── pyproject.toml                 # Package configuration (hatchling)
├── uv.lock                        # uv lock file
├── tox.ini                        # Tox configuration
├── LICENSE, README.md, CLAUDE.md
├── MIGRATION_GUIDE_V0.3.md        # Migration guide from v0.2.x
├── docker-compose.yaml            # Service orchestration
└── Phase documentation files
```

## Package Structure

### Single Unified Package:  (v0.3.0)

The CLX package is now a single unified package with three main subpackages representing a clear three-layer architecture:

#### 1.  - Domain Logic

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
- **Backends**: SqliteBackend (primary), FastStreamBackend (legacy)

**Key Modules**:
- `backends/` - Backend implementations (sqlite, faststream)
- `database/` - SQLite job queue (schema, job_queue, db_operations)
- `messaging/` - Message payloads and results
- `workers/` - Worker management (worker_base, pool_manager, worker_executor)
- `logging/`, `services/`, `utils/`

**Dependencies**: `pydantic~=2.8.2`, `attrs`, `faststream[rabbit]`

#### 3. `clx.cli` - Command-Line Interface

**Purpose**: CLI tool for running course conversions

**Entry Point**: `clx` command (via `clx.cli.main:cli`)

**Main Features**:
- Course conversion
- File watching with watchdog
- Backend selection (SQLite default)
- Worker management

**Key Files**:
- `main.py` - Click-based CLI entry point
- `file_event_handler.py` - Watchdog file monitoring
- `git_dir_mover.py` - Git directory utilities

**Dependencies**: `click`, `watchdog`

### Installation

**Single package installation:**
```bash
# From repository root
pip install -e .

# Or with uv
uv pip install -e .
```

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
```


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

### Xvfb Setup (Required for DrawIO Worker)

The DrawIO converter requires a display server for headless rendering. In remote/headless environments (like Claude Code on the web), you need to start **Xvfb** (X virtual framebuffer).

**When is Xvfb needed?**
- Running DrawIO worker in direct execution mode
- Running integration tests that use DrawIO converter
- Running e2e tests that process Draw.io diagrams

**Starting Xvfb:**

```bash
# Start Xvfb on display :99 (runs in background)
Xvfb :99 -screen 0 1024x768x24 -ac +extension GLX +render -noreset &

# Set DISPLAY environment variable
export DISPLAY=:99
```

**Checking if Xvfb is running:**

```bash
# Check process
pgrep -x Xvfb

# If running, you'll see a PID number
# If not running, no output (need to start it)
```

**Stopping Xvfb:**

```bash
# Kill all Xvfb processes
pkill Xvfb
```

**Note**: The sessionStart hook does NOT automatically start Xvfb. You must start it manually when needed for DrawIO-related tasks.

### PlantUML Setup (Required for PlantUML Worker)

The PlantUML converter requires the PlantUML JAR file and Java Runtime Environment.

**When is PlantUML needed?**
- Running PlantUML worker in direct execution mode
- Running integration tests that use PlantUML converter
- Running e2e tests that process PlantUML diagrams

**Installing PlantUML:**

```bash
# 1. Download PlantUML JAR (if not already installed by sessionStart hook)
PLANTUML_VERSION="1.2024.6"
wget "https://github.com/plantuml/plantuml/releases/download/v${PLANTUML_VERSION}/plantuml-${PLANTUML_VERSION}.jar" \
  -O /usr/local/share/plantuml-${PLANTUML_VERSION}.jar

# 2. Create wrapper script for plantuml command
cat > /usr/local/bin/plantuml << 'EOF'
#!/bin/bash
PLANTUML_JAR="/usr/local/share/plantuml-1.2024.6.jar"
exec java -DPLANTUML_LIMIT_SIZE=8192 -jar "$PLANTUML_JAR" "$@"
EOF

chmod +x /usr/local/bin/plantuml

# 3. Set environment variable
export PLANTUML_JAR="/usr/local/share/plantuml-${PLANTUML_VERSION}.jar"
```

**Verifying PlantUML installation:**

```bash
# Check if PlantUML JAR exists
ls -lh /usr/local/share/plantuml-1.2024.6.jar

# Check if wrapper script works
plantuml -version

# Verify environment variable
echo $PLANTUML_JAR
# Should output: /usr/local/share/plantuml-1.2024.6.jar
```

**Required Dependencies:**
- Java Runtime Environment (JRE) 8 or higher

**Note**: The sessionStart hook attempts to install PlantUML automatically in remote environments. Manual installation is only needed if automatic installation fails or in local environments.

### DrawIO Setup (Required for DrawIO Worker)

The DrawIO converter requires the DrawIO desktop application and Xvfb for headless rendering.

**When is DrawIO needed?**
- Running DrawIO worker in direct execution mode
- Running integration tests that use DrawIO converter
- Running e2e tests that process Draw.io diagrams

**Installing DrawIO:**

```bash
# 1. Download DrawIO .deb package (if not already installed by sessionStart hook)
DRAWIO_VERSION="24.7.5"
wget "https://github.com/jgraph/drawio-desktop/releases/download/v${DRAWIO_VERSION}/drawio-amd64-${DRAWIO_VERSION}.deb" \
  -O /tmp/drawio-amd64-${DRAWIO_VERSION}.deb

# 2. Extract DrawIO binary from .deb package
dpkg -x /tmp/drawio-amd64-${DRAWIO_VERSION}.deb /tmp/drawio-extract

# 3. Create symlink to DrawIO binary
ln -sf /tmp/drawio-extract/opt/drawio/drawio /usr/local/bin/drawio

# 4. Start Xvfb (required for headless operation)
Xvfb :99 -screen 0 1024x768x24 -ac +extension GLX +render -noreset &

# 5. Set DISPLAY environment variable
export DISPLAY=:99
```

**Verifying DrawIO installation:**

```bash
# Check if DrawIO binary exists
ls -lh /usr/local/bin/drawio

# Check if Xvfb is running
pgrep -x Xvfb

# Test DrawIO (requires Xvfb to be running)
drawio --version

# Verify environment variable
echo $DISPLAY
# Should output: :99
```

**Required Dependencies:**
- Xvfb (X virtual framebuffer)
- Various system libraries (usually available in Debian-based systems)

**Note**: The sessionStart hook attempts to install DrawIO automatically in remote environments. Manual installation is only needed if automatic installation fails or in local environments. **DrawIO always requires Xvfb to be started manually** (see Xvfb Setup section above).

### Test Organization

- **Unit tests**: Fast, mocked dependencies, no markers
- **Integration tests**: Real workers, SQLite database, `@pytest.mark.integration`
  - **Requires Xvfb** if testing DrawIO converter
- **E2E tests**: Full course conversion, `@pytest.mark.e2e`
  - **Requires Xvfb** if course includes Draw.io diagrams
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

# Install in development mode (from repository root)
pip install -e .

# Or with uv
uv pip install -e .

# Verify installation
clx --help
python -c "from clx import Course; print('✓ CLX installed successfully!')"
```

### Native Worker Setup (Direct Execution Mode)

The CLX project includes native workers that can run directly on your system (without Docker). The sessionStart hook automatically attempts to install the required external tools in remote environments.

**External Tools Required:**
- **PlantUML** - For converting PlantUML diagrams to images
- **DrawIO** - For converting Draw.io diagrams to images
- **Xvfb** - For headless rendering of DrawIO diagrams

**Automatic Installation by sessionStart Hook:**

In remote environments (Claude Code on the web), the sessionStart hook will attempt to:
1. Download PlantUML JAR from GitHub releases or use the repository file if Git LFS is set up
2. Download DrawIO .deb package from GitHub releases or use the repository file if Git LFS is set up
3. Install both tools to standard locations

**⚠️ Note**: If downloads fail (e.g., GitHub access restrictions), you'll need to install manually. See instructions below.

**Skipping Downloads:**

In restricted environments where downloads always fail, you can skip download attempts entirely:

```bash
# Set environment variable before running sessionStart
export CLX_SKIP_DOWNLOADS=1

# Or set it in your shell configuration
echo 'export CLX_SKIP_DOWNLOADS=1' >> ~/.bashrc
```

This will:
- Skip all download attempts (faster execution)
- Show clear messages that tools are not available
- Direct you to manual installation instructions

**Git LFS Files in Repository:**
- `services/plantuml-converter/plantuml-1.2024.6.jar` - PlantUML JAR file (Git LFS pointer, 22MB actual)
- `services/drawio-converter/drawio-amd64-24.7.5.deb` - DrawIO Debian package (Git LFS pointer, 98MB actual)

If these files are Git LFS pointers, the sessionStart hook will fall back to downloading from GitHub releases (unless `CLX_SKIP_DOWNLOADS` is set).

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

**Note**: As of Phase 5 (2025-11-14), docker-compose.yaml now uses the simplified SQLite-based architecture without RabbitMQ or monitoring stack.

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

**Legacy RabbitMQ Setup**: If you need the old RabbitMQ-based setup, use `docker-compose.legacy.yaml`:
```bash
docker-compose -f docker-compose.legacy.yaml up -d
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

**Current Version**: 0.3.0

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

**Last Updated**: 2025-11-14 (Phase 5 complete)
**Repository**: https://github.com/hoelzl/clx/
**Issues**: https://github.com/hoelzl/clx/issues
