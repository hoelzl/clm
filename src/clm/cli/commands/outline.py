"""Outline command for generating course outlines in Markdown and JSON format.

This module provides a command to export a course structure as a Markdown
outline or structured JSON, with section names as headings and topic titles
as entries.
"""

import json
from pathlib import Path

import click

from clm.cli.commands._export_shared import (
    check_exclusive_output,
    iter_declared_sections,
    language_option,
    notebook_in_language,
    output_options,
    resolve_disabled_mode,
    section_visible,
    selection_options,
    spec_argument,
    subsection_visible,
)
from clm.cli.commands._export_shared import (
    disabled_topic_slides as _disabled_topic_slides,
)
from clm.cli.commands.schedule import subsection_label
from clm.core.course import Course
from clm.core.course_files.notebook_file import NotebookFile
from clm.core.course_paths import resolve_course_paths
from clm.core.course_spec import (
    CourseSpec,
    CourseSpecError,
    SectionSpec,
    SubsectionSpec,
)
from clm.core.section import Section
from clm.core.utils.text_utils import sanitize_file_name


def _topic_deck_titles(topic, language: str) -> list[str]:
    """Return the deck titles of one resolved topic for *language*.

    Split ``.de.py`` / ``.en.py`` companions are filtered to the requested
    language so a split pair contributes one title, matching the build's
    per-language routing.
    """
    titles: list[str] = []
    for notebook in topic.notebooks:
        if not notebook_in_language(notebook, language):
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


def _candidate_subsections(
    section_spec: SectionSpec,
    full_sections: list[SectionSpec] | None,
    include_disabled: bool,
) -> list[SubsectionSpec]:
    """All declared subsections of a section, *before* visibility filtering.

    Normally the section's own (enabled-only) subsections; when
    ``include_disabled`` is set and the full (``keep_disabled``) sections are
    available, the matching full section's subsections — which also carry the
    disabled ones. Used both as the render source and as the set whose topics
    are *not* "bare" (so hiding a subsection hides its topics rather than
    demoting them to flat bullets).
    """
    if include_disabled and full_sections is not None:
        full = _match_full_section(section_spec, full_sections)
        if full is not None:
            return full.subsections
    return section_spec.subsections


def _subsections_to_render(
    candidates: list[SubsectionSpec],
    include_disabled: bool,
    include_optional: bool,
) -> list[SubsectionSpec]:
    """Filter candidate subsections down to the visible ones."""
    return [
        sub
        for sub in candidates
        if subsection_visible(
            sub, include_optional=include_optional, include_disabled=include_disabled
        )
    ]


