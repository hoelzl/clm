"""Outline command for generating course outlines in Markdown and JSON format.

This module provides a command to export a course structure as a Markdown
outline or structured JSON, with section names as headings and topic titles
as entries.
"""

import json
from pathlib import Path

import click

from clm.cli.commands.schedule import subsection_label
from clm.core.course import Course
from clm.core.course_files.notebook_file import NotebookFile
from clm.core.course_paths import resolve_course_paths
from clm.core.course_spec import (
    CourseSpec,
    CourseSpecError,
    SectionSpec,
    SubsectionSpec,
    TopicSpec,
)
from clm.core.section import Section
from clm.core.utils.notebook_utils import find_notebook_titles
from clm.core.utils.text_utils import sanitize_file_name
from clm.infrastructure.utils.path_utils import is_slides_file


def _disabled_topic_slides(
    course: Course, topic_spec: TopicSpec, language: str
) -> list[tuple[str, str]] | None:
    """Return ``(file_name, title)`` pairs for slide files in a disabled topic.

    Resolves ``topic_spec.id`` against the course's filesystem-wide topic map
    and reads the H1 header from each slide file the same way
    :class:`NotebookFile` does. Returns ``None`` when the topic id cannot be
    resolved (so callers can fall back to the legacy ``<topic_id>`` display).
    Returns an empty list when the topic resolves but contains no slide files.
    """
    topic_path = course._topic_path_map.get(topic_spec.id)
    if topic_path is None:
        return None

    slide_paths: list[Path] = []
    if topic_path.is_file():
        if is_slides_file(topic_path):
            slide_paths.append(topic_path)
    elif topic_path.is_dir():
        for child in sorted(topic_path.iterdir()):
            if child.is_file() and is_slides_file(child):
                slide_paths.append(child)

    results: list[tuple[str, str]] = []
    for path in slide_paths:
        try:
            text = path.read_text(encoding="utf-8")
            title = find_notebook_titles(text, default=path.stem)
            results.append((path.name, title[language]))
        except (OSError, ValueError):
            results.append((path.name, path.stem))
    return results


def _topic_deck_titles(topic, language: str) -> list[str]:
    """Return the deck titles of one resolved topic for *language*.

    Split ``.de.py`` / ``.en.py`` companions are filtered to the requested
    language so a split pair contributes one title, matching the build's
    per-language routing.
    """
    titles: list[str] = []
    for notebook in topic.notebooks:
        if (
            notebook.output_language_filter is not None
            and notebook.output_language_filter != language
        ):
            continue
        try:
            title = notebook.title[language]
        except (KeyError, AttributeError, TypeError):
            title = notebook.path.stem
        titles.append(title or notebook.path.stem)
    return titles


def _match_full_section(
    section_spec: SectionSpec, full_sections: list[SectionSpec]
) -> SectionSpec | None:
    """Find *section_spec* in *full_sections* (parsed with ``keep_disabled``).

    Matches by ``id`` first, then by exact bilingual name. Returns ``None``
    when no counterpart is found, or when the name match is ambiguous (more
    than one same-named, id-less section) — better to skip disabled-subsection
    recovery than to attach the wrong section's subsections. Used so an enabled
    section can recover its *disabled* subsections (which the enabled-only
    parse dropped).
    """
    if section_spec.id is not None:
        for candidate in full_sections:
            if candidate.id == section_spec.id:
                return candidate
    name_matches = [
        c
        for c in full_sections
        if c.name.de == section_spec.name.de and c.name.en == section_spec.name.en
    ]
    if len(name_matches) == 1:
        return name_matches[0]
    return None


def _subsections_to_render(
    section_spec: SectionSpec,
    full_sections: list[SectionSpec] | None,
    include_disabled: bool,
) -> list[SubsectionSpec]:
    """Return the subsections to render for a section.

    Normally the section's own (enabled-only) subsections; when
    ``include_disabled`` is set and the full (``keep_disabled``) sections
    are available, the matching full section's subsections — which also
    carry the disabled ones.
    """
    if include_disabled and full_sections is not None:
        full = _match_full_section(section_spec, full_sections)
        if full is not None:
            return full.subsections
    return section_spec.subsections


