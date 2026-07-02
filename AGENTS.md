# CLM — Agent Guide

Session-start orientation for **any** AI coding agent (zcode/GLM, Claude Code,
or others) working on the CLM (Coding-Academy Lecture Manager eXperimental)
codebase. This is the single source of truth for agent session-start guidance:
other agents read it natively, and Claude Code reads it via a one-line
`@AGENTS.md` import in `CLAUDE.md`. Edit here, never in `CLAUDE.md`. Read it at
the start of a session.

This file holds only the information you cannot discover cheaply from the
code, a single doc lookup, or `clm info`. Everything else is in its canonical
home — see the **Documentation Map** below.

## Project Overview

**CLM** converts educational materials (Jupyter notebooks, PlantUML diagrams,
Draw.io diagrams) into multiple output formats (HTML slides, executed
notebooks, extracted code) for multiple audiences (students, solutions,
speaker notes).

**Version**: 1.18.0 | **License**: MIT | **Python**: 3.12, 3.13, 3.14

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
pip install -e ".[all]"           # everything clm needs for dev (or: uv sync)
```

`[all]` deliberately **excludes** `[ml]` (course-runtime PyTorch/pandas stack,
not imported by clm; add it with `pip install -e ".[all,ml]"`). JupyterLite is
**not a clm extra** — clm only shells out to `jupyter lite build`, which now
runs in an isolated `uvx` tool env (pinned in
`src/clm/workers/jupyterlite/builder.py`), so it needs no install step beyond
having `uv` on PATH. For the full list of optional extras (`[notebook]`,
`[plantuml]`, `[drawio]`, `[all-workers]`, `[recordings]`, `[summarize]`,
`[voiceover]`, `[slides]`, `[gcal]`, `[mcp]`, `[ml]`, `[dev]`, `[tui]`, `[web]`)
see `docs/user-guide/installation.md`.

## Testing

```bash
pytest                    # Fast suite only (~72s; runs on the pre-PUSH hook)
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
- **Scripts and tooling wrappers**: prefer Python over bash. CLM is a
  Windows-first project and the rest of the toolchain is Python — a `.py`
  script runs identically on Windows/Linux/macOS without depending on Git
  Bash being on PATH, and benefits from the same ruff/mypy checks as the
  rest of the codebase. Reach for bash only when a shell-specific feature
  is genuinely required and the script will never run on Windows.

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

## Changelog Entries

Never edit the `## [Unreleased]` section of `CHANGELOG.md` in a PR — every
concurrent PR inserting there made changelog merge conflicts near-universal.
Instead, add a fragment file `changelog.d/<pr-or-issue>-<slug>.<type>.md`
(type = `added` | `changed` | `deprecated` | `removed` | `fixed` |
`security`) containing the finished markdown bullet(s). Fragments are folded
into `CHANGELOG.md` at release time by `python scripts/collect_changelog.py
X.Y.Z`. Conventions: `changelog.d/README.md`.

## Release Rules

Full procedure lives in `docs/developer-guide/releasing.md`. The hard rules:

- **Never publish a release without updating documentation first.**
- **Never publish a release if any local test fails.**
- **Never publish unless CI has passed for the tagged commit.**
- Use `pytest -m "not docker"` for local pre-release testing (Docker tests
  run in CI only).
- **Publishing is automated — do not run `uv publish` / `gh release create` by
  hand.** Landing a `Bump version …` commit on `master` (e.g. merging a bump PR
  with a **merge commit**, not squash/rebase) — or pushing a `vX.Y.Z` tag —
  triggers `.github/workflows/release.yml`, which gates on CI being green for
  that commit, then publishes to PyPI via OIDC Trusted Publishing and creates
  the GitHub Release. Your job is the docs + CHANGELOG + version bump; the
  workflow does the rest. (A manual fallback is documented in `releasing.md`.)

## Git Workflow

- Branch prefix: `claude/` for AI-generated branches.
- **Commit, push, and open PRs autonomously — do not wait for a go-ahead.**
  When a task is finished (in particular after a significant chunk of work is
  committed), push the branch without asking. When a feature or bugfix is
  complete and you are working in a worktree, create a Pull Request without
  asking for permission. Asking is only warranted for genuinely destructive
  operations (force-push to a shared branch, history rewrites) or pushes to
  `master` itself.
- **Git hooks**: install with `uv run pre-commit install` (wires up both hook
  types via `default_install_hook_types`). The **pre-commit** hook runs ruff +
  mypy (fast, every commit); the **pre-push** hook runs the fast test suite
  (~72s) on `git push`. So commits are near-instant and the test gate fires once
  before a push. Re-run `pre-commit install` if you set up hooks before the
  pre-push split landed.
