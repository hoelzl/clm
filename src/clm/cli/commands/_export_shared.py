"""Shared building blocks for the ``clm export`` command group.

``outline``, ``schedule`` and ``summary`` all turn a course spec into a
human-readable document. This module holds the option decorators and the
section/subsection visibility rules they share, so the three commands stay
consistent. It deliberately imports nothing from the three command modules, so
they can all import from here without an import cycle.

``optional="true"`` and ``enabled="false"`` are presentation-only for these
commands — they never change the build, only what appears in the document.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

import click

from clm.core.utils.notebook_utils import find_notebook_titles
from clm.infrastructure.utils.path_utils import is_slides_file

if TYPE_CHECKING:
    from clm.core.course import Course
    from clm.core.course_spec import SectionSpec, SubsectionSpec, TopicSpec

F = TypeVar("F", bound=Callable[..., object])


# ---------------------------------------------------------------------------
# Option decorators (shared spelling across all three commands)
# ---------------------------------------------------------------------------
def spec_argument(func: F) -> F:
    """The ``SPEC_FILE`` positional argument common to every export command."""
    return click.argument(
        "spec-file",
        type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
    )(func)


def language_option(
    *,
    default: str | None = None,
    aliases: tuple[str, ...] = (),
    help: str = "Language for the generated document.",
) -> Callable[[F], F]:
    """``-L/--language`` with a per-command default and optional extra aliases.

    The destination parameter is always ``language``. ``default=None`` is used
    by ``outline`` to mean "English to stdout, both languages to a directory";
    a non-``None`` default is shown in ``--help``.
    """
    decls = ["-L", "--language", *aliases]

    def decorator(func: F) -> F:
        return click.option(
            *decls,
            type=click.Choice(["de", "en"], case_sensitive=False),
            default=default,
            show_default=default is not None,
            help=help,
        )(func)

    return decorator


def output_options(func: F) -> F:
    """``-o/--output`` (FILE) and ``-d/--output-dir`` (DIR), mutually exclusive."""
    func = click.option(
        "-d",
        "--output-dir",
        type=click.Path(file_okay=False, path_type=Path),
        help="Write to DIR with auto-generated filenames (mutually exclusive with --output).",
    )(func)
    func = click.option(
        "-o",
        "--output",
        "output_file",
        type=click.Path(dir_okay=False, path_type=Path),
        help="Write output to FILE (mutually exclusive with --output-dir).",
    )(func)
    return func


def selection_options(func: F) -> F:
    """``--include-optional`` and ``--include-disabled`` selection gates."""
    func = click.option(
        "--include-disabled",
        "include_disabled",
        is_flag=True,
        default=False,
        help='Include sections/subsections marked enabled="false", tagged with a '
        "(disabled) marker. Off by default.",
    )(func)
    func = click.option(
        "--include-optional",
        "include_optional",
        is_flag=True,
        default=False,
        help='Include modules marked optional="true" (on a <section> or <subsection>). '
        "Off by default; optional modules that are also disabled are only shown when "
        "--include-disabled is given as well.",
    )(func)
    return func


def check_exclusive_output(output_file: Path | None, output_dir: Path | None) -> None:
    """Raise a :class:`click.UsageError` if both output modes were given."""
    if output_file and output_dir:
        raise click.UsageError("--output and --output-dir are mutually exclusive.")


# ---------------------------------------------------------------------------
# Visibility rules
# ---------------------------------------------------------------------------
def section_visible(section_spec: SectionSpec, *, include_optional: bool) -> bool:
    """Whether a whole section should appear in a document view.

    An optional section is hidden unless ``include_optional`` is set. (Disabled
    whole sections are handled separately by each command, because their topics
    are not part of the built course.)
    """
    return include_optional or not section_spec.optional


def subsection_visible(
    subsection: SubsectionSpec,
    *,
    include_optional: bool,
    include_disabled: bool,
) -> bool:
    """Whether a subsection should appear in a document view.

    Disabled is the stricter gate: a subsection that is both disabled and
    optional needs *both* flags to appear.
    """
    if not subsection.enabled and not include_disabled:
        return False
    if subsection.optional and not include_optional:
        return False
    return True


# ---------------------------------------------------------------------------
# Disabled-topic resolution (filesystem fallback)
# ---------------------------------------------------------------------------
def disabled_topic_files(course: Course, topic_spec: TopicSpec) -> list[Path] | None:
    """Return the slide-file paths of a topic, resolved from the filesystem.

    Used to surface topics that are *not* part of the built course (disabled
    sections/subsections). Resolves ``topic_spec.id`` against the course's
    filesystem-wide topic map. Returns ``None`` when the id cannot be resolved
    (so callers can fall back to a ``<topic_id>`` display); an empty list when
    the topic resolves but contains no slide files.
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
    return slide_paths


def disabled_topic_slides(
    course: Course, topic_spec: TopicSpec, language: str
) -> list[tuple[str, str]] | None:
    """Return ``(file_name, title)`` pairs for the slide files of a topic.

    Reads the H1 header from each slide file the same way :class:`NotebookFile`
    does. Returns ``None``/``[]`` following :func:`disabled_topic_files`.
    """
    slide_paths = disabled_topic_files(course, topic_spec)
    if slide_paths is None:
        return None

    results: list[tuple[str, str]] = []
    for path in slide_paths:
        try:
            text = path.read_text(encoding="utf-8")
            title = find_notebook_titles(text, default=path.stem)
            results.append((path.name, title[language]))
        except (OSError, ValueError):
            results.append((path.name, path.stem))
    return results
