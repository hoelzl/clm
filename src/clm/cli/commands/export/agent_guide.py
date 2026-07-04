"""``clm export agent-guide`` — a version-stamped agent-orientation cheat-sheet.

A sibling to ``clm export context``. Where ``context`` summarizes *course
content* for an LLM, ``agent-guide`` summarizes *CLM itself*: where the
authoritative docs live (``clm info``), the high-frequency commands, the live
MCP tool surface, the course-repo layout, and the portable key-path facts.

The point is to **stop agent docs from silently lying**. Hand-maintained agent
docs in course repos restate facts a live ``clm`` / ``gh`` command already
answers — versions, command references, the MCP tool list, the active-course
set — and those restatements rot. ``clm info`` already solved that for
documentation (it is regenerated from the installed ``clm``, so it cannot drift
relative to the tool); but tools that don't run ``clm`` (ZCode/GLM, …) need a
*static file* they can read. This command emits exactly that, assembled
**entirely from live sources** — nothing hand-typed.

Two properties make it trustworthy rather than just another file that rots:

* **Version stamp** — the header carries ``Generated from clm <version>`` so a
  reader (and the staleness gate) can tell at a glance whether it predates the
  installed tool.
* **Staleness gate** — ``clm export agent-guide --check`` regenerates the
  deterministic core in memory and exits non-zero when the committed file is
  missing, stamped from a different ``clm`` version, or would otherwise change.
  Wire it into validate / pre-commit / CI and a ``clm`` bump *forces* a regen
  instead of merely permitting one. The file refuses to be stale.

The optional ``--with-issues`` block (open ``agent-impact`` issues, via ``gh``)
is inherently date-stamped and volatile, so it is fenced off and **excluded from
``--check``** — the gate enforces the deterministic core only.
"""

from __future__ import annotations

import datetime
import json
import logging
import re
import subprocess
from pathlib import Path

import click

from clm.__version__ import __version__

logger = logging.getLogger(__name__)

# Default output file written into the course repo (committed, version-stamped).
DEFAULT_OUTPUT_NAME = "AGENTS.generated.md"

# The header line carrying the version stamp. Kept parseable so ``--check`` can
# recover the stamp from an existing file for a precise drift diagnostic.
_STAMP_RE = re.compile(r"^Generated from clm (?P<version>.+?)\.\s*$", re.MULTILINE)

# The (volatile) open-issues block is fenced with this marker so ``--check`` can
# truncate it off before comparing the deterministic core. The block is always
# emitted last, so splitting on the marker recovers the core verbatim.
_ISSUES_MARKER = "<!-- BEGIN agent-guide:issues"

# High-frequency commands surfaced as a curated summary. Only the *selection*
# (which commands) is hand-maintained here — each description is pulled live
# from the command's own short help, so it cannot drift. ``clm info commands``
# stays the authority. Entry = the click command path under ``clm``.
CURATED_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("build",),
    ("validate",),
    ("slides", "sync"),
    ("slides", "normalize"),
    ("slides", "assign-ids"),
    ("slides", "translate"),
    ("course", "decks"),
    ("course", "gate"),
    ("release", "sync"),
    ("export", "context"),
    ("info",),
)


# ---------------------------------------------------------------------------
# Live-source section builders (pure; each returns a markdown fragment)
# ---------------------------------------------------------------------------
def _info_index_section() -> str:
    """The ``clm info`` topic index — the authoritative documentation pointers."""
    from clm.cli.commands.info import TOPICS

    lines = [
        "## Documentation index (`clm info`)",
        "",
        "The authoritative, version-accurate references — regenerated from the "
        "installed clm, so they cannot drift relative to the tool. Run "
        "`clm info <topic>`:",
        "",
    ]
    for slug, ti in TOPICS.items():
        lines.append(f"- `{slug}` — {ti.description}")
    return "\n".join(lines)


def _command_short_help(path: tuple[str, ...]) -> str | None:
    """Live short-help for a ``clm`` subcommand path, or ``None`` if unresolved."""
    from clm.cli.main import cli

    ctx = click.Context(cli)
    cmd: click.Command = cli
    for part in path:
        if not isinstance(cmd, click.Group):
            return None
        sub = cmd.get_command(ctx, part)
        if sub is None:
            return None
        ctx = click.Context(sub, parent=ctx)
        cmd = sub
    return str(cmd.get_short_help_str(limit=200))


def _curated_commands_section() -> str:
    """The curated high-frequency command subset, with live descriptions."""
    lines = [
        "## Key commands (summary)",
        "",
        "The high-frequency, stable subset. These are **summaries**; the "
        "authoritative reference is `clm info commands` (and `clm <cmd> --help`).",
        "",
    ]
    for path in CURATED_COMMANDS:
        name = "clm " + " ".join(path)
        help_str = _command_short_help(path)
        if help_str:
            lines.append(f"- `{name}` — {help_str}")
        else:
            lines.append(f"- `{name}` — _(run `{name} --help`)_")
    return "\n".join(lines)


