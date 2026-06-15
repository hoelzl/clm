"""Context command — an agent-audience course view scoped to a cut point.

``clm export context`` answers the question *"what has the course already
taught up to here?"* for an LLM that is authoring or revising material. An
assistant writing section 11 can pull the context for sections 1-10 so it can
reference prior workshops and avoid re-teaching concepts the participants
already know.

It differs from ``export outline`` (titles only) and ``export summary``
(client/trainer prose for humans) in three ways:

* **Scope** — a prefix or window of the course, by section
  (``--through``/``--from``) or by topic anchor (``--before``/``--upto``).
* **Audience** — the ``agent`` audience: dense, factual notes about the
  concepts, terms and APIs introduced, written for an LLM consumer.
* **Depth** — ``--level titles`` (deterministic structure, no LLM),
  ``summary`` (per-topic LLM summaries, cached) or ``full`` (raw extracted
  markdown+code, deterministic).

The LLM-backed ``summary`` level reuses ``export summary``'s machinery
(``summarize_notebook`` + :class:`SummaryCache`), so summaries are cached
across runs and shared with the ``agent`` audience partition.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click
from attrs import define, field
from rich.console import Console

from clm.cli.commands._export_shared import (
    check_exclusive_output,
    disabled_topic_files,
    language_option,
    notebook_in_language,
    output_options,
    resolve_disabled_mode,
    section_visible,
    selection_options,
    spec_argument,
)
from clm.cli.commands.export.summary import (
    SummarizeProgress,
    content_hash,
    extract_notebook_content,
    notebook_contains_workshop,
)
from clm.core.course import Course
from clm.core.course_files.notebook_file import NotebookFile
from clm.core.course_paths import resolve_course_paths
from clm.core.course_spec import CourseSpec, CourseSpecError
from clm.core.utils.notebook_utils import find_notebook_titles
from clm.core.utils.text_utils import Text, sanitize_file_name

if TYPE_CHECKING:
    from clm.core.course_spec import SectionSpec
    from clm.infrastructure.llm.cache import SummaryCache

logger = logging.getLogger(__name__)

# The audience used for every context document. Unlike client/trainer this is
# tuned for an LLM consumer; see ``infrastructure/llm/prompts.py``.
AGENT_AUDIENCE = "agent"


# ---------------------------------------------------------------------------
# Course → units
# ---------------------------------------------------------------------------
@define
class _Notebook:
    """A notebook entry within a topic: its display title and source path."""

    file_name: str
    title: str
    path: Path


@define
class _TopicUnit:
    """One topic and the notebooks (decks) under it, filtered to a language."""

    topic_id: str
    notebooks: list[_Notebook] = field(factory=list)


@define
class _SectionUnit:
    """One section of the emitted context, with its topics.

    ``number`` is the 1-based position in the *unscoped* emitted order, so it
    stays stable under scoping — ``--from 5 --through 10`` yields sections still
    numbered 5..10, matching how an author counts them.
    """

    number: int
    name: str
    disabled: bool
    section_id: str | None = None
    topics: list[_TopicUnit] = field(factory=list)


def _notebook_entry(title: Text, path: Path, language: str) -> _Notebook:
    return _Notebook(file_name=path.name, title=title[language] or path.stem, path=path)


def _enabled_topic_units(section, language: str) -> list[_TopicUnit]:
    """Topic units for an enabled (built) section, filtered to *language*.

    Split ``.de``/``.en`` companions are filtered by ``notebook_in_language`` so
    a split pair contributes one entry, matching the build's per-language
    routing.
    """
    units: list[_TopicUnit] = []
    for topic in section.topics:
        notebooks = [
            _notebook_entry(nb.title, nb.path, language)
            for nb in topic.notebooks
            if isinstance(nb, NotebookFile) and notebook_in_language(nb, language)
        ]
        units.append(_TopicUnit(topic_id=topic.id, notebooks=notebooks))
    return units


def _disabled_topic_units(
    course: Course, section_spec: SectionSpec, language: str
) -> list[_TopicUnit]:
    """Topic units for a disabled section, resolved from the filesystem.

    Disabled sections are not part of the built course; their slide files are
    read straight from disk (mirroring ``export summary``/``outline``). A topic
    that resolves to no slide files still appears, as an empty unit, so a
    planned-but-absent topic stays visible in the structure.
    """
    units: list[_TopicUnit] = []
    for topic_spec in section_spec.topics:
        notebooks: list[_Notebook] = []
        for path in disabled_topic_files(course, topic_spec, language) or []:
            try:
                title = find_notebook_titles(path.read_text(encoding="utf-8"), default=path.stem)
            except (OSError, ValueError):
                title = Text(de=path.stem, en=path.stem)
            notebooks.append(_notebook_entry(title, path, language))
        units.append(_TopicUnit(topic_id=topic_spec.id, notebooks=notebooks))
    return units


def build_section_units(
    course: Course,
    language: str,
    *,
    include_optional: bool,
    include_disabled: bool,
    full_sections: list[SectionSpec] | None,
) -> list[_SectionUnit]:
    """Build the ordered, numbered section units (before scoping).

    Enabled sections come from the built course; disabled whole sections (when
    ``include_disabled`` and *full_sections* are supplied) are read from disk
    and appended after the enabled ones with ``disabled=True``. Numbering runs
    1..N across the whole emitted sequence.

    ``merge`` mode (folding disabled sections into declared order) is
    intentionally not offered for context: the document is a linear "what came
    before" reference, so disabled content is either excluded or clearly tagged
    rather than silently interleaved.
    """
    units: list[_SectionUnit] = []
    for section, section_spec in zip(course.sections, course.spec.sections, strict=True):
        if not section_visible(section_spec, include_optional=include_optional):
            continue
        units.append(
            _SectionUnit(
                number=len(units) + 1,
                name=section.name[language],
                disabled=False,
                section_id=section.id,
                topics=_enabled_topic_units(section, language),
            )
        )

    if include_disabled and full_sections is not None:
        for section_spec in full_sections:
            if section_spec.enabled:
                continue
            if section_spec.optional and not include_optional:
                continue
            units.append(
                _SectionUnit(
                    number=len(units) + 1,
                    name=section_spec.name[language],
                    disabled=True,
                    section_id=section_spec.id,
                    topics=_disabled_topic_units(course, section_spec, language),
                )
            )
    return units


# ---------------------------------------------------------------------------
# Scope resolution
# ---------------------------------------------------------------------------
class ScopeError(ValueError):
    """A scope selector could not be resolved against the course."""


def _resolve_section_position(token: str, units: list[_SectionUnit]) -> int:
    """Resolve a ``--through``/``--from`` token to a section ``number``.

    The token is a 1-based section number or a section id. Raises
    :class:`ScopeError` when it matches nothing.
    """
    if token.isdigit():
        pos = int(token)
        if not units or pos < units[0].number or pos > units[-1].number:
            raise ScopeError(
                f"section number {pos} is out of range (course has {len(units)} visible section(s))"
            )
        return pos
    for unit in units:
        if unit.section_id == token:
            return unit.number
    raise ScopeError(f"no section with id {token!r} in this course view")


def _topic_order(units: list[_SectionUnit]) -> list[tuple[int, str]]:
    """Flatten units to an ordered ``(section_index, topic_id)`` list."""
    return [
        (sec_idx, topic.topic_id) for sec_idx, unit in enumerate(units) for topic in unit.topics
    ]


def apply_scope(
    units: list[_SectionUnit],
    *,
    through: str | None = None,
    from_section: str | None = None,
    before: str | None = None,
    upto: str | None = None,
) -> list[_SectionUnit]:
    """Trim *units* to the requested scope, preserving section numbers.

    Section selectors (``through``/``from``) keep whole sections by position;
    topic selectors (``before``/``upto``) cut at a topic anchor, keeping earlier
    sections whole and truncating the anchor's section. The two families are
    mutually exclusive — the caller validates that — so at most one family is
    active here. Raises :class:`ScopeError` for an unresolvable selector.
    """
    if before is not None or upto is not None:
        return _apply_topic_scope(units, before=before, upto=upto)
    if through is not None or from_section is not None:
        return _apply_section_scope(units, through=through, from_section=from_section)
    return list(units)


def _apply_section_scope(
    units: list[_SectionUnit], *, through: str | None, from_section: str | None
) -> list[_SectionUnit]:
    low = _resolve_section_position(from_section, units) if from_section is not None else None
    high = _resolve_section_position(through, units) if through is not None else None
    if low is not None and high is not None and low > high:
        raise ScopeError(f"--from {from_section} comes after --through {through}")
    return [
        unit
        for unit in units
        if (low is None or unit.number >= low) and (high is None or unit.number <= high)
    ]


def _locate_topic(units: list[_SectionUnit], topic_id: str) -> tuple[int, int]:
    """Return ``(section_index, topic_index)`` of *topic_id*, or raise."""
    for sec_idx, unit in enumerate(units):
        for topic_idx, topic in enumerate(unit.topics):
            if topic.topic_id == topic_id:
                return sec_idx, topic_idx
    raise ScopeError(f"topic {topic_id!r} is not in this course view")


def _apply_topic_scope(
    units: list[_SectionUnit], *, before: str | None, upto: str | None
) -> list[_SectionUnit]:
    if before is not None and upto is not None:
        raise ScopeError("--before and --upto are mutually exclusive")
    anchor = before if before is not None else upto
    assert anchor is not None
    sec_idx, topic_idx = _locate_topic(units, anchor)
    # `inclusive` topic count kept in the anchor's section.
    keep = topic_idx if before is not None else topic_idx + 1

    result: list[_SectionUnit] = []
    for idx, unit in enumerate(units):
        if idx < sec_idx:
            result.append(unit)
        elif idx == sec_idx:
            trimmed = _SectionUnit(
                number=unit.number,
                name=unit.name,
                disabled=unit.disabled,
                section_id=unit.section_id,
                topics=unit.topics[:keep],
            )
            if trimmed.topics:
                result.append(trimmed)
        # idx > sec_idx: dropped (authored after the anchor)
    return result


# ---------------------------------------------------------------------------
# Rendering — titles / full (deterministic, no LLM)
# ---------------------------------------------------------------------------
def _section_heading(unit: _SectionUnit) -> str:
    marker = " (disabled)" if unit.disabled else ""
    return f"## {unit.number}. {unit.name}{marker}"


def render_titles_markdown(course: Course, units: list[_SectionUnit], language: str) -> str:
    lines = [f"# {course.name[language]}", ""]
    for unit in units:
        lines.append(_section_heading(unit))
        lines.append("")
        for topic in unit.topics:
            for nb in topic.notebooks:
                lines.append(f"- {nb.title}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_full_markdown(course: Course, units: list[_SectionUnit], language: str) -> str:
    lines = [f"# {course.name[language]}", ""]
    for unit in units:
        lines.append(_section_heading(unit))
        lines.append("")
        for topic in unit.topics:
            for nb in topic.notebooks:
                lines.append(f"### {nb.title}")
                lines.append("")
                content = extract_notebook_content(nb.path, AGENT_AUDIENCE, language)
                lines.append(content.strip() if content else "_(no extractable content)_")
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Rendering — summary (LLM, cached)
# ---------------------------------------------------------------------------
async def _summaries_by_hash(
    units: list[_SectionUnit],
    course: Course,
    language: str,
    *,
    style: str,
    model: str,
    temperature: float,
    api_base: str | None,
    api_key: str | None,
    max_concurrent: int,
    cache: SummaryCache | None,
    no_cache: bool,
    progress: SummarizeProgress | None,
) -> dict[str, str]:
    """Compute (cache-or-LLM) agent summaries, keyed by content hash.

    One entry per distinct extracted content; the renderer looks summaries up
    by hash so identical decks are summarized once.
    """
    from clm.infrastructure.llm.client import LLMError, summarize_notebook

    course_name = course.name[language]
    summaries: dict[str, str] = {}
    pending: list[tuple[str, str, str, bool, str]] = []  # (hash, title, content, has_ws, section)

    for unit in units:
        for topic in unit.topics:
            for nb in topic.notebooks:
                content = extract_notebook_content(nb.path, AGENT_AUDIENCE, language)
                if not content:
                    if progress:
                        progress.on_cached(nb.title)
                    continue
                h = content_hash(content)
                if h in summaries:
                    if progress:
                        progress.on_cached(nb.title)
                    continue
                cached = (
                    cache.get(h, AGENT_AUDIENCE, model, language, style)
                    if cache and not no_cache
                    else None
                )
                if cached is not None:
                    summaries[h] = cached
                    if progress:
                        progress.on_cached(nb.title)
                    continue
                has_ws = notebook_contains_workshop(nb.path)
                pending.append((h, nb.title, content, has_ws, unit.name))

    if not pending:
        return summaries

    coros = [
        summarize_notebook(
            content=content,
            audience=AGENT_AUDIENCE,
            model=model,
            notebook_title=title,
            section_name=section_name,
            course_name=course_name,
            temperature=temperature,
            api_base=api_base,
            api_key=api_key,
            max_concurrent=max_concurrent,
            has_workshop=has_ws,
            language=language,
            style=style,
        )
        for (_h, title, content, has_ws, section_name) in pending
    ]
    results = await asyncio.gather(*coros, return_exceptions=True)

    for (h, title, _content, _has_ws, _section), result in zip(pending, results, strict=True):
        if isinstance(result, BaseException):
            msg = str(result) if isinstance(result, LLMError) else repr(result)
            summaries[h] = f"_Error: {msg}_"
            if progress:
                progress.on_error(title, msg)
            else:
                click.echo(f"  Warning: {msg}", err=True)
        else:
            summary = str(result)
            summaries[h] = summary
            if cache and not summary.startswith("_Error"):
                cache.put(h, AGENT_AUDIENCE, model, summary, language, style)
            if progress:
                progress.on_generated(title)
    return summaries


def render_summary_markdown(
    course: Course, units: list[_SectionUnit], language: str, summaries: dict[str, str]
) -> str:
    lines = [f"# {course.name[language]}", ""]
    for unit in units:
        lines.append(_section_heading(unit))
        lines.append("")
        for topic in unit.topics:
            for nb in topic.notebooks:
                content = extract_notebook_content(nb.path, AGENT_AUDIENCE, language)
                summary = summaries.get(content_hash(content)) if content else None
                lines.append(f"### {nb.title}")
                lines.append(summary or "_(no extractable content)_")
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Rendering — JSON (all levels)
# ---------------------------------------------------------------------------
def render_json(
    course: Course,
    units: list[_SectionUnit],
    language: str,
    *,
    level: str,
    scope: dict[str, str | None],
    summaries: dict[str, str] | None = None,
) -> dict:
    """Structured view of the scoped context for machine consumers."""
    sections: list[dict] = []
    for unit in units:
        topics_out: list[dict] = []
        for topic in unit.topics:
            slides: list[dict] = []
            for nb in topic.notebooks:
                entry: dict = {"file": nb.file_name, "title": nb.title}
                if level in ("summary", "full"):
                    content = extract_notebook_content(nb.path, AGENT_AUDIENCE, language)
                    if level == "summary":
                        h = content_hash(content) if content else None
                        entry["summary"] = (summaries or {}).get(h) if h else None
                    else:  # full
                        entry["content"] = content or None
                slides.append(entry)
            topics_out.append({"topic_id": topic.topic_id, "slides": slides})
        section_entry: dict = {
            "number": unit.number,
            "name": unit.name,
            "disabled": unit.disabled,
            "topics": topics_out,
        }
        if unit.section_id is not None:
            section_entry["id"] = unit.section_id
        sections.append(section_entry)
    return {
        "course_name": course.name[language],
        "language": language,
        "level": level,
        "scope": {k: v for k, v in scope.items() if v is not None},
        "sections": sections,
    }


# ---------------------------------------------------------------------------
# Shared loading + scoping (used by the CLI and the MCP tool)
# ---------------------------------------------------------------------------
def load_scoped_units(
    course: Course,
    spec_file: Path,
    language: str,
    *,
    include_optional: bool,
    include_disabled: bool,
    through: str | None,
    from_section: str | None,
    before: str | None,
    upto: str | None,
) -> list[_SectionUnit]:
    """Build the section units and apply the scope selectors.

    Reparses the spec with ``keep_disabled`` only when disabled content is
    requested. Raises :class:`ScopeError` for an unresolvable selector.
    """
    full_sections: list[SectionSpec] | None = None
    if include_disabled:
        full_spec = CourseSpec.from_file(spec_file, keep_disabled=True)
        full_sections = full_spec.sections

    units = build_section_units(
        course,
        language,
        include_optional=include_optional,
        include_disabled=include_disabled,
        full_sections=full_sections,
    )
    return apply_scope(units, through=through, from_section=from_section, before=before, upto=upto)


def validate_scope_options(
    through: str | None,
    from_section: str | None,
    before: str | None,
    upto: str | None,
) -> None:
    """Reject incompatible scope selector combinations (raises UsageError)."""
    section_sel = through is not None or from_section is not None
    topic_sel = before is not None or upto is not None
    if section_sel and topic_sel:
        raise click.UsageError(
            "section selectors (--through/--from) and topic selectors "
            "(--before/--upto) are mutually exclusive."
        )
    if before is not None and upto is not None:
        raise click.UsageError("--before and --upto are mutually exclusive.")
    if from_section is not None and through is None:
        # A bare --from window runs to the end of the course; allowed, but make
        # the (common) typo of forgetting --through harmless and explicit.
        pass


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------
@click.command()
@spec_argument
@click.option(
    "--level",
    type=click.Choice(["titles", "summary", "full"], case_sensitive=False),
    default="summary",
    show_default=True,
    help="Depth: titles (structure only, no LLM), summary (per-topic LLM "
    "summaries, cached), or full (raw extracted markdown+code, no LLM).",
)
@click.option(
    "--through",
    default=None,
    metavar="SECTION",
    help="Include sections up to and including SECTION (1-based number or "
    "section id). Mutually exclusive with --before/--upto.",
)
@click.option(
    "--from",
    "from_section",
    default=None,
    metavar="SECTION",
    help="Start the window at SECTION (1-based number or section id); pairs "
    "with --through. Without --through the window runs to the end.",
)
@click.option(
    "--before",
    default=None,
    metavar="TOPIC_ID",
    help="Include everything authored strictly before TOPIC_ID. Mutually "
    "exclusive with --through/--from.",
)
@click.option(
    "--upto",
    default=None,
    metavar="TOPIC_ID",
    help="Include everything up to and including TOPIC_ID. Mutually exclusive "
    "with --through/--from.",
)
@language_option(default="en", help="Language for the context document.")
@click.option(
    "-f",
    "--format",
    "output_format",
    type=click.Choice(["markdown", "json"], case_sensitive=False),
    default="markdown",
    show_default=True,
    help="Output format.",
)
@output_options
@selection_options
@click.option(
    "--style",
    type=click.Choice(["prose", "bullets"], case_sensitive=False),
    default="bullets",
    show_default=True,
    help="Summary style (--level summary only): dense bullets or prose.",
)
@click.option("--model", default=None, help="Override LLM model identifier (--level summary).")
@click.option("--api-base", default=None, help="Custom API base URL (--level summary).")
@click.option(
    "--no-cache", is_flag=True, default=False, help="Skip the summary cache (--level summary)."
)
@click.option("--no-progress", is_flag=True, default=False, help="Disable the progress bar.")
def context(
    spec_file: Path,
    level: str,
    through: str | None,
    from_section: str | None,
    before: str | None,
    upto: str | None,
    language: str,
    output_format: str,
    output_file: Path | None,
    output_dir: Path | None,
    include_optional: bool,
    disabled_mode: str | None,
    style: str,
    model: str | None,
    api_base: str | None,
    no_cache: bool,
    no_progress: bool,
):
    """Export an agent-audience course view scoped to a cut point.

    Produces a "what has been taught up to here" reference for an LLM working
    on the course — so an assistant authoring later material can reference
    prior topics and avoid re-teaching them.

    \b
    Examples:
        clm export context course.xml --through 10                 # sections 1-10, summaries
        clm export context course.xml --through 10 --level titles  # cheap structure
        clm export context course.xml --from 5 --through 10        # a window
        clm export context course.xml --before rag_intro           # all prior topics
        clm export context course.xml --upto rag_intro --level full
        clm export context course.xml --through 10 -f json -o ctx.json
    """
    check_exclusive_output(output_file, output_dir)
    validate_scope_options(through, from_section, before, upto)

    include_disabled, merge_disabled = resolve_disabled_mode(disabled_mode)
    if merge_disabled:
        raise click.UsageError(
            "--include-disabled=merge is not supported by 'export context'; "
            "use bare --include-disabled to tag disabled sections."
        )

    is_summary = level == "summary"
    is_json = output_format == "json"

    if is_summary:
        try:
            import openai  # noqa: F401
        except ImportError:
            raise click.ClickException(
                "openai is required for --level summary. "
                'Install with: pip install "coding-academy-lecture-manager[summarize]"'
            ) from None

    # Load + validate the spec.
    try:
        spec = CourseSpec.from_file(spec_file)
    except CourseSpecError as e:
        raise click.ClickException(f"Failed to parse spec file: {e}") from None

    validation_errors = spec.validate()
    if validation_errors:
        error_msg = "\n".join(f"  - {e}" for e in validation_errors)
        raise click.ClickException(f"Spec validation failed:\n{error_msg}")

    data_dir, _ = resolve_course_paths(spec_file)
    course = Course.from_spec(spec, data_dir, output_root=None)

    try:
        units = load_scoped_units(
            course,
            spec_file,
            language,
            include_optional=include_optional,
            include_disabled=include_disabled,
            through=through,
            from_section=from_section,
            before=before,
            upto=upto,
        )
    except (ScopeError, CourseSpecError) as e:
        raise click.ClickException(str(e)) from None

    scope = {"through": through, "from": from_section, "before": before, "upto": upto}

    # Progress + cache for the LLM-backed summary level only.
    console = Console(file=sys.stderr, force_terminal=not no_progress)
    progress: SummarizeProgress | None = None
    cache: SummaryCache | None = None
    summaries: dict[str, str] | None = None

    if is_summary:
        from clm.infrastructure.config import get_config

        llm_config = get_config().llm
        effective_model = model or llm_config.model
        effective_api_base = api_base or llm_config.api_base or None
        effective_api_key = llm_config.api_key or None

        total = sum(len(t.notebooks) for u in units for t in u.topics)
        if not no_progress:
            progress = SummarizeProgress(console, show_progress=True)
            progress.start(course.name[language], total, effective_model, AGENT_AUDIENCE)

        if not no_cache:
            from clm.infrastructure.llm.cache import SummaryCache

            cache = SummaryCache(data_dir / "clm_summaries.db")

        try:
            summaries = asyncio.run(
                _summaries_by_hash(
                    units,
                    course,
                    language,
                    style=style,
                    model=effective_model,
                    temperature=llm_config.temperature,
                    api_base=effective_api_base,
                    api_key=effective_api_key,
                    max_concurrent=llm_config.max_concurrent,
                    cache=cache,
                    no_cache=no_cache,
                    progress=progress,
                )
            )
        finally:
            if cache:
                cache.close()
            if progress:
                progress.finish()

    # Render.
    if is_json:
        result = json.dumps(
            render_json(course, units, language, level=level, scope=scope, summaries=summaries),
            indent=2,
        )
    elif level == "titles":
        result = render_titles_markdown(course, units, language)
    elif level == "full":
        result = render_full_markdown(course, units, language)
    else:
        result = render_summary_markdown(course, units, language, summaries or {})

    # Output.
    ext = ".json" if is_json else ".md"
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        title = sanitize_file_name(course.name[language])
        file_path = output_dir / f"{title}-context-{level}{ext}"
        file_path.write_text(result, encoding="utf-8")
        console.print(f"[dim]Written: {file_path}[/dim]")
    elif output_file:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(result, encoding="utf-8")
        console.print(f"[dim]Written: {output_file}[/dim]")
    else:
        click.echo(result)
