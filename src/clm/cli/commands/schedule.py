"""``clm schedule`` — export a day-of-week deck listing (issue #261).

AZAV (and similar) certification requires a listing of which weekday each
video (slide deck) is presented, per week. The course spec expresses this
with an optional ``<subsection weekday="...">`` layer inside each
``<section>`` (``<section>`` = week, ``<subsection>`` = day; see
``clm info spec-files``). This command resolves the spec against the
filesystem decks and emits the certification listing in Markdown (default)
or CSV.

Each listing is single-language (``--lang``, default German): deck titles
come from the language-appropriate ``header_*`` macro. Deck order within a
(week, day) is topic document order, then ``slides_NNN_`` order within each
topic — the same order the build uses.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path

import click

from clm.core.course import Course
from clm.core.course_paths import resolve_course_paths
from clm.core.course_spec import CourseSpec, CourseSpecError, SubsectionSpec
from clm.core.utils.text_utils import Text

# Localized weekday labels for the language-neutral tokens in
# ``clm.core.course_spec.VALID_WEEKDAYS``. Presentation only — the spec and
# validator deal in the tokens; only rendering resolves them to words.
WEEKDAY_LABELS: dict[str, Text] = {
    "mon": Text(de="Montag", en="Monday"),
    "tue": Text(de="Dienstag", en="Tuesday"),
    "wed": Text(de="Mittwoch", en="Wednesday"),
    "thu": Text(de="Donnerstag", en="Thursday"),
    "fri": Text(de="Freitag", en="Friday"),
    "sat": Text(de="Samstag", en="Saturday"),
    "sun": Text(de="Sonntag", en="Sunday"),
}

# Localized Markdown table headers, per language.
_MD_HEADERS: dict[str, tuple[str, str, str]] = {
    "de": ("Tag", "Video (Foliensatz)", "Topic"),
    "en": ("Day", "Video (slides)", "Topic"),
}

_CSV_FIELDS = ["week", "week_title", "weekday", "video_title", "topic", "deck_file"]


@dataclass
class ScheduleDeck:
    """A single video (slide deck) within a scheduled day."""

    video_title: str
    topic_id: str
    deck_file: str  # source stem, e.g. "slides_010_introduction_ml_course_azav"


@dataclass
class ScheduleDay:
    """One day (``<subsection>``) of a week, with its decks in order."""

    weekday: str | None  # language-neutral token, or None for a thematic group
    label: str  # localized display label
    decks: list[ScheduleDeck] = field(default_factory=list)


@dataclass
class ScheduleWeek:
    """One week (``<section>``) with its scheduled days."""

    number: int
    title: str
    days: list[ScheduleDay] = field(default_factory=list)


def subsection_label(subsection: SubsectionSpec, language: str) -> str:
    """Resolve a subsection's display label for *language*.

    A ``<name>`` override wins; otherwise the weekday token is localized via
    :data:`WEEKDAY_LABELS`; otherwise (neither set) the label is empty.
    """
    if subsection.name is not None:
        return subsection.name[language]
    if subsection.weekday is not None:
        return WEEKDAY_LABELS[subsection.weekday][language]
    return ""


def _topic_decks(topic, language: str) -> list[ScheduleDeck]:
    """Return the decks of one resolved topic for *language*, in build order.

    Split ``.de.py`` / ``.en.py`` companions are filtered to the requested
    language so a split pair is listed once (and with the correct title),
    mirroring the build's per-language routing.
    """
    decks: list[ScheduleDeck] = []
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
        if not title:
            title = notebook.path.stem
        decks.append(
            ScheduleDeck(
                video_title=title,
                topic_id=topic.id,
                deck_file=notebook.path.stem,
            )
        )
    return decks


def build_schedule(course: Course, language: str) -> list[ScheduleWeek]:
    """Build the day-of-week schedule from a resolved *course*.

    Walks each section's retained ``<subsection>`` structure and maps every
    subsection topic to its resolved decks. Sections (weeks) are numbered
    1-based in declared order; only enabled subsections are listed.
    """
    weeks: list[ScheduleWeek] = []
    # course.sections aligns 1:1 with course.spec.sections (no section
    # selection is applied here, and the spec is parsed enabled-only).
    for number, (section, section_spec) in enumerate(
        zip(course.sections, course.spec.sections, strict=True), start=1
    ):
        topic_decks: dict[str, list[ScheduleDeck]] = {}
        for topic in section.topics:
            topic_decks[topic.id] = _topic_decks(topic, language)

        days: list[ScheduleDay] = []
        for subsection in section_spec.subsections:
            if not subsection.enabled:
                continue
            decks: list[ScheduleDeck] = []
            for topic_spec in subsection.topics:
                decks.extend(topic_decks.get(topic_spec.id, []))
            days.append(
                ScheduleDay(
                    weekday=subsection.weekday,
                    label=subsection_label(subsection, language),
                    decks=decks,
                )
            )

        weeks.append(ScheduleWeek(number=number, title=section.name[language], days=days))
    return weeks


def _md_cell(text: str) -> str:
    """Escape a value for a Markdown table cell."""
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").strip()


def render_markdown(course_title: str, weeks: list[ScheduleWeek], language: str) -> str:
    """Render the schedule as Markdown: one table per week."""
    day_h, video_h, topic_h = _MD_HEADERS[language]
    empty_cell = "—"
    lines: list[str] = [f"# {course_title}", ""]

    for week in weeks:
        lines.append(f"## {week.title}")
        lines.append("")
        if not week.days:
            note = "_Keine Tage geplant._" if language == "de" else "_No days scheduled._"
            lines.append(note)
            lines.append("")
            continue

        lines.append(f"| {day_h} | {video_h} | {topic_h} |")
        lines.append("|------|------|------|")
        for day in week.days:
            day_label = _md_cell(day.label)
            if not day.decks:
                lines.append(f"| {day_label} | {empty_cell} | {empty_cell} |")
                continue
            for index, deck in enumerate(day.decks):
                first = _md_cell(day.label) if index == 0 else ""
                lines.append(
                    f"| {first} | {_md_cell(deck.video_title)} | {_md_cell(deck.topic_id)} |"
                )
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def render_csv(weeks: list[ScheduleWeek]) -> str:
    """Render the schedule as CSV: one row per deck."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(_CSV_FIELDS)
    for week in weeks:
        for day in week.days:
            for deck in day.decks:
                writer.writerow(
                    [
                        week.number,
                        week.title,
                        day.weekday or "",
                        deck.video_title,
                        deck.topic_id,
                        deck.deck_file,
                    ]
                )
    return buffer.getvalue()


