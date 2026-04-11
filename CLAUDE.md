# CLM - AI Assistant Guide

Session-start orientation for AI assistants working on the CLM (Coding-Academy
Lecture Manager eXperimental) codebase. This file holds only the information
you cannot discover cheaply from the code, a single doc lookup, or
`clm info`. Everything else is in its canonical home — see the
**Documentation Map** below.

## Project Overview

**CLM** converts educational materials (Jupyter notebooks, PlantUML diagrams,
Draw.io diagrams) into multiple output formats (HTML slides, executed
notebooks, extracted code) for multiple audiences (students, solutions,
speaker notes).

**Version**: 1.2.0 | **License**: MIT | **Python**: 3.11, 3.12, 3.13, 3.14

## Architecture

Four-layer architecture with an SQLite job queue and Direct/Docker worker
execution. Full details in `docs/developer-guide/architecture.md`.

```
clm/
├── core/           # Domain logic (Course, Section, Topic, CourseFile)
├── infrastructure/ # Job queue, worker management, backends, LLM client
├── workers/        # Worker implementations (notebook, plantuml, drawio)
└── cli/            # Command-line interface
```

Optional extensions live alongside this core: `clm.notebooks` (slide
utilities), `clm.slides` (authoring tools), `clm.mcp` (MCP server),
`clm.voiceover` (video pipeline), `clm.recordings` (recording workflow). See
the "Extended Modules" section of `architecture.md` for entry points.

## Installation

```bash
pip install -e .                  # core only
pip install -e ".[all]"           # everything (recommended for development)
```

For the full list of optional extras (`[notebook]`, `[plantuml]`, `[drawio]`,
`[all-workers]`, `[recordings]`, `[summarize]`, `[voiceover]`, `[slides]`,
`[mcp]`, `[ml]`, `[dev]`, `[tui]`, `[web]`) see
`docs/user-guide/installation.md`.

## Testing

```bash
pytest                    # Fast suite only (~30s, runs via pre-commit hook)
pytest -m "not docker"    # Full suite minus Docker tests (~2 min, pre-release gate)
pytest -m ""              # Everything including docker/slow/integration/e2e
```

Tests run in parallel by default via `pytest-xdist` (`-n auto`). The fast
suite excludes `slow`, `integration`, `e2e`, `db_only`, and `docker` markers.
Docker-marked tests require CI-built images and are validated in CI only —
**use `pytest -m "not docker"` for pre-release local testing**. Full marker
list and strategy in `docs/developer-guide/testing.md`.

## Code Conventions

- **Type hints**: required for all public APIs.
- **Async/await**: preferred for I/O operations.
- **Dataclasses**: `attrs @define` for internal structures, Pydantic for
  messages crossing the worker/CLI boundary.
- **Logging**: `logging.getLogger(__name__)` — never `print()` in library code.

## Info Topics Maintenance Rule (CRITICAL)

The `clm info <topic>` command outputs version-accurate markdown documentation
that **downstream agents in course repositories rely on** to understand CLM's
current behavior. The content lives in `src/clm/cli/info_topics/*.md` and uses
`{version}` placeholders replaced at output time.

**When you change CLM behavior that affects how course repositories are
structured or built, you MUST update the corresponding info topic.**

| Change | Info topic to update |
|---|---|
| Spec file format (elements, attributes, defaults) | `src/clm/cli/info_topics/spec-files.md` |
| CLI commands, flags, options | `src/clm/cli/info_topics/commands.md` |
| Breaking changes, migration steps | `src/clm/cli/info_topics/migration.md` |

If the info topics are stale, downstream agents will produce incorrect output.
Keeping these files current is as important as updating tests. Do not
hardcode version numbers — use `{version}`.

## Release Rules

Full procedure lives in `docs/developer-guide/releasing.md`. The hard rules:

- **Never publish a release without updating documentation first.**
- **Never publish a release if any local test fails.**
- **Never publish unless CI has passed for the tagged commit.**
- Use `pytest -m "not docker"` for local pre-release testing (Docker tests
  run in CI only).

## Git Workflow

- Branch prefix: `claude/` for AI-generated branches.
- **Pre-commit hooks**: install with `uv run pre-commit install`. Runs ruff,
  mypy, and the fast test suite automatically.
- Manual checks: `uv run ruff check src/ tests/` and
  `uv run ruff format src/ tests/`.
- Commits that fail a hook did **not** happen — fix the issue, re-stage, and
  create a **new** commit. Never `--amend` a commit the hook rejected.

## Documentation Map

When you need information, go to its canonical home rather than guessing:

| You need... | Run / read |
|---|---|
| CLI commands, flags, options (version-accurate) | `clm info commands` |
| Course spec XML format (version-accurate) | `clm info spec-files` |
| Breaking-change migrations | `clm info migration` |
| System architecture, module overview | `docs/developer-guide/architecture.md` |
| Per-version feature history | `CHANGELOG.md` |
| Environment variables & config | `docs/user-guide/configuration.md` |
| Installation & optional extras | `docs/user-guide/installation.md` |
| Testing markers & strategy | `docs/developer-guide/testing.md` |
| Release procedure (full steps) | `docs/developer-guide/releasing.md` |
| Recording workflow & backends | `docs/user-guide/recordings.md`, `recordings-auphonic.md` |
| Voiceover pipeline | `docs/user-guide/voiceover.md` |
| Troubleshooting | `docs/user-guide/troubleshooting.md` |
| Known issues / bugs / TODOs | `docs/claude/TODO.md` |
| AI assistant design docs | `docs/claude/design/` |

**Before proposing changes to code**, read the file first. **Before
recommending a command or flag**, verify it exists in `clm info commands` —
do not fabricate options.

## Troubleshooting Quick Hits

1. **Tests failing**: check external tools (PlantUML JAR, Draw.io executable)
   and enable logging with `CLM_ENABLE_TEST_LOGGING=1`.
2. **Worker issues**: run `python diagnose_workers.py`.
3. **Import errors**: ensure `pip install -e .` was run in the correct
   environment.

---

**Repository**: https://github.com/hoelzl/clm/ | **Issues**: https://github.com/hoelzl/clm/issues

**Last Updated**: 2026-04-11
