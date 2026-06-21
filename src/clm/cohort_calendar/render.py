"""Render a projected cohort calendar to Markdown / CSV / iCalendar (#283).

Inputs are a :class:`~clm.cohort_calendar.projection.Projection` (the date-keyed
assignments) plus the course title and language. The Markdown and CSV views are
for the trainer; the ``.ics`` feed is the student-facing payload — a subscribable
calendar whose event UIDs are **stable** across re-exports, so a pushed
adjustment updates events in place instead of duplicating them.
"""

from __future__ import annotations

import csv
import datetime as dt
import io

from clm.cli.commands.export.schedule import WEEKDAY_LABELS, ScheduleDeck
from clm.cohort_calendar.projection import Assignment, Projection
from clm.core.course_spec import WEEKDAY_ORDER

_MD_HEADERS = {
    "de": ("Datum", "Inhalt"),
    "en": ("Date", "Content"),
}
_CSV_FIELDS = [
    "date",
    "end_date",
    "weekday",
    "kind",
    "label",
    "video_title",
    "topic",
    "deck_file",
]


def _weekday_token(day: dt.date) -> str:
    return WEEKDAY_ORDER[day.weekday()]


def _fmt_day(day: dt.date, language: str) -> str:
    """A localized 'Weekday YYYY-MM-DD' label for a single date."""
    return f"{WEEKDAY_LABELS[_weekday_token(day)][language]} {day.isoformat()}"


def _fmt_span(a: Assignment, language: str) -> str:
    if a.end_date != a.start_date:
        return f"{_fmt_day(a.start_date, language)} – {_fmt_day(a.end_date, language)}"
    return _fmt_day(a.start_date, language)


def _md_cell(text: str) -> str:
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").strip()


#: "+N more" suffix wording for the multi-deck event title, per language.
_MORE_SUFFIX = {"de": "+{n} weitere", "en": "+{n} more"}


def _content_text(a: Assignment) -> str:
    """Plain (un-escaped) content: deck titles + any activity labels, or the insert label."""
    if a.kind == "insert":
        return a.label or ""
    parts = [d.video_title for d in a.decks]
    parts.extend(a.activity_labels)
    return "; ".join(parts)


def _summary_text(a: Assignment, language: str) -> str:
    """A short event title: the first deck's title, plus a "+N more" count.

    A single-deck day reads as just that deck's title; a multi-deck day appends
    "(+N weitere)" / "(+N more)" so the day cell stays scannable. A deck-less day
    uses its insert label or, for an activity day (project work, exam, …), its
    activity label(s). The full numbered list lives in the body (:func:`_body_text`).
    """
    if a.kind == "insert" or not a.decks:
        return a.label or "; ".join(a.activity_labels)
    first = a.decks[0].video_title
    extra = len(a.decks) - 1
    if extra <= 0:
        return first
    suffix = _MORE_SUFFIX.get(language, _MORE_SUFFIX["en"]).format(n=extra)
    return f"{first} ({suffix})"


def _deck_line(deck: ScheduleDeck) -> str:
    """One body line for a deck: its section number (if known) then its title."""
    if deck.number_in_section:
        return f"{deck.number_in_section:02d}  {deck.video_title}"
    return deck.video_title


def _body_text(a: Assignment) -> str:
    """The event description: the section title, then the numbered slide list.

    The number is the deck's ``number_in_section`` — the same value students see
    in the output filenames — so a slide is easy to locate in a large course.
    Empty for inserts and for days with neither decks nor activities. An
    activity-only day (project work, exam, …) shows just the section title for
    context (the activity itself is already the event title).
    """
    if a.kind == "insert" or (not a.decks and not a.activity_labels):
        return ""
    lines: list[str] = []
    if a.section_title:
        lines.append(a.section_title)
    if a.decks:
        if a.section_title:
            lines.append("")
        lines.extend(_deck_line(d) for d in a.decks)
        lines.extend(a.activity_labels)  # extra non-deck items after the slides
    return "\n".join(lines)


def assignment_date_label(a: Assignment, language: str) -> str:
    """Public: the localized 'Weekday date' (or range) for one assignment."""
    return _fmt_span(a, language)