@click.command()
@click.argument(
    "spec-file",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
)
@click.option(
    "-L",
    "--language",
    "--lang",
    type=click.Choice(["de", "en"], case_sensitive=False),
    default="de",
    show_default=True,
    help="Language for deck titles and labels (titles come from header_de/header_en).",
)
@click.option(
    "-f",
    "--format",
    "output_format",
    type=click.Choice(["md", "csv"], case_sensitive=False),
    default="md",
    show_default=True,
    help="Output format.",
)
@click.option(
    "-o",
    "--output",
    "output_file",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write output to FILE instead of stdout.",
)
@click.option(
    "--data-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Course data directory (contains slides/). Default: inferred from spec location.",
)
def schedule(
    spec_file: Path,
    language: str,
    output_format: str,
    output_file: Path | None,
    data_dir: Path | None,
):
    """Export a day-of-week deck listing for certification.

    Reads the ``<subsection weekday="...">`` layer of a course spec and the
    decks discovered on disk, then emits one weekday listing per week.

    \b
    Examples:
        clm schedule course.xml                  # German Markdown to stdout
        clm schedule course.xml -L en            # English listing
        clm schedule course.xml -f csv           # CSV (one row per deck)
        clm schedule course.xml -o schedule.md   # Write to a file
    """
    language = language.lower()
    output_format = output_format.lower()

    try:
        spec = CourseSpec.from_file(spec_file)
    except CourseSpecError as e:
        raise click.ClickException(f"Failed to parse spec file: {e}") from None

    validation_errors = spec.validate()
    if validation_errors:
        error_msg = "\n".join(f"  - {e}" for e in validation_errors)
        raise click.ClickException(f"Spec validation failed:\n{error_msg}")

    course_root, _ = resolve_course_paths(spec_file, data_dir=data_dir)

    course = Course.from_spec(spec, course_root, output_root=None)

    weeks = build_schedule(course, language)

    if not any(week.days for week in weeks):
        click.echo(
            "Warning: this spec defines no <subsection> days; the schedule is "
            'empty. Add <subsection weekday="..."> groups to schedule decks. '
            "See 'clm info spec-files'.",
            err=True,
        )

    if output_format == "csv":
        content = render_csv(weeks)
    else:
        content = render_markdown(course.name[language], weeks, language)

    if output_file is not None:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(content, encoding="utf-8")
        click.echo(f"Written: {output_file}")
    else:
        click.echo(content, nl=False)
