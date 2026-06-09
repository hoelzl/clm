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

from clm.cli.commands._export_shared import (
    check_exclusive_output,
    disabled_topic_slides,
    language_option,
    notebook_in_language,
    output_options,
    resolve_disabled_mode,
    section_visible,
    selection_options,
    spec_argument,
    subsection_visible,
)
from clm.core.course import Course
from clm.core.course_paths import resolve_course_paths
from clm.core.course_spec import (
    CourseSpec,
    CourseSpecError,
    SectionSpec,
    SubsectionSpec,
)
from clm.core.section import Section
from clm.core.utils.text_utils import Text, sanitize_file_name

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
    disabled: bool = False


@dataclass
class ScheduleActivity:
    """A non-deck scheduled entry (project work, exam, …) within a day.

    Resolved from an ``<activity>`` element: it has display text but no deck on
    disk and is never built. Surfaced so a certification listing has no empty
    days. See :class:`clm.core.course_spec.ActivitySpec`.
    """

    text: str
    kind: str = ""
    disabled: bool = False


@dataclass
class ScheduleDay:
    """One day (``<subsection>``) of a week, with its decks/activities in order."""

    weekdays: list[str]  # language-neutral tokens; empty for a thematic group
    label: str  # localized display label
    decks: list[ScheduleDeck] = field(default_factory=list)
    activities: list[ScheduleActivity] = field(default_factory=list)
    disabled: bool = False

    @property
    def weekday(self) -> str | None:
        """The first weekday token, or ``None`` — convenience for single-day callers."""
        return self.weekdays[0] if self.weekdays else None


@dataclass
class ScheduleWeek:
    """One week (``<section>``) with its scheduled days."""

    number: int
    title: str
    days: list[ScheduleDay] = field(default_factory=list)
    disabled: bool = False


def subsection_label(subsection: SubsectionSpec, language: str) -> str:
    """Resolve a subsection's display label for *language*.

    A ``<name>`` override wins; otherwise the weekday token(s) are localized
    via :data:`WEEKDAY_LABELS` and joined with ", " (so a multi-day
    subsection reads "Monday, Tuesday, Wednesday"); otherwise (neither set)
    the label is empty.
    """
    if subsection.name is not None:
        return subsection.name[language]
    if subsection.weekdays:
        return ", ".join(WEEKDAY_LABELS[wd][language] for wd in subsection.weekdays)
    return ""


def _topic_decks(topic, language: str) -> list[ScheduleDeck]:
    """Return the decks of one resolved topic for *language*, in build order.

    Split ``.de.py`` / ``.en.py`` companions are filtered to the requested
    language so a split pair is listed once (and with the correct title),
    mirroring the build's per-language routing.
    """
    decks: list[ScheduleDeck] = []
    for notebook in topic.notebooks:
        if not notebook_in_language(notebook, language):
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


def _section_key(spec: SectionSpec) -> tuple:
    """A stable identity for matching an enabled section to its full-spec twin."""
    if spec.id is not None:
        return ("id", spec.id)
    return ("name", spec.name.de, spec.name.en)


def _decks_for_subsection(
    subsection: SubsectionSpec,
    topic_decks: dict[str, list[ScheduleDeck]],
    course: Course,
    language: str,
    *,
    parent_disabled: bool,
) -> list[ScheduleDeck]:
    """Resolve a subsection's decks in build order.

    Enabled topics of an enabled subsection in an enabled section read from
    *topic_decks* (the resolved course). Anything disabled (or otherwise not
    part of the built course) is read straight from the filesystem and tagged
    ``disabled``.
    """
    disabled = parent_disabled or not subsection.enabled
    decks: list[ScheduleDeck] = []
    for topic_spec in subsection.topics:
        # Feature: export="false" — built but hidden from the export listing.
        if not topic_spec.export:
            continue
        if not disabled and topic_spec.id in topic_decks:
            decks.extend(topic_decks[topic_spec.id])
        else:
            slides = disabled_topic_slides(course, topic_spec, language) or []
            for file_name, title in slides:
                decks.append(
                    ScheduleDeck(
                        video_title=title,
                        topic_id=topic_spec.id,
                        deck_file=Path(file_name).stem,
                        disabled=disabled,
                    )
                )
    return decks


