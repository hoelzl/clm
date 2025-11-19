# CLX - Coding-Academy Lecture Manager eXperimental

[![CI](https://github.com/hoelzl/clx/actions/workflows/ci.yml/badge.svg)](https://github.com/hoelzl/clx/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/hoelzl/clx/branch/master/graph/badge.svg)](https://codecov.io/gh/hoelzl/clx)

**Version**: 0.4.0
**License**: MIT
**Python**: 3.11, 3.12, 3.13

CLX is a course content processing system that converts educational materials (Jupyter notebooks, PlantUML diagrams, Draw.io diagrams) into multiple output formats.

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/hoelzl/clx.git
cd clx

# Install core package (minimal)
pip install -e .

# Or with uv (recommended)
uv pip install -e .

# Install with worker dependencies (for direct execution mode)
pip install -e ".[all-workers]"  # All workers (notebook, plantuml, drawio)
pip install -e ".[notebook]"     # Just notebook processing
pip install -e ".[plantuml]"     # Just PlantUML conversion
pip install -e ".[drawio]"       # Just Draw.io conversion

# Install with optional UI features
pip install -e ".[tui]"      # TUI monitoring (clx monitor)
pip install -e ".[web]"      # Web dashboard (clx serve)

# Install for development
pip install -e ".[dev]"      # Development tools (pytest, mypy, ruff)
pip install -e ".[all]"      # Everything (required for full testing)
```

### Basic Usage

```bash
# Convert a course
clx build /path/to/course.yaml

# Watch for changes and auto-rebuild
clx build /path/to/course.yaml --watch

# System monitoring
clx status                 # Show system status (workers, jobs, health)
clx workers list           # List all workers
clx monitor                # Real-time TUI monitoring (requires [tui])
clx serve                  # Web dashboard (requires [web])

# Show help
clx --help
```

## Features

- ✅ **Single unified package** - Simple installation with `pip install -e .`
- ✅ **Integrated workers** - Workers built into main package (`clx.workers`)
- ✅ **Flexible dependencies** - Install only what you need with optional extras
- ✅ **SQLite-based architecture** - No RabbitMQ setup required
- ✅ **Modern packaging** - Built with hatchling, compatible with uv and poetry
- ✅ **Worker modes** - Direct execution (fast) or Docker (isolated)
- ✅ **File watching** - Auto-rebuild on file changes
- ✅ **Multiple output formats** - HTML, Jupyter notebooks, slides, PDF
- ✅ **Multi-language support** - Python, C++, C#, Java, TypeScript notebooks
- ✅ **ML support** - Optional PyTorch, FastAI, transformers for advanced notebooks
- ✅ **Monitoring tools** - CLI status, TUI monitor, web dashboard
- ✅ **Worker management** - Auto-start, persistent services, health monitoring

## Architecture

CLX uses a clean four-layer architecture:

```
clx/
├── core/           # Domain logic (Course, Section, Topic)
├── infrastructure/ # Job queue, worker management, backends
├── workers/        # Worker implementations (notebook, plantuml, drawio)
└── cli/            # Command-line interface
```

## Testing

**Prerequisites**: Install with all dependencies before running tests:
```bash
pip install -e ".[all]"
```

**Running tests**:
```bash
# Run fast unit tests (default)
pytest

# Run all tests including integration and e2e
pytest -m ""

# Run specific test types
pytest -m integration
pytest -m e2e
```

**Test coverage**: 221 tests (171/172 unit tests passing - 99.4%)

## Documentation

**For Users**:
- **[User Guide](docs/user-guide/README.md)** - Complete guide for using CLX
- **[Installation Guide](docs/user-guide/installation.md)** - Setup instructions
- **[Quick Start Guide](docs/user-guide/quick-start.md)** - Build your first course in 5 minutes
- **[Configuration Guide](docs/user-guide/configuration.md)** - Course configuration options
- **[Troubleshooting](docs/user-guide/troubleshooting.md)** - Common issues and solutions

**For Developers**:
- **[Contributing Guide](CONTRIBUTING.md)** - How to contribute to CLX
- **[Developer Guide](docs/developer-guide/README.md)** - Development documentation
- **[Architecture](docs/developer-guide/architecture.md)** - System design and architecture
- **[CLAUDE.md](CLAUDE.md)** - Comprehensive guide for AI assistants

**Migration**:
- **[Migration Guide v0.3](docs/MIGRATION_GUIDE_V0.3.md)** - Upgrading from v0.2.x to v0.3.1

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed development guidelines.

```bash
# Install all dependencies (required for development and testing)
pip install -e ".[all]"

# Or install specific groups
pip install -e ".[all-workers,dev]"  # Workers + dev tools
pip install -e ".[notebook,dev]"     # Just notebook worker + dev tools

# Run tests with coverage
pytest --cov=src/clx

# Build Docker services (optional, for Docker mode)
./build-services.sh  # Linux/macOS
.\build-services.ps1 # Windows

# Start services (optional, for Docker mode)
docker-compose up -d
```

## Package Structure

```
clx/
├── src/clx/              # Package source
│   ├── core/             # Course processing logic
│   ├── infrastructure/   # Job queue & worker management
│   ├── workers/          # Worker implementations (NEW in v0.4.0)
│   │   ├── notebook/     # Notebook processing
│   │   ├── plantuml/     # PlantUML conversion
│   │   └── drawio/       # Draw.io conversion
│   └── cli/              # CLI interface
├── tests/                # All tests
├── services/             # Legacy (Docker builds only)
└── pyproject.toml        # Package configuration
```

## Recent Changes

### v0.4.0 - Worker Integration

🎉 **Workers integrated into main package**

- ✅ Workers now part of `clx.workers` package (notebook, plantuml, drawio)
- ✅ Optional dependencies for each worker: `[notebook]`, `[plantuml]`, `[drawio]`
- ✅ No separate package installation needed for direct execution mode
- ✅ Simplified setup: `pip install -e ".[all-workers]"` installs all workers
- ✅ Core package remains minimal (can use Docker mode without worker deps)
- ✅ New `[ml]` extra for machine learning packages (PyTorch, FastAI, transformers)

### v0.3.1 - Package Consolidation

🎉 **Consolidated 4 packages into a single unified package**

- ✅ Simpler installation: `pip install -e .` instead of 4 separate packages
- ✅ Cleaner imports: `from clx.core import Course`
- ✅ Modern packaging with hatchling
- ✅ Package at repository root (following Python best practices)
- ✅ All tests migrated and passing

See [MIGRATION_GUIDE_V0.3.md](docs/MIGRATION_GUIDE_V0.3.md) for upgrading from v0.2.x.

## License

MIT License - see [LICENSE](LICENSE) for details.

## Links

- **Repository**: https://github.com/hoelzl/clx/
- **Issues**: https://github.com/hoelzl/clx/issues
