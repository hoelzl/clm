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

from clm.cli.commands.schedule import WEEKDAY_LABELS
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


def _content_text(a: Assignment) -> str:
    """Plain (un-escaped) content of an assignment: titles, or the insert label."""
    if a.kind == "insert":
        return a.label or ""
    return "; ".join(d.video_title for d in a.decks)


def assignment_date_label(a: Assignment, language: str) -> str:
    """Public: the localized 'Weekday date' (or range) for one assignment."""
    return _fmt_span(a, language)


def assignment_content(a: Assignment) -> str:
    """Public: an assignment's display content (deck titles, or the insert label)."""
    return _content_text(a)


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
            writer.writerow([*base, a.label or "", "", "", ""])
            continue
        for deck in a.decks:
            writer.writerow([*base, "", deck.video_title, deck.topic_id, deck.deck_file])
    return buffer.getvalue()


def _ics_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _ics_uid(a: Assignment, namespace: str) -> str:
    """A stable UID per assignment so re-exports update rather than duplicate.

    Seeded by the assignment's bucket refs (deck-file stems) for video/merged
    rows, or the date for an insert — both stable across re-projection of the
    same content, even as dates shift.
    """
    if a.kind == "insert":
        key = f"insert-{a.start_date.isoformat()}"
    else:
        key = "+".join(a.bucket_refs) or a.start_date.isoformat()
    return f"{namespace}-{key}@clm.cohort-calendar"


def render_ics(course_title: str, projection: Projection, *, namespace: str) -> str:
    """Render an all-day iCalendar feed (RFC 5545, CRLF line endings).

    Each assignment is one all-day VEVENT; a multi-date span uses the
    exclusive-end ``DTEND`` convention (end date + 1 day). ``DTSTAMP`` is fixed
    to each event's start date (no wall-clock read) to keep output deterministic.
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
        summary = _content_text(a) or (a.label or "")
        dtend = a.end_date + dt.timedelta(days=1)  # DTEND is exclusive
        stamp = a.start_date.strftime("%Y%m%dT000000Z")
        lines += [
            "BEGIN:VEVENT",
            f"UID:{_ics_uid(a, namespace)}",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{a.start_date.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{dtend.strftime('%Y%m%d')}",
            f"SUMMARY:{_ics_escape(summary)}",
        ]
        if a.kind != "insert" and a.decks:
            topics = ", ".join(d.topic_id for d in a.decks)
            lines.append(f"DESCRIPTION:{_ics_escape('Topics: ' + topics)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
