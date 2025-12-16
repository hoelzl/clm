# CLX - Coding-Academy Lecture Manager eXperimental

[![CI](https://github.com/hoelzl/clx/actions/workflows/ci.yml/badge.svg)](https://github.com/hoelzl/clx/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/hoelzl/clx/branch/master/graph/badge.svg)](https://codecov.io/gh/hoelzl/clx)

**Version**: 0.6.0 | **License**: MIT | **Python**: 3.11, 3.12, 3.13

CLX is a course content processing system that converts educational materials (Jupyter notebooks, PlantUML diagrams, Draw.io diagrams) into multiple output formats.

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/hoelzl/clx.git
cd clx

# Install with all dependencies (recommended)
pip install -e ".[all]"

# Or minimal install (core only)
pip install -e .
```

### Basic Usage

```bash
# Convert a course
clx build /path/to/course.xml

# Watch for changes and auto-rebuild
clx build /path/to/course.xml --watch

# Show help
clx --help
```

## Features

- **Multiple Output Formats**: HTML slides, Jupyter notebooks, PDF, extracted code
- **Multi-Language Notebooks**: Python, C++, C#, Java, TypeScript
- **Diagram Support**: PlantUML and Draw.io conversion
- **Multiple Output Targets**: Separate student/solution/instructor outputs
- **Watch Mode**: Auto-rebuild on file changes
- **Incremental Builds**: Content-based caching

## Documentation

**For Users**:
- [User Guide](docs/user-guide/README.md) - Complete usage guide
- [Quick Start](docs/user-guide/quick-start.md) - Build your first course
- [Spec File Reference](docs/user-guide/spec-file-reference.md) - Course XML format
- [Configuration](docs/user-guide/configuration.md) - Configuration options

**For Developers**:
- [Contributing Guide](CONTRIBUTING.md) - How to contribute
- [Developer Guide](docs/developer-guide/README.md) - Development documentation
- [Architecture](docs/developer-guide/architecture.md) - System design
- [CLAUDE.md](CLAUDE.md) - AI assistant reference

## Development Setup

```bash
# Install pre-commit hooks (recommended)
uv run pre-commit install

# This enables automatic linting (ruff) and type checking (mypy) on every commit
```

## Testing

```bash
# Run unit tests
pytest

# Run all tests (unit, integration, e2e)
pytest -m ""

# Run with coverage
pytest --cov=src/clx
```

## License

MIT License - see [LICENSE](LICENSE) for details.

## Links

- **Repository**: https://github.com/hoelzl/clx/
- **Issues**: https://github.com/hoelzl/clx/issues