def _subsection_deck_titles(
    subsection: SubsectionSpec,
    course: Course,
    language: str,
    resolved_titles: dict[str, list[str]],
) -> list[str]:
    """Resolve a subsection's deck titles, in document order.

    Enabled subsections read from *resolved_titles* (the section's resolved
    decks). Disabled subsections fall back to a filesystem read via
    :func:`_disabled_topic_slides`, because their topics are not part of the
    built course; a topic that is not (yet) on disk falls back to its id so
    planned topics stay visible, matching the disabled whole-section path.
    """
    titles: list[str] = []
    if subsection.enabled:
        for topic_spec in subsection.topics:
            titles.extend(resolved_titles.get(topic_spec.id, []))
        return titles
    for topic_spec in subsection.topics:
        slides = _disabled_topic_slides(course, topic_spec, language)
        if slides:
            titles.extend(title for _file_name, title in slides)
        else:
            titles.append(topic_spec.id)
    return titles


def _render_section_subsections(
    section: Section,
    subsections: list[SubsectionSpec],
    course: Course,
    language: str,
) -> list[str]:
    """Render the bullet lines for a section that uses subsections.

    Bare topics (not under any subsection) are listed first as flat
    bullets; each subsection then renders as a bold-label bullet with its
    decks indented beneath it. Disabled subsections get a ``(disabled)``
    marker.
    """
    resolved_titles = {topic.id: _topic_deck_titles(topic, language) for topic in section.topics}
    subsection_topic_ids = {t.id for sub in subsections for t in sub.topics}

    lines: list[str] = []
    # Bare topics first, in section (document) order.
    for topic in section.topics:
        if topic.id in subsection_topic_ids:
            continue
        for title in resolved_titles.get(topic.id, []):
            lines.append(f"- {title}")

    for subsection in subsections:
        marker = "" if subsection.enabled else " (disabled)"
        label = subsection_label(subsection, language) or "(unnamed)"
        lines.append(f"- **{label}**{marker}")
        for title in _subsection_deck_titles(subsection, course, language, resolved_titles):
            lines.append(f"  - {title}{marker}")
    return lines


def _subsections_json(
    section: Section,
    subsections: list[SubsectionSpec],
    course: Course,
    language: str,
) -> list[dict]:
    """Build the JSON ``subsections`` list for a section.

    Enabled subsection topics reuse the section's resolved decks; disabled
    (or otherwise unresolved) topics fall back to a filesystem read so the
    structure is still populated under ``--include-disabled``.
    """
    resolved: dict[str, dict] = {}
    for topic in section.topics:
        resolved[topic.id] = {
            "directory": str(topic.path),
            "slides": [
                {"file": f.path.name, "title": f.title[language]}
                for f in topic.files
                if isinstance(f, NotebookFile)
            ],
        }

    result: list[dict] = []
    for subsection in subsections:
        topics_out: list[dict] = []
        for topic_spec in subsection.topics:
            if subsection.enabled and topic_spec.id in resolved:
                topics_out.append({"topic_id": topic_spec.id, **resolved[topic_spec.id]})
            else:
                topic_path = course._topic_path_map.get(topic_spec.id)
                slides_data = _disabled_topic_slides(course, topic_spec, language) or []
                topics_out.append(
                    {
                        "topic_id": topic_spec.id,
                        "directory": str(topic_path) if topic_path is not None else None,
                        "slides": [{"file": fname, "title": title} for fname, title in slides_data],
                    }
                )
        result.append(
            {
                "weekday": subsection.weekday,
                "label": subsection_label(subsection, language),
                "enabled": subsection.enabled,
                "topics": topics_out,
            }
        )
    return result


def generate_outline(
    course: Course,
    language: str,
    *,
    disabled_sections: list[SectionSpec] | None = None,
    sections_only: bool = False,
    full_sections: list[SectionSpec] | None = None,
    include_disabled: bool = False,
) -> str:
    """Generate a Markdown outline for a course.

    Args:
        course: The course to generate an outline for
        language: Language code ('en' or 'de')
        disabled_sections: Disabled ``SectionSpec`` objects to include with a
            ``(disabled)`` marker. Interleaved into the output by declared
            order using ``id`` or name matching when possible, otherwise
            appended at the end.
        sections_only: When True, emit only section headings (no topic
            bullet points).

    Returns:
        Markdown string with the course outline
    """
    lines = []

    # Course title as H1
    lines.append(f"# {course.name[language]}")
    lines.append("")

    # course.sections aligns 1:1 with course.spec.sections (no section
    # selection is applied in the outline path, and the spec was parsed
    # enabled-only). The spec side carries the retained subsection grouping.
    for section, section_spec in zip(course.sections, course.spec.sections, strict=True):
        lines.append(f"## {section.name[language]}")
        lines.append("")
        if sections_only:
            continue
        subsections = _subsections_to_render(section_spec, full_sections, include_disabled)
        if subsections:
            lines.extend(_render_section_subsections(section, subsections, course, language))
        else:
            # Unchanged flat rendering for sections without subsections.
            for notebook in section.notebooks:
                if isinstance(notebook, NotebookFile):
                    title = notebook.title[language]
                    lines.append(f"- {title}")
        lines.append("")

    for section_spec in disabled_sections or []:
        lines.append(f"## {section_spec.name[language]} (disabled)")
        lines.append("")
        if sections_only:
            continue
        if not section_spec.topics:
            lines.append("- (no topics declared)")
            lines.append("")
            continue
        for topic_spec in section_spec.topics:
            slides = _disabled_topic_slides(course, topic_spec, language)
            if slides is None:
                # Topic does not exist on disk — fall back to topic id.
                lines.append(f"- {topic_spec.id} (disabled)")
            elif not slides:
                # Topic resolved but has no slide files.
                lines.append(f"- {topic_spec.id} (disabled)")
            else:
                for _file_name, title in slides:
                    lines.append(f"- {title} (disabled)")
        lines.append("")

    return "\n".join(lines)


