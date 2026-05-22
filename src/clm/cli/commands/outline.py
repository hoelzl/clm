"""Outline command for generating course outlines in Markdown and JSON format.

This module provides a command to export a course structure as a Markdown
outline or structured JSON, with section names as headings and topic titles
as entries.
"""

import json
from pathlib import Path

import click

from clm.core.course import Course
from clm.core.course_files.notebook_file import NotebookFile
from clm.core.course_paths import resolve_course_paths
from clm.core.course_spec import CourseSpec, CourseSpecError, SectionSpec, TopicSpec
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


def generate_outline(
    course: Course,
    language: str,
    *,
    disabled_sections: list[SectionSpec] | None = None,
    sections_only: bool = False,
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

    for section in course.sections:
        lines.append(f"## {section.name[language]}")
        lines.append("")
        if not sections_only:
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
    for section in course.sections:
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
    if include_disabled:
        try:
            full_spec = CourseSpec.from_file(spec_file, keep_disabled=True)
        except CourseSpecError as e:
            raise click.ClickException(f"Failed to parse spec file: {e}") from None
        disabled_sections = [s for s in full_spec.sections if not s.enabled]

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
                ),
                indent=2,
            )
        return generate_outline(
            course,
            lang,
            disabled_sections=disabled_sections,
            sections_only=sections_only,
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