def _activities_for_subsection(
    subsection: SubsectionSpec, language: str, *, disabled: bool
) -> list[ScheduleActivity]:
    """Resolve a subsection's ``<activity>`` entries for *language*, in order.

    Activities are non-deck schedule rows (project work, exams, …); they are
    never resolved against the filesystem, so they appear identically whether
    or not the surrounding section is built.
    """
    return [
        ScheduleActivity(
            text=activity.text[language],
            kind=activity.kind,
            disabled=disabled,
        )
        for activity in subsection.activities
    ]


def _days_for_section(
    section_spec: SectionSpec,
    topic_decks: dict[str, list[ScheduleDeck]],
    course: Course,
    language: str,
    *,
    include_optional: bool,
    include_disabled: bool,
    parent_disabled: bool,
) -> list[ScheduleDay]:
    """Build the :class:`ScheduleDay` list for one section's subsections."""
    days: list[ScheduleDay] = []
    for subsection in section_spec.subsections:
        if not subsection_visible(
            subsection, include_optional=include_optional, include_disabled=include_disabled
        ):
            continue
        day_disabled = parent_disabled or not subsection.enabled
        days.append(
            ScheduleDay(
                weekdays=list(subsection.weekdays),
                label=subsection_label(subsection, language),
                decks=_decks_for_subsection(
                    subsection, topic_decks, course, language, parent_disabled=parent_disabled
                ),
                activities=_activities_for_subsection(subsection, language, disabled=day_disabled),
                disabled=day_disabled,
            )
        )
    return days


def build_schedule(
    course: Course,
    language: str,
    *,
    include_optional: bool = False,
    include_disabled: bool = False,
    full_sections: list[SectionSpec] | None = None,
) -> list[ScheduleWeek]:
    """Build the day-of-week schedule from a resolved *course*.

    Walks each section's retained ``<subsection>`` structure and maps every
    subsection topic to its resolved decks. Sections (weeks) are numbered
    1-based in declared order; only enabled subsections are listed.

    Optional modules (``optional="true"`` on a ``<section>`` or
    ``<subsection>``) are omitted unless ``include_optional`` is set. Skipped
    optional weeks keep their declared number (so an excluded optional Week 3
    leaves Weeks 1, 2, 4, … rather than renumbering).

    When ``include_disabled`` is set and *full_sections* (the ``keep_disabled``
    parse) is supplied, disabled subsections and disabled whole sections are
    surfaced too, with their decks read from the filesystem and tagged
    ``disabled``. In that mode weeks are numbered by their declared position in
    *full_sections* (so a disabled Week 2 is shown as Week 2).
    """
    if include_disabled and full_sections is not None:
        return _build_schedule_with_disabled(course, language, full_sections, include_optional)

    weeks: list[ScheduleWeek] = []
    # course.sections aligns 1:1 with course.spec.sections (no section
    # selection is applied here, and the spec is parsed enabled-only).
    for number, (section, section_spec) in enumerate(
        zip(course.sections, course.spec.sections, strict=True), start=1
    ):
        if not section_visible(section_spec, include_optional=include_optional):
            continue
        topic_decks = {topic.id: _topic_decks(topic, language) for topic in section.topics}
        days = _days_for_section(
            section_spec,
            topic_decks,
            course,
            language,
            include_optional=include_optional,
            include_disabled=False,
            parent_disabled=False,
        )
        weeks.append(ScheduleWeek(number=number, title=section.name[language], days=days))
    return weeks


