"""Tests for the cohort calendar renderers (issue #283, phase 4)."""

import datetime as dt

from clm.cli.commands.export.schedule import ScheduleDeck
from clm.cohort_calendar.projection import Assignment, Projection
from clm.cohort_calendar.render import render_csv, render_ics, render_markdown


def deck(title, topic, file):
    return ScheduleDeck(video_title=title, topic_id=topic, deck_file=file)


def sample() -> Projection:
    return Projection(
        assignments=(
            Assignment(
                dt.date(2026, 3, 2),
                dt.date(2026, 3, 2),
                (deck("Intro", "intro", "slides_010_intro"),),
                None,
                "video",
                ("slides_010_intro",),
            ),
            Assignment(
                dt.date(2026, 3, 3),
                dt.date(2026, 3, 3),
                (),
                "Review & Q&A",
                "insert",
                ("insert:2026-03-03",),
            ),
            Assignment(
                dt.date(2026, 3, 4),
                dt.date(2026, 3, 5),
                (deck("Spanned", "span", "slides_020_span"),),
                None,
                "video",
                ("slides_020_span",),
            ),
        ),
        diagnostics=(),
    )


class TestMarkdown:
    def test_header_and_rows(self):
        out = render_markdown("My Course", sample(), "en")
        assert out.startswith("# My Course — Calendar\n")
        assert "| Date | Content |" in out
        assert "| Monday 2026-03-02 | Intro |" in out
        # Insert rows render the label in italics, no decks.
        assert "| Tuesday 2026-03-03 | _Review & Q&A_ |" in out

    def test_span_renders_date_range(self):
        out = render_markdown("C", sample(), "en")
        assert "| Wednesday 2026-03-04 – Thursday 2026-03-05 | Spanned |" in out

    def test_german_headers(self):
        out = render_markdown("Kurs", sample(), "de")
        assert "# Kurs — Kalender" in out
        assert "| Datum | Inhalt |" in out
        assert "| Montag 2026-03-02 | Intro |" in out

    def test_empty(self):
        out = render_markdown("C", Projection((), ()), "en")
        assert "_No dates scheduled._" in out


class TestCsv:
    def test_header_and_video_row(self):
        out = render_csv(sample(), "en")
        lines = out.strip().splitlines()
        assert lines[0] == "date,end_date,weekday,kind,label,video_title,topic,deck_file"
        assert lines[1] == "2026-03-02,2026-03-02,mon,video,,Intro,intro,slides_010_intro"

    def test_insert_row_has_empty_deck_triple(self):
        out = render_csv(sample(), "en")
        assert "2026-03-03,2026-03-03,tue,insert,Review & Q&A,,," in out

    def test_span_end_date_column(self):
        out = render_csv(sample(), "en")
        assert "2026-03-04,2026-03-05,wed,video,,Spanned,span,slides_020_span" in out


class TestIcs:
    def test_calendar_envelope(self):
        out = render_ics("My Course", sample(), namespace="jan")
        assert out.startswith("BEGIN:VCALENDAR\r\n")
        assert out.rstrip().endswith("END:VCALENDAR")
        assert "X-WR-CALNAME:My Course (jan)" in out

    def test_all_day_event_with_stable_uid(self):
        out = render_ics("C", sample(), namespace="jan")
        assert "DTSTART;VALUE=DATE:20260302" in out
        assert "SUMMARY:Intro" in out
        assert "UID:jan-slides_010_intro@clm.cohort-calendar" in out

    def test_span_uses_exclusive_dtend(self):
        out = render_ics("C", sample(), namespace="jan")
        # End date 5 Mar -> exclusive DTEND 6 Mar.
        assert "DTSTART;VALUE=DATE:20260304" in out
        assert "DTEND;VALUE=DATE:20260306" in out

    def test_insert_event_uses_label_and_date_uid(self):
        out = render_ics("C", sample(), namespace="jan")
        assert "SUMMARY:Review & Q&A" in out
        assert "UID:jan-insert-2026-03-03@clm.cohort-calendar" in out

    def test_crlf_line_endings(self):
        out = render_ics("C", sample(), namespace="jan")
        assert "\r\n" in out
        # No lone LFs.
        assert "\n" not in out.replace("\r\n", "")