def _visible_topic_ids(
    section: Section,
    candidates: list[SubsectionSpec],
    visible: list[SubsectionSpec],
) -> set[str]:
    """Topic ids that should appear in a section's flat ``topics`` list.

    A topic is visible if it is bare (under no declared subsection) or it is
    under a *visible* subsection. Topics under a hidden (optional/disabled)
    subsection are excluded.
    """
    candidate_ids = {t.id for sub in candidates for t in sub.topics}
    visible_ids = {t.id for sub in visible for t in sub.topics}
    return {
        topic.id
        for topic in section.topics
        if topic.id not in candidate_ids or topic.id in visible_ids
    }


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
    candidates: list[SubsectionSpec],
    course: Course,
    language: str,
    *,
    mark_disabled: bool = True,
    show_weekdays: bool = False,
) -> list[str]:
    """Render the bullet lines for a section that uses subsections.

    Bare topics (under *no* declared subsection) are listed first as flat
    bullets. With ``show_weekdays`` each visible subsection then renders as a
    bold-label bullet (its weekday/name) with its decks indented beneath it;
    without it (the default) the subsection grouping is dropped and its decks
    are emitted as flat bullets in document order — so the outline reads the
    same whether or not a section happens to declare ``<subsection>`` groups.
    Disabled subsections get a ``(disabled)`` marker unless ``mark_disabled`` is
    False (``--include-disabled=merge``, which folds disabled content into the
    normal flow). ``candidates`` is the full declared subsection set (visible or
    not) so a topic under a hidden subsection is not mistaken for a bare topic.
    """
    resolved_titles = {topic.id: _topic_deck_titles(topic, language) for topic in section.topics}
    subsection_topic_ids = {t.id for sub in candidates for t in sub.topics}

    lines: list[str] = []
    # Bare topics first, in section (document) order.
    for topic in section.topics:
        if topic.id in subsection_topic_ids:
            continue
        for title in resolved_titles.get(topic.id, []):
            lines.append(f"- {title}")

    for subsection in subsections:
        marker = "" if (subsection.enabled or not mark_disabled) else " (disabled)"
        titles = _subsection_deck_titles(subsection, course, language, resolved_titles)
        if show_weekdays:
            label = subsection_label(subsection, language) or "(unnamed)"
            lines.append(f"- **{label}**{marker}")
            for title in titles:
                lines.append(f"  - {title}{marker}")
        else:
            for title in titles:
                lines.append(f"- {title}{marker}")
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
                if isinstance(f, NotebookFile) and notebook_in_language(f, language)
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
                # `weekday` is the first token (back-compat); `weekdays` is the
                # full ordered list for multi-day subsections.
                "weekday": subsection.weekday,
                "weekdays": list(subsection.weekdays),
                "label": subsection_label(subsection, language),
                "enabled": subsection.enabled,
                "optional": subsection.optional,
                "topics": topics_out,
            }
        )
    return result


def _render_enabled_section_block(
    section: Section,
    section_spec: SectionSpec,
    course: Course,
    language: str,
    *,
    sections_only: bool,
    full_sections: list[SectionSpec] | None,
    include_disabled: bool,
    include_optional: bool,
    mark_disabled: bool,
    show_weekdays: bool,
) -> list[str]:
    """Markdown lines for one enabled section (heading, body, trailing blank).

    ``mark_disabled`` controls whether disabled *subsections* nested in this
    section keep their ``(disabled)`` marker (True, default/marked mode) or are
    folded in silently (False, ``--include-disabled=merge``). ``show_weekdays``
    controls whether ``<subsection>`` weekday/name groupings are rendered as
    bold labels or flattened into plain bullets.
    """
    lines = [f"## {section.name[language]}", ""]
    if sections_only:
        return lines
    candidates = _candidate_subsections(section_spec, full_sections, include_disabled)
    subsections = _subsections_to_render(candidates, include_disabled, include_optional)
    if candidates:
        lines.extend(
            _render_section_subsections(
                section,
                subsections,
                candidates,
                course,
                language,
                mark_disabled=mark_disabled,
                show_weekdays=show_weekdays,
            )
        )
    else:
        # Flat rendering for sections without subsections; split .de/.en
        # companions are filtered to the requested language.
        for notebook in section.notebooks:
            if isinstance(notebook, NotebookFile) and notebook_in_language(notebook, language):
                lines.append(f"- {notebook.title[language]}")
    lines.append("")
    return lines


def _disabled_topic_titles(course: Course, topic_spec, language: str) -> list[str]:
    """Deck titles for one *disabled* topic, read from disk (id fallback).

    Returns the topic id (as a single-element list) when the topic has no
    resolvable slide files, so a planned-but-absent topic still appears.
    """
    slides = _disabled_topic_slides(course, topic_spec, language)
    if not slides:
        return [topic_spec.id]
    return [title for _file_name, title in slides]