def _mcp_tools() -> list[tuple[str, str]]:
    """The live MCP tool surface as ``(name, first-line-of-docstring)`` pairs.

    The MCP list is a core part of the guide, so a missing ``[mcp]`` extra is a
    hard, actionable error rather than a silently-omitted section (which would
    also make the output non-deterministic across environments).
    """
    try:
        from clm.mcp.server import create_server
    except ImportError as exc:
        raise click.ClickException(
            "The MCP tool list needs the [mcp] extra. Install with: "
            'pip install "coding-academy-lecture-manager[mcp]" (or [all]). '
            f"({exc})"
        ) from None

    server = create_server(Path("."))
    tools = server._tool_manager.list_tools()
    out: list[tuple[str, str]] = []
    for tool in sorted(tools, key=lambda t: t.name):
        first = ""
        if tool.description:
            stripped = tool.description.strip().splitlines()
            if stripped:
                first = stripped[0].strip()
        out.append((tool.name, first))
    return out


def _mcp_tools_section() -> str:
    """The live MCP tool list (the real surface the hand tables only approximate)."""
    lines = [
        "## MCP tools (live surface)",
        "",
        "The MCP server (`clm mcp`) exposes these tools — this is the complete, "
        "live surface, not a hand-maintained excerpt. Descriptions are the tools' "
        "own docstrings:",
        "",
    ]
    for name, desc in _mcp_tools():
        lines.append(f"- `{name}` — {desc}" if desc else f"- `{name}`")
    return "\n".join(lines)


def _section_label(name: object) -> str:
    """Display label for a section name (bilingual ``Text`` or plain str)."""
    try:
        return str(name["en"] or name["de"])  # type: ignore[index]
    except (TypeError, KeyError):
        return str(name)


def _spec_layout(spec_file: Path, slides_dir: Path, topic_map) -> str:
    """One spec's layout facts: enabled/disabled sections and the deck count."""
    from clm.core.course_spec import CourseSpec, CourseSpecError

    rel = spec_file.name
    try:
        spec = CourseSpec.from_file(spec_file, keep_disabled=True)
    except CourseSpecError as exc:
        return f"### `course-specs/{rel}`\n\n- _failed to parse: {exc}_"

    enabled = [s for s in spec.sections if s.enabled]
    disabled = [s for s in spec.sections if not s.enabled]

    lines = [f"### `course-specs/{rel}` — {_section_label(spec.name)}", ""]
    if enabled:
        names = ", ".join(_section_label(s.name) for s in enabled)
        lines.append(f"- Enabled sections ({len(enabled)}): {names}")
    else:
        lines.append("- Enabled sections (0): _none_")
    if disabled:
        names = ", ".join(_section_label(s.name) for s in disabled)
        lines.append(f"- Disabled sections ({len(disabled)}): {names}")

    # Deck count (the shipping set) — best-effort; needs the slides tree. Mirrors
    # `clm course decks`, which is the authority for the deck *list*.
    try:
        from clm.core.spec_decks import resolve_spec_decks

        resolution = resolve_spec_decks(spec, slides_dir, topic_map=topic_map)
        lines.append(
            f"- Decks (shipping set): {len(resolution.deck_files)} "
            f"(`clm course decks course-specs/{rel}` for the list)"
        )
    except Exception as exc:  # noqa: BLE001 — layout enrichment, never fatal
        logger.debug("deck resolution failed for %s: %s", spec_file, exc)
    return "\n".join(lines)


def _repo_layout_section(repo_dir: Path) -> str:
    """Course-repo layout, replacing the hand-maintained 'active courses' list."""
    specs_dir = repo_dir / "course-specs"
    lines = ["## Repo layout", ""]
    if not specs_dir.is_dir():
        lines.append(
            f"_No `course-specs/` directory found under `{repo_dir}` — "
            "run this command from a course repo root to list its specs._"
        )
        return "\n".join(lines)

    spec_files = sorted(specs_dir.glob("*.xml"))
    if not spec_files:
        lines.append("_No `*.xml` course specs found under `course-specs/`._")
        return "\n".join(lines)

    lines.append(
        "Course specs discovered under `course-specs/` (the authority for the "
        "active-course set — do not hand-maintain it elsewhere):"
    )
    lines.append("")

    # Build the topic map once and share it across specs (they share a slides tree).
    from clm.core.course_paths import resolve_course_paths

    course_root, _ = resolve_course_paths(spec_files[0])
    slides_dir = course_root / "slides"
    topic_map = None
    if slides_dir.is_dir():
        try:
            from clm.core.topic_resolver import build_topic_map

            topic_map = build_topic_map(slides_dir)
        except Exception as exc:  # noqa: BLE001 — enrichment only
            logger.debug("topic map build failed for %s: %s", slides_dir, exc)

    blocks = [_spec_layout(sf, slides_dir, topic_map) for sf in spec_files]
    lines.append("\n\n".join(blocks))
    return "\n".join(lines)


