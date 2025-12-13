# CLX Developer Guide

Welcome to the CLX developer documentation! This guide is for developers who want to contribute to the CLX project.

## Quick Links

- **[Architecture](architecture.md)** - System architecture and design
- **[Building](building.md)** - Building Docker services
- **[Testing](testing.md)** - Testing strategy and practices
- **[Direct Workers](direct_worker_execution.md)** - Direct worker execution mode
- **[Worker Lifecycle](worker-lifecycle-management.md)** - Worker lifecycle management
- **[Implementation Summary](IMPLEMENTATION_SUMMARY.md)** - Technical implementation details

## Getting Started

### Prerequisites

- Python 3.11, 3.12, or 3.13
- Git
- Docker (optional, for Docker mode workers)
- uv or pip (uv recommended)

### Clone and Install

```bash
# Clone the repository
git clone https://github.com/hoelzl/clx.git
cd clx

# Install in development mode (core only - minimal)
pip install -e .

# Or with all dependencies (RECOMMENDED for development)
pip install -e ".[all]"

# Or specific dependency groups
pip install -e ".[all-workers,dev]"  # Workers + dev tools
pip install -e ".[notebook,dev]"     # Just notebook worker + dev tools

# Or with uv (recommended)
uv pip install -e .
uv pip install -e ".[all]"

# Verify installation
clx --help
```

### Set Up Pre-commit Hooks

We use [pre-commit](https://pre-commit.com/) to automatically run linting and type checking before each commit:

```bash
# Install pre-commit hooks (recommended)
uv run pre-commit install

# Or with pip
pre-commit install
```

This will:
- Run **ruff** for linting and auto-fixing code style issues
- Run **ruff format** for consistent code formatting
- Run **mypy** for static type checking

To run hooks manually on all files:
```bash
uv run pre-commit run --all-files
```

### Running Tests

```bash
# Fast unit tests only (default)
pytest

# Include integration tests
pytest -m integration

# Include end-to-end tests
pytest -m e2e

# Run all tests
pytest -m ""

# Run with coverage
pytest --cov=src/clx
```

**Note**: Integration and e2e tests may require PlantUML and DrawIO. See [Architecture](architecture.md) for setup instructions.

## Project Structure

```
clx/
├── src/clx/              # Package source (v0.4.0)
│   ├── core/             # Domain logic (Course, Section, Topic)
│   ├── infrastructure/   # Job queue, worker management, backends
│   ├── workers/          # Worker implementations (NEW in v0.4.0)
│   │   ├── notebook/     # Notebook processing worker
│   │   ├── plantuml/     # PlantUML conversion worker
│   │   └── drawio/       # Draw.io conversion worker
│   └── cli/              # Command-line interface
├── tests/                # All tests (221 total)
│   ├── core/             # Core module tests
│   ├── infrastructure/   # Infrastructure tests
│   ├── cli/              # CLI tests
│   └── e2e/              # End-to-end tests
├── docker/               # Docker build files
│   ├── notebook/
│   ├── plantuml/
│   └── drawio/
├── docs/                 # Documentation
└── pyproject.toml        # Package configuration
```

## Development Workflow

### Making Changes

1. **Create a branch**:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes**:
   - Follow existing code style
   - Add type hints
   - Write tests for new functionality

3. **Run tests**:
   ```bash
   pytest
   ```

4. **Commit your changes**:
   ```bash
   git add .
   git commit -m "Add feature: description"
   ```

5. **Push and create pull request**:
   ```bash
   git push origin feature/your-feature-name
   ```

### Code Style

- **Type hints**: Use extensively, especially in public APIs
- **Docstrings**: Document public functions and classes
- **Logging**: Use `logging` module, not print statements
- **Error handling**: Include context in error messages

### Testing Guidelines

- Write unit tests for all new code
- Use appropriate test markers (`@pytest.mark.integration`, `@pytest.mark.e2e`)
- Test both success and failure cases
- Use fixtures for common setup

## Architecture Overview

CLX uses a clean four-layer architecture:

```
┌─────────────────────────────────────────┐
│          clx.core (Domain)               │
│  Course, Section, Topic, CourseFile      │
└─────────────┬───────────────────────────┘
              │
┌─────────────▼───────────────────────────┐
│    clx.infrastructure (Runtime)          │
│  JobQueue, Worker Management, Backends   │
└─────────────┬───────────────────────────┘
              │
┌─────────────▼───────────────────────────┐
│       clx.workers (Implementations)      │
│  notebook, plantuml, drawio (NEW v0.4.0) │
└─────────────┬───────────────────────────┘
              │
┌─────────────▼───────────────────────────┐
│          clx.cli (Interface)             │
│        Click-based CLI                   │
└──────────────────────────────────────────┘
```

**Key Principles**:
- Domain layer has no infrastructure dependencies
- Workers integrated into main package with optional dependencies
- SQLite-based job queue (no RabbitMQ)
- Direct file system access (no message serialization)
- Worker pools (Docker or direct execution)

For detailed architecture information, see [Architecture](architecture.md).

## Common Development Tasks

### Adding a New Operation

1. Create operation class in `src/clx/core/operations/`
2. Inherit from appropriate base class
3. Implement required methods
4. Add tests in `tests/core/operations/`

### Adding a New File Type

1. Create file class in `src/clx/core/course_files/`
2. Inherit from `CourseFile`
3. Implement `process()` method
4. Register in file type detection
5. Add tests in `tests/core/course_files/`

### Adding a New Worker Service

1. Create worker module in `src/clx/workers/`
2. Implement worker using `WorkerBase`
3. Create Dockerfile in `docker/` with BuildKit cache mounts
4. Update `build-services.sh` to include the new worker
5. Add tests with appropriate markers

## Documentation

- **User Documentation**: `docs/user-guide/`
- **Developer Documentation**: `docs/developer-guide/` (you are here)
- **API Reference**: See source code docstrings and type hints
- **Architecture Decisions**: `docs/archive/migration-history/`

## Getting Help

- **Issues**: https://github.com/hoelzl/clx/issues
- **Discussions**: GitHub Discussions (if enabled)
- **[Known Issues](../claude/TODO.md)**: Current bugs and planned improvements
- **CLAUDE.md**: Comprehensive guide for AI assistants (useful for developers too!)

## Contributing

See [CONTRIBUTING.md](../../CONTRIBUTING.md) in the repository root for detailed contribution guidelines.

## License

MIT License - see [LICENSE](../../LICENSE) for details.