- Manual checks: `uv run ruff check src/ tests/` and
  `uv run ruff format src/ tests/`.
- Commits that fail a hook did **not** happen — fix the issue, re-stage, and
  create a **new** commit. Never `--amend` a commit the hook rejected.
- **`master` belongs to the main repo — a worktree must NEVER switch to it.**
  The `master` branch is checked out in the main repo (`C:/…/Projects/clm`), and
  Git forbids the *same* branch being checked out in two worktrees at once (no
  config changes this). So a worktree never runs `git switch master` /
  `git checkout master` — that command can only fail or, if forced, corrupt the
  main checkout. To put a worktree on the latest master **content**, reset the
  worktree's **own** branch onto `origin/master` instead. From inside the
  worktree:
  ```
  git fetch origin && git switch -C <worktree-branch> origin/master
  ```
  This keeps you on your per-worktree branch (e.g. `worktree-<name>`) with
  master's exact tree — non-destructive when that branch is already merged/behind.
  To start fresh work, branch off it: `git switch -c claude/issue-NNN-...`.
  **Never** set `core.bare=true` on the main repo to "free up" `master` — that
  strips the main checkout's working tree and is a recurring source of breakage.

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
| Agent-facing design docs (decisions + rationale) | `docs/claude/design/` |
| Cross-session handovers / investigations | `docs/claude/` (`*-handover*.md`, `*-investigation*.md`) |

**Before proposing changes to code**, read the file first. **Before
recommending a command or flag**, verify it exists in `clm info commands` —
do not fabricate options.

<!-- DOCS-LAYOUT:BEGIN (managed by /docs-layout) -->
## Document Placement

Companion to the **Documentation Map** above: that says where to *find*
canonical info; this says where to *put* a new document. This is CLM's target
layout — some existing docs predate it and are being migrated toward it, so
prefer these homes for anything new.

| Kind of document | Home | Naming |
|---|---|---|
| Agent session guide | `AGENTS.md` (root); `CLAUDE.md` only does `@AGENTS.md` | fixed |
| User-facing docs | `docs/user-guide/` | kebab-case |
| Developer / contributor docs | `docs/developer-guide/` | kebab-case |
| Agent design docs / decisions | `docs/claude/design/` | kebab-case `<topic>.md` |
| Handover docs | `docs/claude/handovers/` | `<feature>-handover.md` (+ `<feature>-handover-archive.md`) |
| Investigations / analyses / cross-session notes | `docs/claude/` (deeper: `docs/claude/analysis/`, `docs/claude/requirements/`) | `<topic>-investigation.md` |
| Proposals (pre-design exploration) | `docs/proposals/` | kebab-case |
| Retired / historical docs | `docs/archive/` | under a topic subfolder |
| Changelog | `CHANGELOG.md`; unreleased entries as fragments in `changelog.d/` | `<pr-or-issue>-<slug>.<type>.md` |
| Version-accurate info topics | `src/clm/cli/info_topics/` (code-adjacent — do not move) | `<topic>.md` |
| Top-level meta | repo root | `README.md`, `LICENSE`, `CONTRIBUTING.md` |

CLM uses `docs/claude/` (not the agent-neutral `docs/agent/` other repos use)
because the path is referenced from source code and the version-accurate
`info_topics`; renaming it is a separate, deliberate migration.
<!-- DOCS-LAYOUT:END -->

## Working in this Repo (Windows-first)

CLM is developed primarily on Windows (Git Bash + `cmd.exe` available). A few
practical consequences:

- Use forward-slash or relative paths in shell commands; watch out for MSYS
  path conversion when invoking Docker from Git Bash (a leading `/db` becomes
  `C:/Program Files/Git/db`). Container paths in test/exec code use the
  `//db/...` double-slash prefix on Windows to defeat this.
- `.venv/Scripts/python.exe` is the project interpreter on Windows
  (`.venv/bin/python` on Unix).

## Troubleshooting Quick Hits

1. **Tests failing**: check external tools (PlantUML JAR, Draw.io executable)
   and enable logging with `CLM_ENABLE_TEST_LOGGING=1`.
2. **Worker issues**: run `python diagnose_workers.py`.
3. **Import errors**: ensure `pip install -e .` was run in the correct
   environment.

---

**Repository**: https://github.com/hoelzl/clm/ | **Issues**: https://github.com/hoelzl/clm/issues

**Last Updated**: 2026-06-23
