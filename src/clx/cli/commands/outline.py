"""Outline command for generating course outlines in Markdown format.

This module provides a command to export a course structure as a Markdown
outline, with section names as headings and topic titles as bullet points.
"""

from pathlib import Path

import click

from clx.core.course import Course
from clx.core.course_files.notebook_file import NotebookFile
from clx.core.course_spec import CourseSpec, CourseSpecError
from clx.core.utils.text_utils import sanitize_file_name


def generate_outline(course: Course, language: str) -> str:
    """Generate a Markdown outline for a course.

    Args:
        course: The course to generate an outline for
        language: Language code ('en' or 'de')

    Returns:
        Markdown string with the course outline
    """
    lines = []

    # Course title as H1
    lines.append(f"# {course.name[language]}")
    lines.append("")

    for section in course.sections:
        # Section name as H2
        lines.append(f"## {section.name[language]}")

        # Get notebook titles as bullet points
        for notebook in section.notebooks:
            if isinstance(notebook, NotebookFile):
                title = notebook.title[language]
                lines.append(f"- {title}")

        lines.append("")

    return "\n".join(lines)


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
def outline(spec_file: Path, output_file: Path | None, output_dir: Path | None, language: str | None):
    """Generate a Markdown outline of a course.

    Creates a Markdown document with section names as headings and
    topic titles as bullet points.

    Examples:

    \b
        clx outline course.xml              # Print English outline to stdout
        clx outline course.xml -L de        # Print German outline to stdout
        clx outline course.xml -o out.md    # Write English outline to file
        clx outline course.xml -d ./docs    # Write both languages to directory
    """
    # Validate mutually exclusive options
    if output_file and output_dir:
        raise click.UsageError("--output and --output-dir are mutually exclusive.")

    # Load course specification
    try:
        spec = CourseSpec.from_file(spec_file)
    except CourseSpecError as e:
        raise click.ClickException(f"Failed to parse spec file: {e}") from None

    # Validate spec
    validation_errors = spec.validate()
    if validation_errors:
        error_msg = "\n".join(f"  - {e}" for e in validation_errors)
        raise click.ClickException(f"Spec validation failed:\n{error_msg}")

    # Determine data directory (parent of spec file's parent, following build command pattern)
    data_dir = spec_file.parents[1] if spec_file.parent.name else spec_file.parent

    # Create a lightweight course object (no output processing needed)
    course = Course.from_spec(
        spec,
        data_dir,
        output_root=None,  # We don't need output directories
    )

    # Determine languages to generate
    if output_dir:
        # --output-dir: both languages by default, or single if specified
        languages = [language] if language else ["en", "de"]
    else:
        # stdout or --output: default to English
        languages = [language] if language else ["en"]

    # Generate and output
    if output_dir:
        # Ensure output directory exists
        output_dir.mkdir(parents=True, exist_ok=True)

        needs_suffix = titles_are_identical(course)

        for lang in languages:
            content = generate_outline(course, lang)
            filename = get_output_filename(course, lang, needs_suffix)
            file_path = output_dir / filename
            file_path.write_text(content, encoding="utf-8")
            click.echo(f"Written: {file_path}")

    elif output_file:
        # Write to specified file
        lang = languages[0]
        content = generate_outline(course, lang)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(content, encoding="utf-8")
        click.echo(f"Written: {output_file}")

    else:
        # Print to stdout
        lang = languages[0]
        content = generate_outline(course, lang)
        click.echo(content)