def generate_outline_json(
    course: Course,
    language: str,
    *,
    disabled_sections: list[SectionSpec] | None = None,
    sections_only: bool = False,
    full_sections: list[SectionSpec] | None = None,
    include_disabled: bool = False,
) -> dict:
    """Generate a structured JSON outline for a course.

    Args:
        course: The course to generate an outline for
        language: Language code ('en' or 'de')
        disabled_sections: Disabled ``SectionSpec`` objects to include in the
            output with ``"disabled": true`` markers. Disabled sections are
            appended after the enabled sections.
        sections_only: When True, omit the ``topics`` list from each section
            entry.

    Returns:
        Dict with the course outline in structured form.
    """
    sections: list[dict] = []
    for section, section_spec in zip(course.sections, course.spec.sections, strict=True):
        entry: dict = {
            "number": len(sections) + 1,
            "name": section.name[language],
            "disabled": False,
        }
        if section.id is not None:
            entry["id"] = section.id
        if not sections_only:
            topics: list[dict] = []
            for topic in section.topics:
                slides: list[dict] = []
                for f in topic.files:
                    if isinstance(f, NotebookFile):
                        slides.append(
                            {
                                "file": f.path.name,
                                "title": f.title[language],
                            }
                        )
                topics.append(
                    {
                        "topic_id": topic.id,
                        "directory": str(topic.path),
                        "slides": slides,
                    }
                )
            entry["topics"] = topics
            subsections = _subsections_to_render(section_spec, full_sections, include_disabled)
            if subsections:
                entry["subsections"] = _subsections_json(section, subsections, course, language)
        sections.append(entry)

    for section_spec in disabled_sections or []:
        entry = {
            "number": len(sections) + 1,
            "name": section_spec.name[language],
            "disabled": True,
        }
        if section_spec.id is not None:
            entry["id"] = section_spec.id
        if not sections_only:
            topics = []
            for t in section_spec.topics:
                slides_data = _disabled_topic_slides(course, t, language)
                topic_path = course._topic_path_map.get(t.id)
                topics.append(
                    {
                        "topic_id": t.id,
                        "directory": str(topic_path) if topic_path is not None else None,
                        "slides": [
                            {"file": fname, "title": title} for fname, title in (slides_data or [])
                        ],
                    }
                )
            entry["topics"] = topics
        sections.append(entry)

    return {
        "course_name": course.name[language],
        "language": language,
        "sections": sections,
    }


def get_output_filename(course: Course, language: str, needs_suffix: bool) -> str:
    """Generate the output filename for a course outline.

    Args:
        course: The course
        language: Language code ('en' or 'de')
        needs_suffix: Whether to add language suffix (when titles are identical)

    Returns:
        Filename with .md extension
    """
    title = sanitize_file_name(course.name[language])
    if needs_suffix:
        return f"{title}-{language}.md"
    return f"{title}.md"


def titles_are_identical(course: Course) -> bool:
    """Check if the English and German course titles are identical."""
    return course.name.en == course.name.de


