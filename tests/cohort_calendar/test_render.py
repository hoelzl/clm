"""Tests for the cohort calendar renderers (issue #283, phase 4)."""

import datetime as dt

from clm.cli.commands.export.schedule import ScheduleDeck
from clm.cohort_calendar.projection import Assignment, Projection
from clm.cohort_calendar.render import (
    assignment_body,
    assignment_summary,
    render_csv,
    render_ics,
    render_markdown,
)


def deck(title, topic, file, number=0):
    return ScheduleDeck(video_title=title, topic_id=topic, deck_file=file, number_in_section=number)


def sample() -> Projection:
    return Projection(
        assignments=(
            Assignment(
                dt.date(2026, 3, 2),
                dt.date(2026, 3, 2),
                (deck("Intro", "intro", "slides_010_intro", 1),),
                None,
                "video",
                ("slides_010_intro",),
                section_title="Week 01: Foundations",
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
                (deck("Spanned", "span", "slides_020_span", 2),),
                None,
                "video",
                ("slides_020_span",),
                section_title="Week 02: More",
            ),
        ),
        diagnostics=(),
    )


def multi_deck() -> Assignment:
    """A single day carrying several decks (the readability pain point)."""
    return Assignment(
        dt.date(2026, 3, 6),
        dt.date(2026, 3, 6),
        (
            deck("Video - Funktionen", "py", "slides_040v_functions", 19),
            deck("Video - f-strings", "py", "slides_042v_fstrings", 20),
            deck("Video - Imports", "py", "slides_044v_imports", 21),
        ),
        None,
        "video",
        ("slides_040v_functions",),
        section_title="Woche 01: Python-Setup",
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

    def test_description_carries_section_and_numbered_slides(self):
        out = render_ics("C", sample(), namespace="jan")
        # Section title, blank line, then "NN  Title" — newlines escaped to \n.
        assert "DESCRIPTION:Week 01: Foundations\\n\\n01  Intro" in out

    def test_multi_deck_summary_and_body(self):
        proj = Projection((multi_deck(),), ())
        out = render_ics("C", proj, namespace="jan", language="de")
        assert "SUMMARY:Video - Funktionen (+2 weitere)" in out
        assert "DESCRIPTION:Woche 01: Python-Setup\\n\\n19  Video - Funktionen" in out
        assert "20  Video - f-strings" in out

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


class TestSummaryAndBody:
    def test_single_deck_summary_is_just_the_title(self):
        a = sample().assignments[0]
        assert assignment_summary(a, "de") == "Intro"

    def test_multi_deck_summary_counts_the_rest(self):
        assert assignment_summary(multi_deck(), "de") == "Video - Funktionen (+2 weitere)"
        assert assignment_summary(multi_deck(), "en") == "Video - Funktionen (+2 more)"

    def test_insert_summary_is_the_label(self):
        insert = sample().assignments[1]
        assert assignment_summary(insert, "de") == "Review & Q&A"

    def test_body_has_section_then_numbered_slides(self):
        assert assignment_body(multi_deck(), "de") == (
            "Woche 01: Python-Setup\n\n"
            "19  Video - Funktionen\n"
            "20  Video - f-strings\n"
            "21  Video - Imports"
        )

    def test_insert_has_no_body(self):
        insert = sample().assignments[1]
        assert assignment_body(insert, "de") == ""

    def test_unnumbered_deck_omits_the_number_prefix(self):
        a = Assignment(
            dt.date(2026, 3, 2),
            dt.date(2026, 3, 2),
            (deck("No Number", "x", "slides_x"),),  # number_in_section defaults to 0
            None,
            "video",
            ("slides_x",),
            section_title="Sec",
        )
        assert assignment_body(a, "de") == "Sec\n\nNo Number"
