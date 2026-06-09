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

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

import click

from clm.core.utils.notebook_utils import find_notebook_titles
from clm.infrastructure.utils.path_utils import is_slides_file, split_lang_suffix

if TYPE_CHECKING:
    from clm.core.course import Course
    from clm.core.course_files.notebook_file import NotebookFile
    from clm.core.course_spec import SectionSpec, SubsectionSpec, TopicSpec
    from clm.core.section import Section

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
    """``--include-optional`` and ``--include-disabled`` selection gates.

    ``--include-disabled`` is an *optional-value* option (the only one in the
    CLI): omitted it excludes disabled content; a bare ``--include-disabled``
    (or ``=marked``) includes it tagged ``(disabled)``; ``=merge`` folds it into
    the normal course flow with no marker. Use the ``=VALUE`` form to be
    unambiguous — a bare flag placed *immediately before* the ``SPEC_FILE``
    positional would consume it as the value (Click optional-value behaviour),
    so keep the spec file first.
    """
    func = click.option(
        "--include-disabled",
        "disabled_mode",
        is_flag=False,
        flag_value="marked",
        default=None,
        type=click.Choice(["marked", "merge"], case_sensitive=False),
        metavar="[marked|merge]",
        help='Include sections/subsections marked enabled="false". Bare (or '
        "=marked): tagged with a (disabled) marker (disabled whole sections "
        "after the enabled ones in outline/summary). =merge: folded into the "
        "normal course flow, in declared order, with no marker. Omitted: "
        "disabled content is excluded (default).",
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


def resolve_disabled_mode(disabled_mode: str | None) -> tuple[bool, bool]:
    """Translate the ``--include-disabled`` value into two booleans.

    Returns ``(include_disabled, merge_disabled)``:

    * ``None``      -> ``(False, False)`` — disabled content excluded (default).
    * ``"marked"``  -> ``(True, False)``  — included with a ``(disabled)`` marker.
    * ``"merge"``   -> ``(True, True)``   — folded into the normal flow, no marker.

    The export command handlers call this once and thread the two booleans into
    the generators, which keep their plain-``bool`` signatures.
    """
    return (disabled_mode is not None, disabled_mode == "merge")


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
# Per-language filtering (split .de/.en companions)
# ---------------------------------------------------------------------------
def notebook_in_language(notebook: NotebookFile, language: str) -> bool:
    """Whether a resolved ``NotebookFile`` belongs in a *language*'s document.

    A bilingual file (``output_language_filter is None``) belongs to every
    language; a split ``.de``/``.en`` companion only to its own. This is the
    predicate every "resolved course" enumeration must apply so a split pair
    contributes a single entry, matching the build's per-language routing.
    """
    flt = notebook.output_language_filter
    return flt is None or flt == language


def path_in_language(path: Path, language: str) -> bool:
    """The filesystem twin of :func:`notebook_in_language`.

    Used where slide files are read straight from disk (disabled topics, which
    are not part of the built course). A bilingual ``slides_x.py`` matches every
    language; a split ``slides_x.de.py`` / ``slides_x.en.py`` only its own.
    """
    lang = split_lang_suffix(path)
    return lang is None or lang == language


# ---------------------------------------------------------------------------
# Declared-order section walk (used by --include-disabled=merge)
# ---------------------------------------------------------------------------
def iter_declared_sections(
    course: Course, full_sections: list[SectionSpec]
) -> Iterator[tuple[SectionSpec, tuple[Section, SectionSpec] | None]]:
    """Walk the full declared section list in document order for merge mode.

    Yields ``(full_spec, built)`` where *built* is the ``(Section,
    SectionSpec)`` pair from the enabled-parse course for an enabled section, or
    ``None`` for a disabled one. Enabled sections are matched to the built
    course **positionally** — ``course.sections`` is the enabled subset of
    *full_sections* in the same document order — so this is robust to duplicate,
    id-less section names. (A name/id-keyed map would collapse two same-named
    id-less sections onto one built object; the positional walk does not.)
    """
    built_iter = iter(zip(course.sections, course.spec.sections, strict=False))
    for full_spec in full_sections:
        if full_spec.enabled:
            yield full_spec, next(built_iter, None)
        else:
            yield full_spec, None


# ---------------------------------------------------------------------------
# Disabled-topic resolution (filesystem fallback)
# ---------------------------------------------------------------------------
def disabled_topic_files(course: Course, topic_spec: TopicSpec, language: str) -> list[Path] | None:
    """Return the slide-file paths of a topic, resolved from the filesystem.

    Used to surface topics that are *not* part of the built course (disabled
    sections/subsections). Resolves ``topic_spec.id`` against the course's
    filesystem-wide topic map. Returns ``None`` when the id cannot be resolved
    (so callers can fall back to a ``<topic_id>`` display); an empty list when
    the topic resolves but contains no slide files.

    Split ``.de``/``.en`` companions are filtered to *language* via
    :func:`path_in_language` so a split pair contributes one file — the same
    per-language routing the built course applies, kept consistent for the
    not-built (disabled) topics read straight from disk.
    """
    topic_path = course._topic_path_map.get(topic_spec.id)
    if topic_path is None:
        return None

    slide_paths: list[Path] = []
    if topic_path.is_file():
        if is_slides_file(topic_path) and path_in_language(topic_path, language):
            slide_paths.append(topic_path)
    elif topic_path.is_dir():
        for child in sorted(topic_path.iterdir()):
            if child.is_file() and is_slides_file(child) and path_in_language(child, language):
                slide_paths.append(child)
    return slide_paths


def disabled_topic_slides(
    course: Course, topic_spec: TopicSpec, language: str
) -> list[tuple[str, str]] | None:
    """Return ``(file_name, title)`` pairs for the slide files of a topic.

    Reads the H1 header from each slide file the same way :class:`NotebookFile`
    does. Returns ``None``/``[]`` following :func:`disabled_topic_files`.
    """
    slide_paths = disabled_topic_files(course, topic_spec, language)
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