def _key_paths_section() -> str:
    """Portable key-path facts. Resolved (machine-specific) paths stay behind clm.

    The committed guide is read on *other* machines, so embedding this host's
    absolute cache/config paths would be wrong everywhere else and would make
    ``--check`` fail across machines. We emit the portable *rules* and defer the
    resolved values to ``clm config locate``.
    """
    from clm.infrastructure.llm.cache import CACHE_DB_NAME

    return "\n".join(
        [
            "## Key paths",
            "",
            "Resolved, machine-specific paths are intentionally *not* embedded "
            "(this file is committed and read elsewhere) — run `clm config locate` "
            "for the values on a given machine. The portable rules:",
            "",
            "- **LLM cache** (summaries, translations): defaults "
            f"to `<repo>/.clm-cache/{CACHE_DB_NAME}`. Override with `--cache-dir` "
            "or `$CLM_CACHE_DIR`.",
            "- **Git worktrees**: a *relative* `cache_dir` is anchored to the "
            "**main** worktree root, so linked worktrees share one cache.",
            "- **Config files** (highest to lowest priority): environment "
            "variables → project `.clm/config.toml` (or `clm.toml`) → user config "
            "(`~/.config/clm/config.toml`) → system config.",
        ]
    )


def _issues_block(repo: str, label: str, date_str: str) -> str:
    """The fenced, date-stamped open-issues block (volatile; excluded from --check).

    Raises :class:`click.ClickException` if ``gh`` is unavailable or fails — the
    caller asked for issues explicitly, so silently omitting them would be worse.
    """
    cmd = [
        "gh",
        "issue",
        "list",
        "-R",
        repo,
        "--label",
        label,
        "--state",
        "open",
        "--limit",
        "200",
        "--json",
        "number,title",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except FileNotFoundError:
        raise click.ClickException(
            "--with-issues needs the GitHub CLI (`gh`) on PATH. Install it or omit --with-issues."
        ) from None
    except subprocess.CalledProcessError as exc:
        raise click.ClickException(
            f"`gh issue list` failed (exit {exc.returncode}): {exc.stderr.strip()}"
        ) from None

    try:
        issues = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"could not parse `gh issue list` output: {exc}") from None

    refresh = f"clm export agent-guide --with-issues --issues-repo {repo} --issues-label {label}"
    lines = [
        f"{_ISSUES_MARKER} (volatile — refreshed only on regen; excluded from --check) -->",
        f"## Open `{label}` issues",
        "",
        f"As of {date_str} — fresh as of the last regen, **not** live. "
        f"Refresh: `{refresh}` (or `gh issue list -R {repo} --label {label} --state open`).",
        "",
    ]
    if issues:
        for issue in sorted(issues, key=lambda i: i.get("number", 0)):
            lines.append(f"- #{issue.get('number')} — {issue.get('title', '').strip()}")
    else:
        lines.append(f"_No open `{label}` issues._")
    lines.append("")
    lines.append("<!-- END agent-guide:issues -->")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------
def _normalize(text: str) -> str:
    """Normalize trailing whitespace for a stable comparison / write."""
    return text.rstrip() + "\n"


def build_core(repo_dir: Path, version: str) -> str:
    """Assemble the deterministic core of the guide (everything but issues)."""
    header = "\n".join(
        [
            "<!-- GENERATED BY `clm export agent-guide` — DO NOT EDIT BY HAND. -->",
            "<!-- Regenerate after a clm upgrade: `clm export agent-guide`. -->",
            "<!-- Staleness gate (CI/pre-commit): `clm export agent-guide --check`. -->",
            "",
            "# CLM Agent Guide",
            "",
            f"Generated from clm {version}.",
            "",
            "Assembled entirely from live `clm` sources — nothing here is "
            "hand-typed. Treat every fact as a *summary* whose authority is the "
            "linked command; when in doubt, run it. Corollary hard rule for the "
            "**hand-maintained** agent docs that sit beside this file: version "
            "numbers, issue numbers, and dates live only here (stamped) or behind "
            "`clm` / `gh` — never restated in durable prose.",
        ]
    )
    sections = [
        header,
        _info_index_section(),
        _curated_commands_section(),
        _mcp_tools_section(),
        _repo_layout_section(repo_dir),
        _key_paths_section(),
    ]
    return _normalize("\n\n".join(sections))


