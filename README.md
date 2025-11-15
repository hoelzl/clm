# CLX - Coding-Academy Lecture Manager eXperimental

**Version**: 0.3.0
**License**: MIT
**Python**: 3.10, 3.11, 3.12

CLX is a course content processing system that converts educational materials (Jupyter notebooks, PlantUML diagrams, Draw.io diagrams) into multiple output formats.

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/hoelzl/clx.git
cd clx

# Install with pip
pip install -e .

# Or with uv (recommended)
uv pip install -e .
```

### Basic Usage

```bash
# Convert a course
clx build /path/to/course.yaml

# Watch for changes and auto-rebuild
clx build /path/to/course.yaml --watch

# Show help
clx --help
```

## Features

- ✅ **Single unified package** - Simple installation with `pip install -e .`
- ✅ **SQLite-based architecture** - No RabbitMQ setup required
- ✅ **Modern packaging** - Built with hatchling, compatible with uv and poetry
- ✅ **Worker modes** - Direct execution (fast) or Docker (isolated)
- ✅ **File watching** - Auto-rebuild on file changes
- ✅ **Multiple output formats** - HTML, PDF, slides, and more
- ✅ **Multi-language support** - Python, C++, C#, Java, TypeScript notebooks

## Architecture

CLX uses a clean three-layer architecture:

```
clx/
├── core/           # Domain logic (Course, Section, Topic)
├── infrastructure/ # Job queue, workers, backends
└── cli/            # Command-line interface
```

## Testing

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
- **[Migration Guide v0.3](docs/MIGRATION_GUIDE_V0.3.md)** - Upgrading from v0.2.x to v0.3.0

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed development guidelines.

```bash
# Install development dependencies
pip install -e ".[dev]"

# Run tests with coverage
pytest --cov=src/clx

# Build Docker services
./build-services.sh  # Linux/macOS
.\build-services.ps1 # Windows

# Start services
docker-compose up -d
```

## Package Structure

```
clx/
├── src/clx/              # Package source
│   ├── core/             # Course processing
│   ├── infrastructure/   # Job queue & workers
│   └── cli/              # CLI interface
├── tests/                # All tests
├── services/             # Worker services
└── pyproject.toml        # Package configuration
```

## Changes in v0.3.0

🎉 **Major refactoring**: Consolidated 4 packages into a single unified package

- ✅ Simpler installation: `pip install -e .` instead of 4 separate packages
- ✅ Cleaner imports: `from clx.core import Course`
- ✅ Modern packaging with hatchling
- ✅ Package at repository root (following Python best practices)
- ✅ All tests migrated and passing

See [MIGRATION_GUIDE_V0.3.md](MIGRATION_GUIDE_V0.3.md) for upgrading from v0.2.x.

## License

MIT License - see [LICENSE](LICENSE) for details.

## Links

- **Repository**: https://github.com/hoelzl/clx/
- **Issues**: https://github.com/hoelzl/clx/issues