def _render_disabled_section_subsections(
    section_spec: SectionSpec,
    course: Course,
    language: str,
    marker: str,
) -> list[str]:
    """Render a disabled whole section's bullets with its ``<subsection>`` groups.

    Used by ``--weekdays always`` so disabled weeks show the same weekday/name
    grouping as enabled ones. All decks are read from the filesystem (disabled
    topics are not part of the built course). Bare topics (under no subsection)
    are listed first; each subsection then renders as a bold-label bullet with
    its decks indented beneath it. Every line carries the section's ``marker``
    (the whole section is disabled); the subsection's own ``enabled`` flag adds
    no extra marker beyond that.
    """
    subsection_topic_ids = {t.id for sub in section_spec.subsections for t in sub.topics}
    lines: list[str] = []
    for topic_spec in section_spec.topics:
        if topic_spec.id in subsection_topic_ids:
            continue
        for title in _disabled_topic_titles(course, topic_spec, language):
            lines.append(f"- {title}{marker}")
    for subsection in section_spec.subsections:
        label = subsection_label(subsection, language) or "(unnamed)"
        lines.append(f"- **{label}**{marker}")
        for topic_spec in subsection.topics:
            for title in _disabled_topic_titles(course, topic_spec, language):
                lines.append(f"  - {title}{marker}")
    return lines


def _render_disabled_section_block(
    section_spec: SectionSpec,
    course: Course,
    language: str,
    *,
    sections_only: bool,
    mark_disabled: bool,
    show_weekdays: bool,
) -> list[str]:
    """Markdown lines for one disabled whole section, read from the filesystem.

    With ``mark_disabled`` the heading and every bullet carry a ``(disabled)``
    marker; ``--include-disabled=merge`` passes False so the section reads like
    any enabled one. With ``show_weekdays`` and a section that declares
    ``<subsection>`` groups, the decks render grouped under bold weekday/name
    labels (mirroring enabled sections); otherwise they are flat bullets.
    """
    marker = " (disabled)" if mark_disabled else ""
    lines = [f"## {section_spec.name[language]}{marker}", ""]
    if sections_only:
        return lines
    if show_weekdays and section_spec.subsections:
        lines.extend(_render_disabled_section_subsections(section_spec, course, language, marker))
        lines.append("")
        return lines
    if not section_spec.topics:
        lines.append("- (no topics declared)")
        lines.append("")
        return lines
    for topic_spec in section_spec.topics:
        for title in _disabled_topic_titles(course, topic_spec, language):
            lines.append(f"- {title}{marker}")
    lines.append("")
    return lines