def build_guide(
    repo_dir: Path,
    version: str,
    *,
    issues_block: str | None = None,
) -> str:
    """The full guide: deterministic core plus an optional trailing issues block."""
    core = build_core(repo_dir, version)
    if issues_block is None:
        return core
    return core + "\n" + _normalize(issues_block)


def extract_core(text: str) -> str:
    """Recover the deterministic core from a generated file (drop the issues block)."""
    head = text.split(_ISSUES_MARKER, 1)[0]
    return _normalize(head)


def extract_stamp(text: str) -> str | None:
    """Recover the ``Generated from clm <version>`` stamp from a generated file."""
    match = _STAMP_RE.search(text)
    return match.group("version") if match else None


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------
@click.command("agent-guide")
@click.argument(
    "repo_dir",
    required=False,
    default=".",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "-o",
    "--output",
    "output_file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=f"Output file. Default: <REPO_DIR>/{DEFAULT_OUTPUT_NAME}.",
)
@click.option(
    "--stdout",
    "to_stdout",
    is_flag=True,
    default=False,
    help="Print to stdout instead of writing the file (ignored with --check).",
)
@click.option(
    "--check",
    is_flag=True,
    default=False,
    help="Don't write: regenerate the deterministic core and exit non-zero if "
    "the existing file is missing, stamped from a different clm version, or "
    "would otherwise change. The volatile issues block is excluded.",
)
@click.option(
    "--with-issues",
    is_flag=True,
    default=False,
    help="Embed open agent-impact issues (via `gh`) as a fenced, date-stamped "
    "block. Excluded from --check.",
)
@click.option(
    "--issues-repo",
    default="hoelzl/clm",
    show_default=True,
    metavar="OWNER/REPO",
    help="Repository for --with-issues.",
)
@click.option(
    "--issues-label",
    default="agent-impact",
    show_default=True,
    help="Issue label for --with-issues.",
)
def agent_guide(
    repo_dir: Path,
    output_file: Path | None,
    to_stdout: bool,
    check: bool,
    with_issues: bool,
    issues_repo: str,
    issues_label: str,
) -> None:
    """Emit a version-stamped, self-staleness-checking agent cheat-sheet.

    A committed Markdown file assembled entirely from live sources — the
    `clm info` index, a curated command summary, the live MCP tool surface, the
    course-repo layout, and portable key-path facts — so tools that don't run
    `clm` (and agents in general) get a fresh-as-of-last-regen orientation
    instead of hand-maintained prose that rots.

    \b
    Examples:
        clm export agent-guide                      # write <repo>/AGENTS.generated.md
        clm export agent-guide --stdout             # preview without writing
        clm export agent-guide --with-issues        # also embed open agent-impact issues
        clm export agent-guide --check              # staleness gate (CI / pre-commit)
    """
    target = output_file if output_file is not None else repo_dir / DEFAULT_OUTPUT_NAME

    if check:
        _run_check(repo_dir, target)
        return

    issues = None
    if with_issues:
        today = datetime.date.today().isoformat()
        issues = _issues_block(issues_repo, issues_label, today)

    result = build_guide(repo_dir, __version__, issues_block=issues)

    if to_stdout:
        click.echo(result)
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(result, encoding="utf-8", newline="\n")
    click.echo(f"Wrote {target} (clm {__version__})", err=True)


def _run_check(repo_dir: Path, target: Path) -> None:
    """The staleness gate: compare the existing file's core to a fresh regen."""
    regen = f"clm export agent-guide{'' if target.name == DEFAULT_OUTPUT_NAME else f' -o {target}'}"

    if not target.exists():
        click.echo(
            f"agent-guide STALE: {target} is missing. Generate it with: {regen}",
            err=True,
        )
        raise SystemExit(1)

    existing = target.read_text(encoding="utf-8")
    fresh_core = build_core(repo_dir, __version__)
    existing_core = extract_core(existing)

    if existing_core == fresh_core:
        click.echo(f"agent-guide OK: {target} is up to date (clm {__version__}).", err=True)
        return

    stamp = extract_stamp(existing)
    if stamp is not None and stamp != __version__:
        click.echo(
            f"agent-guide STALE: {target} is stamped from clm {stamp}, "
            f"but clm {__version__} is installed. Regenerate with: {regen}",
            err=True,
        )
    else:
        click.echo(
            f"agent-guide STALE: {target} would change on regeneration "
            f"(content drift at clm {__version__}). Regenerate with: {regen}",
            err=True,
        )
    raise SystemExit(1)