def _build_schedule_with_disabled(
    course: Course,
    language: str,
    full_sections: list[SectionSpec],
    include_optional: bool,
) -> list[ScheduleWeek]:
    """Build the schedule from the full (``keep_disabled``) section list.

    Numbers weeks by declared position so disabled weeks keep their place, and
    resolves enabled sections' decks from the built course while reading
    disabled content from the filesystem.
    """
    built_by_key: dict[tuple, Section] = {}
    for section, section_spec in zip(course.sections, course.spec.sections, strict=True):
        built_by_key[_section_key(section_spec)] = section

    weeks: list[ScheduleWeek] = []
    for number, full_spec in enumerate(full_sections, start=1):
        if full_spec.optional and not include_optional:
            continue
        built = built_by_key.get(_section_key(full_spec))
        section_disabled = not full_spec.enabled
        topic_decks: dict[str, list[ScheduleDeck]] = {}
        if built is not None and not section_disabled:
            topic_decks = {topic.id: _topic_decks(topic, language) for topic in built.topics}
        days = _days_for_section(
            full_spec,
            topic_decks,
            course,
            language,
            include_optional=include_optional,
            include_disabled=True,
            parent_disabled=section_disabled,
        )
        weeks.append(
            ScheduleWeek(
                number=number,
                title=full_spec.name[language],
                days=days,
                disabled=section_disabled,
            )
        )
    return weeks


def _md_cell(text: str) -> str:
    """Escape a value for a Markdown table cell."""
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").strip()


def render_markdown(
    course_title: str,
    weeks: list[ScheduleWeek],
    language: str,
    *,
    no_topic: bool = False,
    mark_disabled: bool = True,
) -> str:
    """Render the schedule as Markdown: one table per week.

    With ``no_topic`` the Topic column is dropped, leaving just Day and
    Video (slides) — the columns a certification authority needs.

    With ``mark_disabled`` False (``--include-disabled=merge``) the ``(disabled)``
    tags are suppressed so disabled weeks/days read like enabled ones; the weeks
    already appear in declared order. The schedule data (and the CSV ``disabled``
    column) keep their truthful flags regardless.
    """
    day_h, video_h, topic_h = _MD_HEADERS[language]
    empty_cell = "—"
    disabled_tag = " (disabled)"
    lines: list[str] = [f"# {course_title}", ""]

    for week in weeks:
        week_title = week.title + (disabled_tag if (week.disabled and mark_disabled) else "")
        lines.append(f"## {week_title}")
        lines.append("")
        if not week.days:
            note = "_Keine Tage geplant._" if language == "de" else "_No days scheduled._"
            lines.append(note)
            lines.append("")
            continue

        if no_topic:
            lines.append(f"| {day_h} | {video_h} |")
            lines.append("|------|------|")
        else:
            lines.append(f"| {day_h} | {video_h} | {topic_h} |")
            lines.append("|------|------|------|")
        for day in week.days:
            # Mark a disabled subsection inside an otherwise-enabled week; if the
            # whole week is disabled the heading already carries the tag. Merge
            # mode (mark_disabled False) suppresses the tag entirely.
            label_text = day.label + (
                disabled_tag if (day.disabled and not week.disabled and mark_disabled) else ""
            )
            day_label = _md_cell(label_text)
            # One row per deck, then one per activity (no-deck entries render the
            # day label like a deck but leave the Topic column empty).
            rows: list[tuple[str, str]] = [(deck.video_title, deck.topic_id) for deck in day.decks]
            rows += [(activity.text, "") for activity in day.activities]
            if not rows:
                if no_topic:
                    lines.append(f"| {day_label} | {empty_cell} |")
                else:
                    lines.append(f"| {day_label} | {empty_cell} | {empty_cell} |")
                continue
            for index, (video_title, topic_id) in enumerate(rows):
                first = day_label if index == 0 else ""
                video = _md_cell(video_title)
                if no_topic:
                    lines.append(f"| {first} | {video} |")
                else:
                    topic_cell = _md_cell(topic_id) if topic_id else empty_cell
                    lines.append(f"| {first} | {video} | {topic_cell} |")
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def render_csv(
    weeks: list[ScheduleWeek], *, no_topic: bool = False, include_disabled: bool = False
) -> str:
    """Render the schedule as CSV: one row per deck.

    A multi-day subsection joins its weekday tokens with "," in the
    ``weekday`` field (the cell is quoted by the CSV writer). With
    ``no_topic`` the ``topic`` column is dropped. With ``include_disabled`` a
    trailing ``disabled`` column ("true"/"") is appended so disabled rows are
    distinguishable; the default CSV schema is unchanged.
    """
    fields = [f for f in _CSV_FIELDS if not (no_topic and f == "topic")]
    if include_disabled:
        fields = [*fields, "disabled"]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(fields)
    for week in weeks:
        for day in week.days:
            weekday_cell = ",".join(day.weekdays)
            for deck in day.decks:
                row: list = [
                    week.number,
                    week.title,
                    weekday_cell,
                    deck.video_title,
                ]
                if not no_topic:
                    row.append(deck.topic_id)
                row.append(deck.deck_file)
                if include_disabled:
                    row.append("true" if deck.disabled else "")
                writer.writerow(row)
            for activity in day.activities:
                # A non-deck row: video_title carries the label; topic and
                # deck_file are empty (there is no topic id or source stem).
                row = [week.number, week.title, weekday_cell, activity.text]
                if not no_topic:
                    row.append("")
                row.append("")
                if include_disabled:
                    row.append("true" if activity.disabled else "")
                writer.writerow(row)
    return buffer.getvalue()