def generate_outline(
    course: Course,
    language: str,
    *,
    disabled_sections: list[SectionSpec] | None = None,
    sections_only: bool = False,
    full_sections: list[SectionSpec] | None = None,
    include_disabled: bool = False,
    include_optional: bool = False,
    merge_disabled: bool = False,
    show_weekdays: bool = False,
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
        include_optional: When False (default), sections/subsections marked
            ``optional="true"`` are omitted.
        show_weekdays: When True, ``<subsection>`` weekday/name groupings are
            rendered as bold labels with their decks indented beneath. When
            False (default), the grouping is dropped and every deck is a flat
            bullet, so the outline reads uniformly regardless of which sections
            declare subsections.

    Returns:
        Markdown string with the course outline
    """
    # Course title as H1.
    lines = [f"# {course.name[language]}", ""]

    if merge_disabled and full_sections is not None:
        # Fold disabled whole sections into declared order, with no markers.
        for full_spec, built in iter_declared_sections(course, full_sections):
            if full_spec.optional and not include_optional:
                continue
            if full_spec.enabled:
                if built is None:
                    continue
                section, section_spec = built
                lines.extend(
                    _render_enabled_section_block(
                        section,
                        section_spec,
                        course,
                        language,
                        sections_only=sections_only,
                        full_sections=full_sections,
                        include_disabled=include_disabled,
                        include_optional=include_optional,
                        mark_disabled=False,
                        show_weekdays=show_weekdays,
                    )
                )
            else:
                lines.extend(
                    _render_disabled_section_block(
                        full_spec,
                        course,
                        language,
                        sections_only=sections_only,
                        mark_disabled=False,
                        show_weekdays=show_weekdays,
                    )
                )
        return "\n".join(lines)

    # Default / marked mode: enabled sections in order, then disabled whole
    # sections appended at the end with a (disabled) marker.
    # course.sections aligns 1:1 with course.spec.sections (no section
    # selection is applied in the outline path, and the spec was parsed
    # enabled-only). The spec side carries the retained subsection grouping.
    for section, section_spec in zip(course.sections, course.spec.sections, strict=True):
        if not section_visible(section_spec, include_optional=include_optional):
            continue
        lines.extend(
            _render_enabled_section_block(
                section,
                section_spec,
                course,
                language,
                sections_only=sections_only,
                full_sections=full_sections,
                include_disabled=include_disabled,
                include_optional=include_optional,
                mark_disabled=True,
                show_weekdays=show_weekdays,
            )
        )

    for section_spec in disabled_sections or []:
        if section_spec.optional and not include_optional:
            continue
        lines.extend(
            _render_disabled_section_block(
                section_spec,
                course,
                language,
                sections_only=sections_only,
                mark_disabled=True,
                show_weekdays=show_weekdays,
            )
        )

    return "\n".join(lines)


def _enabled_section_json(
    section: Section,
    section_spec: SectionSpec,
    course: Course,
    language: str,
    *,
    number: int,
    sections_only: bool,
    full_sections: list[SectionSpec] | None,
    include_disabled: bool,
    include_optional: bool,
) -> dict:
    """JSON entry for one enabled section.

    Split ``.de``/``.en`` companions are filtered to *language* so a split pair
    contributes one slide entry.
    """
    entry: dict = {"number": number, "name": section.name[language], "disabled": False}
    if section.id is not None:
        entry["id"] = section.id
    if not sections_only:
        candidates = _candidate_subsections(section_spec, full_sections, include_disabled)
        subsections = _subsections_to_render(candidates, include_disabled, include_optional)
        visible_topic_ids = _visible_topic_ids(section, candidates, subsections)
        topics: list[dict] = []
        for topic in section.topics:
            if topic.id not in visible_topic_ids:
                continue
            slides = [
                {"file": f.path.name, "title": f.title[language]}
                for f in topic.files
                if isinstance(f, NotebookFile) and notebook_in_language(f, language)
            ]
            topics.append({"topic_id": topic.id, "directory": str(topic.path), "slides": slides})
        entry["topics"] = topics
        if candidates:
            entry["subsections"] = _subsections_json(section, subsections, course, language)
    return entry


def _disabled_section_json(
    section_spec: SectionSpec,
    course: Course,
    language: str,
    *,
    number: int,
    sections_only: bool,
) -> dict:
    """JSON entry for one disabled whole section, read from the filesystem.

    The ``"disabled"`` field stays ``True`` even under ``merge`` — it is
    structured metadata, not a visible marker — while ``merge`` changes only the
    section's *placement* (declared order rather than appended at the end).
    """
    entry: dict = {"number": number, "name": section_spec.name[language], "disabled": True}
    if section_spec.id is not None:
        entry["id"] = section_spec.id
    if not sections_only:
        topics: list[dict] = []
        for topic_spec in section_spec.topics:
            slides_data = _disabled_topic_slides(course, topic_spec, language)
            topic_path = course._topic_path_map.get(topic_spec.id)
            topics.append(
                {
                    "topic_id": topic_spec.id,
                    "directory": str(topic_path) if topic_path is not None else None,
                    "slides": [
                        {"file": fname, "title": title} for fname, title in (slides_data or [])
                    ],
                }
            )
        entry["topics"] = topics
    return entry


def generate_outline_json(
    course: Course,
    language: str,
    *,
    disabled_sections: list[SectionSpec] | None = None,
    sections_only: bool = False,
    full_sections: list[SectionSpec] | None = None,
    include_disabled: bool = False,
    include_optional: bool = False,
    merge_disabled: bool = False,
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
        include_optional: When False (default), sections/subsections marked
            ``optional="true"`` are omitted.

    Returns:
        Dict with the course outline in structured form.
    """
    sections: list[dict] = []

    if merge_disabled and full_sections is not None:
        # Fold disabled whole sections into declared order (placement only; the
        # per-entry "disabled" flag stays truthful).
        for full_spec, built in iter_declared_sections(course, full_sections):
            if full_spec.optional and not include_optional:
                continue
            if full_spec.enabled:
                if built is None:
                    continue
                section, section_spec = built
                sections.append(
                    _enabled_section_json(
                        section,
                        section_spec,
                        course,
                        language,
                        number=len(sections) + 1,
                        sections_only=sections_only,
                        full_sections=full_sections,
                        include_disabled=include_disabled,
                        include_optional=include_optional,
                    )
                )
            else:
                sections.append(
                    _disabled_section_json(
                        full_spec,
                        course,
                        language,
                        number=len(sections) + 1,
                        sections_only=sections_only,
                    )
                )
        return {
            "course_name": course.name[language],
            "language": language,
            "sections": sections,
        }

    for section, section_spec in zip(course.sections, course.spec.sections, strict=True):
        if not section_visible(section_spec, include_optional=include_optional):
            continue
        sections.append(
            _enabled_section_json(
                section,
                section_spec,
                course,
                language,
                number=len(sections) + 1,
                sections_only=sections_only,
                full_sections=full_sections,
                include_disabled=include_disabled,
                include_optional=include_optional,
            )
        )

    for section_spec in disabled_sections or []:
        if section_spec.optional and not include_optional:
            continue
        sections.append(
            _disabled_section_json(
                section_spec,
                course,
                language,
                number=len(sections) + 1,
                sections_only=sections_only,
            )
        )

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
@spec_argument
@output_options
@language_option(
    default=None,
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
@selection_options
@click.option(
    "--sections-only",
    is_flag=True,
    default=False,
    help="Emit only section headings, omitting the topic/slide entries within each section.",
)
@click.option(
    "--weekdays",
    "weekdays_mode",
    type=click.Choice(["never", "always"], case_sensitive=False),
    default="never",
    show_default=True,
    help="Show the <subsection> weekday/name groupings as bold labels. "
    "never (default): flatten every section's decks into plain bullets, so "
    "weeks read uniformly whether or not they declare subsections. always: "
    "group decks under their weekday/name label in every week. Markdown only; "
    "JSON always carries the grouping as structured data.",
)
def outline(
    spec_file: Path,
    output_file: Path | None,
    output_dir: Path | None,
    language: str | None,
    output_format: str,
    include_optional: bool,
    disabled_mode: str | None,
    sections_only: bool,
    weekdays_mode: str,
):
    """Generate an outline of a course in Markdown or JSON format.

    Creates a document with section names as headings and topic titles
    as entries. Use --format json for structured output.

    \b
    Examples:
        clm export outline course.xml                  # Markdown to stdout
        clm export outline course.xml --format json    # JSON to stdout
        clm export outline course.xml -L de            # German outline
        clm export outline course.xml -o out.md        # Write to file
        clm export outline course.xml -d ./docs        # Both languages to directory
        clm export outline course.xml --sections-only  # Section headings only
        clm export outline course.xml --weekdays always # Group decks by weekday
        clm export outline course.xml --include-optional  # Keep optional modules
        clm export outline course.xml --include-disabled         # Roadmap, tagged
        clm export outline course.xml --include-disabled=merge   # Roadmap, in flow
    """
    # Validate mutually exclusive options
    check_exclusive_output(output_file, output_dir)

    include_disabled, merge_disabled = resolve_disabled_mode(disabled_mode)
    show_weekdays = weekdays_mode.lower() == "always"

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
                    include_optional=include_optional,
                    merge_disabled=merge_disabled,
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
            include_optional=include_optional,
            merge_disabled=merge_disabled,
            show_weekdays=show_weekdays,
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
