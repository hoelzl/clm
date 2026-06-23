# CLM - Coding-Academy Lecture Manager

[![CI](https://github.com/hoelzl/clm/actions/workflows/ci.yml/badge.svg)](https://github.com/hoelzl/clm/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/hoelzl/clm/branch/master/graph/badge.svg)](https://codecov.io/gh/hoelzl/clm)

**Version**: 1.16.0 | **License**: MIT | **Python**: 3.12, 3.13, 3.14

CLM is a course content processing system that converts educational materials (Jupyter notebooks, PlantUML diagrams, Draw.io diagrams) into multiple output formats.

## Quick Start

### Installation

```bash
# Install from PyPI
pip install coding-academy-lecture-manager

# Or with all optional dependencies (workers, TUI, web dashboard)
pip install "coding-academy-lecture-manager[all]"
```

For development, clone the repository and install in editable mode:

```bash
git clone https://github.com/hoelzl/clm.git
cd clm
pip install -e ".[all]"
```

### Basic Usage

```bash
# Convert a course
clm build /path/to/course.xml

# Watch for changes and auto-rebuild
clm build /path/to/course.xml --watch

# Show help
clm --help
```

## Features

- **Multiple Output Formats**: HTML slides, Jupyter notebooks, extracted code
- **Multi-Language Notebooks**: Python, C++, C#, Java, TypeScript, Markdown
- **Diagram Support**: PlantUML and Draw.io conversion
- **Multiple Output Targets**: Separate student/solution/instructor outputs
- **Shared-Source Includes**: Declare `<include source="…" as="…"/>` on a `<topic>` or `<section>` to splice a canonical Python package (or any file/directory) into multiple topics at build time. `clm course sync-includes` materializes the same sources on disk so local notebook execution (VS Code, JupyterLab) finds them, with a `.clm-include` ledger for safe cleanup.
- **Output-Write Deduplication**: When the same file is written to the same output path by multiple producers, CLM deduplicates the write and surfaces a `output_dedup_count` / `output_conflicts` summary so you can spot accidental cross-topic collisions.
- **Watch Mode**: Auto-rebuild on file changes
- **Incremental Builds**: Content-based caching
- **LLM Summaries**: Generate course summaries with `clm export summary` using any OpenAI-compatible LLM API
- **Recording Management**: Manage video recording workflows with pluggable backends — local ONNX pipeline, iZotope RX 11 external tool, or Auphonic cloud processing — plus assembly, job tracking, and per-course status (`clm recordings`)
- **MCP Server**: Model Context Protocol server for AI-assisted slide authoring (`clm mcp`) with 16 tools for course navigation, validation, normalization, and bilingual editing
- **Slide Authoring Tools**: Split-deck authoring sync (`clm slides sync` — the funnel that keeps both halves of a `.de`/`.en` pair consistent), topic resolution (`clm course resolve-topic`), fuzzy search (`clm slides search`), spec/slide validation (`clm validate`), normalization (`clm slides normalize`), bilingual language view (`clm slides language-view`), voiceover extraction (`clm voiceover extract`), LLM-driven voiceover coverage check (`clm slides coverage`), bilingual ↔ per-language file conversion (`clm slides split` / `clm slides unify`), and structured JSON outlines (`clm export outline --format json`). Lower-level plumbing (`clm slides assign-ids`, `clm slides suggest-sync`) stays available by name for scripts and agents.
- **Voiceover Sync**: Synchronize video recordings with slides to auto-generate speaker notes (`clm voiceover sync`), with multi-file input for part-based recordings and intelligent merge mode that preserves existing content while integrating transcript additions and filtering recording noise
- **LLM Polish**: Clean up speaker notes with LLM-powered text polishing (`clm polish`)
- **Git Integration**: Manage output repos with `clm git init/sync/status`, including `--amend` and `--force-with-lease` for iterative workflows
- **Flexible Remote URLs**: Configurable git remote URL templates for SSH, custom hosts, etc.

## Documentation

**For Users**:
- [User Guide](docs/user-guide/README.md) - Complete usage guide
- [Quick Start](docs/user-guide/quick-start.md) - Build your first course
- [Spec File Reference](docs/user-guide/spec-file-reference.md) - Course XML format
- [Configuration](docs/user-guide/configuration.md) - Configuration options
- [Changelog](CHANGELOG.md) - Version history

**For Developers**:
- [Contributing Guide](CONTRIBUTING.md) - How to contribute
- [Developer Guide](docs/developer-guide/README.md) - Development documentation
- [Architecture](docs/developer-guide/architecture.md) - System design
- [AGENTS.md](AGENTS.md) - AI assistant reference (imported by `CLAUDE.md` for Claude Code)

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
pytest --cov=src/clm
```

## License

MIT License - see [LICENSE](LICENSE) for details.

## Links

- **Repository**: https://github.com/hoelzl/clm/
- **Issues**: https://github.com/hoelzl/clm/issues