@click.command()
@spec_argument
@language_option(
    default="de",
    aliases=("--lang",),
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
@output_options
@click.option(
    "--no-topic",
    "no_topic",
    is_flag=True,
    help="Omit the Topic column, leaving just day and video/slides "
    "(the columns a certification authority needs).",
)
@selection_options
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
    output_dir: Path | None,
    no_topic: bool,
    include_optional: bool,
    disabled_mode: str | None,
    data_dir: Path | None,
):
    """Export a day-of-week deck listing for certification.

    Reads the ``<subsection weekday="...">`` layer of a course spec and the
    decks discovered on disk, then emits one weekday listing per week.

    \b
    Examples:
        clm export schedule course.xml                  # German Markdown to stdout
        clm export schedule course.xml -L en            # English listing
        clm export schedule course.xml -f csv           # CSV (one row per deck)
        clm export schedule course.xml --no-topic       # Day + video/slides only
        clm export schedule course.xml --include-optional   # Add optional modules
        clm export schedule course.xml --include-disabled        # Disabled days, tagged
        clm export schedule course.xml --include-disabled=merge  # Disabled days, in flow
        clm export schedule course.xml -o schedule.md   # Write to a file
        clm export schedule course.xml -d ./docs        # Write into a directory
    """
    language = language.lower()
    output_format = output_format.lower()
    check_exclusive_output(output_file, output_dir)

    include_disabled, merge_disabled = resolve_disabled_mode(disabled_mode)

    try:
        spec = CourseSpec.from_file(spec_file)
    except CourseSpecError as e:
        raise click.ClickException(f"Failed to parse spec file: {e}") from None

    full_sections: list[SectionSpec] | None = None
    if include_disabled:
        try:
            full_spec = CourseSpec.from_file(spec_file, keep_disabled=True)
        except CourseSpecError as e:
            raise click.ClickException(f"Failed to parse spec file: {e}") from None
        full_sections = full_spec.sections

    validation_errors = spec.validate()
    if validation_errors:
        error_msg = "\n".join(f"  - {e}" for e in validation_errors)
        raise click.ClickException(f"Spec validation failed:\n{error_msg}")

    course_root, _ = resolve_course_paths(spec_file, data_dir=data_dir)

    course = Course.from_spec(spec, course_root, output_root=None)

    weeks = build_schedule(
        course,
        language,
        include_optional=include_optional,
        include_disabled=include_disabled,
        full_sections=full_sections,
    )

    if not any(week.days for week in weeks):
        click.echo(
            "Warning: this spec defines no <subsection> days; the schedule is "
            'empty. Add <subsection weekday="..."> groups to schedule decks. '
            "See 'clm info spec-files'.",
            err=True,
        )

    if output_format == "csv":
        content = render_csv(weeks, no_topic=no_topic, include_disabled=include_disabled)
    else:
        content = render_markdown(
            course.name[language],
            weeks,
            language,
            no_topic=no_topic,
            mark_disabled=not merge_disabled,
        )

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        ext = "csv" if output_format == "csv" else "md"
        title = sanitize_file_name(course.name[language])
        file_path = output_dir / f"{title}-schedule-{language}.{ext}"
        file_path.write_text(content, encoding="utf-8")
        click.echo(f"Written: {file_path}")
    elif output_file is not None:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(content, encoding="utf-8")
        click.echo(f"Written: {output_file}")
    else:
        click.echo(content, nl=False)