@click.command()
@click.argument(
    "spec-file",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
)
@click.option(
    "-o",
    "--output",
    "output_file",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write output to FILE (mutually exclusive with --output-dir).",
)
@click.option(
    "-d",
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    help="Write to DIR with auto-generated filenames (mutually exclusive with --output).",
)
@click.option(
    "-L",
    "--language",
    type=click.Choice(["de", "en"], case_sensitive=False),
    help="Language for the outline. Default: 'en' for stdout/--output, both for --output-dir.",
)
@click.option(
    "-f",
    "--format",
    "output_format",
    type=click.Choice(["markdown", "json"], case_sensitive=False),
    default="markdown",
    help="Output format. Default: markdown.",
)
@click.option(
    "--include-disabled",
    is_flag=True,
    default=False,
    help="Include sections marked 'enabled=\"false\"' in the output, "
    "tagged with a (disabled) marker. Default: disabled sections are omitted.",
)
@click.option(
    "--sections-only",
    is_flag=True,
    default=False,
    help="Emit only section headings, omitting the topic/slide entries within each section.",
)
def outline(
    spec_file: Path,
    output_file: Path | None,
    output_dir: Path | None,
    language: str | None,
    output_format: str,
    include_disabled: bool,
    sections_only: bool,
):
    """Generate an outline of a course in Markdown or JSON format.

    Creates a document with section names as headings and topic titles
    as entries. Use --format json for structured output.

    \b
    Examples:
        clm outline course.xml                  # Markdown to stdout
        clm outline course.xml --format json    # JSON to stdout
        clm outline course.xml -L de            # German outline
        clm outline course.xml -o out.md        # Write to file
        clm outline course.xml -d ./docs        # Both languages to directory
        clm outline course.xml --sections-only  # Section headings only
    """
    # Validate mutually exclusive options
    if output_file and output_dir:
        raise click.UsageError("--output and --output-dir are mutually exclusive.")

    # Load course specification.
    # The main spec always drops disabled sections; if --include-disabled is
    # set we parse a second time with keep_disabled=True to retrieve the
    # disabled SectionSpecs for annotation. Disabled sections cannot go
    # through Course.from_spec because they may reference non-existent
    # topic directories.
    try:
        spec = CourseSpec.from_file(spec_file)
    except CourseSpecError as e:
        raise click.ClickException(f"Failed to parse spec file: {e}") from None

    disabled_sections: list[SectionSpec] = []
    full_sections: list[SectionSpec] | None = None
    if include_disabled:
        try:
            full_spec = CourseSpec.from_file(spec_file, keep_disabled=True)
        except CourseSpecError as e:
            raise click.ClickException(f"Failed to parse spec file: {e}") from None
        disabled_sections = [s for s in full_spec.sections if not s.enabled]
        # Retained so enabled sections can also surface their *disabled*
        # subsections (issue #261). Disabled whole sections are rendered
        # separately via ``disabled_sections``.
        full_sections = full_spec.sections

    # Validate spec
    validation_errors = spec.validate()
    if validation_errors:
        error_msg = "\n".join(f"  - {e}" for e in validation_errors)
        raise click.ClickException(f"Spec validation failed:\n{error_msg}")

    # Resolve course paths using centralized helper
    data_dir, _ = resolve_course_paths(spec_file)

    # Create a lightweight course object (no output processing needed)
    course = Course.from_spec(
        spec,
        data_dir,
        output_root=None,  # We don't need output directories
    )

    # Select generator based on format
    is_json = output_format == "json"

    def _generate(lang: str) -> str:
        if is_json:
            return json.dumps(
                generate_outline_json(
                    course,
                    lang,
                    disabled_sections=disabled_sections,
                    sections_only=sections_only,
                    full_sections=full_sections,
                    include_disabled=include_disabled,
                ),
                indent=2,
            )
        return generate_outline(
            course,
            lang,
            disabled_sections=disabled_sections,
            sections_only=sections_only,
            full_sections=full_sections,
            include_disabled=include_disabled,
        )

    # Determine languages to generate
    if output_dir:
        languages = [language] if language else ["en", "de"]
    else:
        languages = [language] if language else ["en"]

    # Generate and output
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        needs_suffix = titles_are_identical(course)
        ext = ".json" if is_json else ".md"

        for lang in languages:
            content = _generate(lang)
            title = sanitize_file_name(course.name[lang])
            suffix = f"-{lang}" if needs_suffix else ""
            filename = f"{title}{suffix}{ext}"
            file_path = output_dir / filename
            file_path.write_text(content, encoding="utf-8")
            click.echo(f"Written: {file_path}")

    elif output_file:
        lang = languages[0]
        content = _generate(lang)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(content, encoding="utf-8")
        click.echo(f"Written: {output_file}")

    else:
        lang = languages[0]
        content = _generate(lang)
        click.echo(content)