def assignment_content(a: Assignment) -> str:
    """Public: an assignment's display content (deck titles, or the insert label)."""
    return _content_text(a)


def assignment_summary(a: Assignment, language: str) -> str:
    """Public: the short calendar-event title (first deck + "+N more")."""
    return _summary_text(a, language)


def assignment_body(a: Assignment, language: str) -> str:
    """Public: the calendar-event description (section title + numbered slides)."""
    return _body_text(a)


def render_markdown(course_title: str, projection: Projection, language: str) -> str:
    """One date-ordered table: ``Date | Content`` (insert rows shown in italics)."""
    date_h, content_h = _MD_HEADERS[language]
    title_suffix = "Kalender" if language == "de" else "Calendar"
    lines = [f"# {course_title} — {title_suffix}", ""]
    if not projection.assignments:
        empty = "_Keine Termine._" if language == "de" else "_No dates scheduled._"
        lines.append(empty)
        return "\n".join(lines) + "\n"

    lines.append(f"| {date_h} | {content_h} |")
    lines.append("|------|------|")
    for a in projection.assignments:
        date_cell = _md_cell(_fmt_span(a, language))
        if a.kind == "insert":
            content = f"_{_md_cell(a.label or '')}_"
        else:
            content = _md_cell(_content_text(a))
        lines.append(f"| {date_cell} | {content} |")
    return "\n".join(lines).rstrip("\n") + "\n"


def render_csv(projection: Projection, language: str) -> str:
    """One row per deck (insert rows carry an empty deck triple), date-ordered."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(_CSV_FIELDS)
    for a in projection.assignments:
        weekday = _weekday_token(a.start_date)
        base = [a.start_date.isoformat(), a.end_date.isoformat(), weekday, a.kind]
        if a.kind == "insert" or not a.decks:
            label = a.label or "; ".join(a.activity_labels)
            writer.writerow([*base, label, "", "", ""])
            continue
        for deck in a.decks:
            writer.writerow([*base, "", deck.video_title, deck.topic_id, deck.deck_file])
    return buffer.getvalue()


def _ics_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def assignment_uid(a: Assignment, namespace: str) -> str:
    """A stable UID per assignment so re-exports update rather than duplicate.

    Seeded by the assignment's bucket refs (deck-file stems) for video/merged
    rows, or the date for an insert — both stable across re-projection of the
    same content, even as dates shift. Shared with the Google Calendar push
    (:mod:`clm.cohort_calendar.google_sync`) so the ``.ics`` feed and a pushed
    calendar agree on event identity.
    """
    if a.kind == "insert":
        key = f"insert-{a.start_date.isoformat()}"
    else:
        key = "+".join(a.bucket_refs) or a.start_date.isoformat()
    return f"{namespace}-{key}@clm.cohort-calendar"


def render_ics(
    course_title: str, projection: Projection, *, namespace: str, language: str = "de"
) -> str:
    """Render an all-day iCalendar feed (RFC 5545, CRLF line endings).

    Each assignment is one all-day VEVENT; a multi-date span uses the
    exclusive-end ``DTEND`` convention (end date + 1 day). ``DTSTAMP`` is fixed
    to each event's start date (no wall-clock read) to keep output deterministic.

    The ``SUMMARY`` is the short event title (first deck + "+N more"); the
    ``DESCRIPTION`` carries the section title and the numbered slide list.
    """
    cal_name = f"{course_title}" + (f" ({namespace})" if namespace else "")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//CLM//Cohort Viewing Calendar//EN",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{_ics_escape(cal_name)}",
    ]
    for a in projection.assignments:
        summary = _summary_text(a, language) or (a.label or "")
        dtend = a.end_date + dt.timedelta(days=1)  # DTEND is exclusive
        stamp = a.start_date.strftime("%Y%m%dT000000Z")
        lines += [
            "BEGIN:VEVENT",
            f"UID:{assignment_uid(a, namespace)}",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{a.start_date.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{dtend.strftime('%Y%m%d')}",
            f"SUMMARY:{_ics_escape(summary)}",
        ]
        body = _body_text(a)
        if body:
            lines.append(f"DESCRIPTION:{_ics_escape(body)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
