"""Tests for the ``clm schedule`` command (issue #261)."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.schedule import (
    WEEKDAY_LABELS,
    Bucket,
    ScheduleDay,
    ScheduleDeck,
    ScheduleWeek,
    build_buckets,
    build_schedule,
    render_csv,
    render_markdown,
    subsection_label,
)
from clm.cli.main import cli
from clm.core.course import Course
from clm.core.course_spec import CourseSpec, SubsectionSpec
from clm.core.utils.text_utils import Text

SPEC_PATH = Path("tests/test-data/course-specs/subsection-spec.xml")


@pytest.fixture
def course() -> Course:
    spec = CourseSpec.from_file(SPEC_PATH)
    return Course.from_spec(spec, SPEC_PATH.parents[1], output_root=None)


class TestSubsectionLabel:
    def test_name_override_wins(self):
        sub = SubsectionSpec(weekdays=("tue",), name=Text(de="Recht", en="Law"))
        assert subsection_label(sub, "de") == "Recht"
        assert subsection_label(sub, "en") == "Law"

    def test_weekday_localized_when_no_name(self):
        sub = SubsectionSpec(weekdays=("mon",))
        assert subsection_label(sub, "de") == "Montag"
        assert subsection_label(sub, "en") == "Monday"

    def test_multiple_weekdays_join_localized_labels(self):
        sub = SubsectionSpec(weekdays=("mon", "tue", "wed"))
        assert subsection_label(sub, "en") == "Monday, Tuesday, Wednesday"
        assert subsection_label(sub, "de") == "Montag, Dienstag, Mittwoch"

    def test_empty_when_neither(self):
        sub = SubsectionSpec()
        assert subsection_label(sub, "de") == ""

    def test_all_weekdays_have_labels(self):
        for token in ("mon", "tue", "wed", "thu", "fri", "sat", "sun"):
            assert WEEKDAY_LABELS[token]["de"]
            assert WEEKDAY_LABELS[token]["en"]


class TestBuildSchedule:
    def test_weeks_and_days_structure(self, course):
        weeks = build_schedule(course, "en")
        assert [w.number for w in weeks] == [1, 2]
        assert weeks[0].title == "Week 1"
        assert [d.weekday for d in weeks[0].days] == ["mon", "tue"]
        assert [d.weekday for d in weeks[1].days] == ["wed"]

    def test_deck_order_within_day(self, course):
        weeks = build_schedule(course, "en")
        monday = weeks[0].days[0]
        assert [d.topic_id for d in monday.decks] == [
            "some_topic_from_test_1",
            "a_topic_from_test_2",
        ]
        assert monday.decks[0].video_title == "Some Topic from Test 1"

    def test_label_uses_name_override(self, course):
        weeks = build_schedule(course, "en")
        tuesday = weeks[0].days[1]
        assert tuesday.label == "Tuesday — Law"

    def test_language_selects_titles(self, course):
        weeks_de = build_schedule(course, "de")
        monday = weeks_de[0].days[0]
        assert monday.decks[0].video_title == "Folien von Test 1"
        assert monday.label == "Montag"

    def test_bare_topic_not_listed_as_a_day(self, course):
        # Week 2 has a bare topic (another_topic_from_test_1) + a wed subsection;
        # the schedule lists only the day, not the bare topic.
        weeks = build_schedule(course, "en")
        week2 = weeks[1]
        all_topics = [deck.topic_id for day in week2.days for deck in day.decks]
        assert all_topics == ["simple_notebook"]


class TestBuildBuckets:
    """The content sequence flattener for the cohort calendar (issue #283)."""

    def _week(self, number, days):
        return ScheduleWeek(number=number, title=f"Week {number}", days=days)

    def test_flattens_days_in_week_then_document_order(self):
        weeks = [
            self._week(
                1,
                [
                    ScheduleDay(weekdays=["mon"], label="Monday", decks=[]),
                    ScheduleDay(weekdays=["tue"], label="Tuesday", decks=[]),
                ],
            ),
            self._week(2, [ScheduleDay(weekdays=["wed"], label="Wednesday", decks=[])]),
        ]
        buckets = build_buckets(weeks)
        assert [(b.week, b.weekday_label) for b in buckets] == [
            (1, "Monday"),
            (1, "Tuesday"),
            (2, "Wednesday"),
        ]

    def test_span_is_weekday_count(self):
        weeks = [
            self._week(
                1,
                [
                    ScheduleDay(weekdays=["mon"], label="Mon", decks=[]),
                    ScheduleDay(weekdays=["mon", "tue"], label="Mon, Tue", decks=[]),
                ],
            )
        ]
        spans = [b.span for b in build_buckets(weeks)]
        assert spans == [1, 2]

    def test_span_defaults_to_one_for_thematic_group(self):
        # A subsection with no weekday (thematic <name>-only group) still
        # occupies a single teaching date.
        weeks = [self._week(1, [ScheduleDay(weekdays=[], label="Intro", decks=[])])]
        assert build_buckets(weeks)[0].span == 1

    def test_decks_preserved_in_order(self):
        decks = [
            ScheduleDeck("Intro", "intro", "slides_010_intro"),
            ScheduleDeck("More", "more", "slides_020_more"),
        ]
        weeks = [self._week(1, [ScheduleDay(weekdays=["mon"], label="Mon", decks=decks)])]
        bucket = build_buckets(weeks)[0]
        assert bucket.decks == decks

    def test_empty_day_still_yields_a_bucket(self):
        weeks = [self._week(1, [ScheduleDay(weekdays=["mon"], label="Mon", decks=[])])]
        assert len(build_buckets(weeks)) == 1

    def test_ref_ids_includes_topic_and_deck_file(self):
        bucket = Bucket(
            decks=[ScheduleDeck("Intro", "intro", "slides_010_intro")],
            span=1,
            week=1,
            weekday_label="Mon",
        )
        assert bucket.ref_ids == {"intro", "slides_010_intro"}

    def test_from_real_schedule(self, course):
        # The fixture spec: Week 1 (mon: two decks, tue: Law), Week 2 (wed).
        buckets = build_buckets(build_schedule(course, "en"))
        assert [(b.week, b.span) for b in buckets] == [(1, 1), (1, 1), (2, 1)]
        assert [d.topic_id for d in buckets[0].decks] == [
            "some_topic_from_test_1",
            "a_topic_from_test_2",
        ]
        assert "some_topic_from_test_1" in buckets[0].ref_ids


class TestRenderMarkdown:
    def test_markdown_table_structure(self):
        weeks = [
            ScheduleWeek(
                number=1,
                title="Week 1",
                days=[
                    ScheduleDay(
                        weekdays=["mon"],
                        label="Monday",
                        decks=[
                            ScheduleDeck("Intro", "intro", "slides_010_intro"),
                            ScheduleDeck("More", "more", "slides_020_more"),
                        ],
                    ),
                ],
            )
        ]
        out = render_markdown("My Course", weeks, "en")
        assert out.startswith("# My Course\n")
        assert "## Week 1" in out
        assert "| Day | Video (slides) | Topic |" in out
        # Label only on the first deck row of the day.
        assert "| Monday | Intro | intro |" in out
        assert "|  | More | more |" in out

    def test_markdown_german_headers(self):
        weeks = [ScheduleWeek(number=1, title="Woche 1", days=[])]
        out = render_markdown("Kurs", weeks, "de")
        assert "_Keine Tage geplant._" in out

    def test_markdown_empty_day_renders_placeholder(self):
        weeks = [
            ScheduleWeek(
                number=1,
                title="W1",
                days=[ScheduleDay(weekdays=["mon"], label="Monday", decks=[])],
            )
        ]
        out = render_markdown("C", weeks, "en")
        assert "| Monday | — | — |" in out

    def test_markdown_escapes_pipe_in_title(self):
        weeks = [
            ScheduleWeek(
                number=1,
                title="W1",
                days=[
                    ScheduleDay(
                        weekdays=["mon"],
                        label="Monday",
                        decks=[ScheduleDeck("A | B", "t", "f")],
                    )
                ],
            )
        ]
        out = render_markdown("C", weeks, "en")
        assert r"A \| B" in out

    def test_no_topic_drops_topic_column(self):
        weeks = [
            ScheduleWeek(
                number=1,
                title="Week 1",
                days=[
                    ScheduleDay(
                        weekdays=["mon"],
                        label="Monday",
                        decks=[ScheduleDeck("Intro", "intro", "slides_010_intro")],
                    ),
                    ScheduleDay(weekdays=["tue"], label="Tuesday", decks=[]),
                ],
            )
        ]
        out = render_markdown("My Course", weeks, "en", no_topic=True)
        # Two-column header, no Topic.
        assert "| Day | Video (slides) |" in out
        assert "Topic" not in out
        assert "| Monday | Intro |" in out
        # Empty day still renders a single placeholder column.
        assert "| Tuesday | — |" in out


class TestRenderCsv:
    def test_csv_header_and_rows(self):
        weeks = [
            ScheduleWeek(
                number=1,
                title="Week 1",
                days=[
                    ScheduleDay(
                        weekdays=["mon"],
                        label="Monday",
                        decks=[ScheduleDeck("Intro", "intro", "slides_010_intro")],
                    )
                ],
            )
        ]
        out = render_csv(weeks)
        lines = out.strip().splitlines()
        assert lines[0] == "week,week_title,weekday,video_title,topic,deck_file"
        assert lines[1] == "1,Week 1,mon,Intro,intro,slides_010_intro"

    def test_csv_quotes_commas(self):
        weeks = [
            ScheduleWeek(
                number=1,
                title="Einführung, LLMs",
                days=[
                    ScheduleDay(
                        weekdays=["mon"],
                        label="Montag",
                        decks=[ScheduleDeck("A, B", "t", "f")],
                    )
                ],
            )
        ]
        out = render_csv(weeks)
        assert '"Einführung, LLMs"' in out
        assert '"A, B"' in out

    def test_no_topic_drops_topic_field(self):
        weeks = [
            ScheduleWeek(
                number=1,
                title="Week 1",
                days=[
                    ScheduleDay(
                        weekdays=["mon"],
                        label="Monday",
                        decks=[ScheduleDeck("Intro", "intro", "slides_010_intro")],
                    )
                ],
            )
        ]
        out = render_csv(weeks, no_topic=True)
        lines = out.strip().splitlines()
        assert lines[0] == "week,week_title,weekday,video_title,deck_file"
        assert lines[1] == "1,Week 1,mon,Intro,slides_010_intro"

    def test_multi_weekday_joined_in_cell(self):
        weeks = [
            ScheduleWeek(
                number=1,
                title="Week 1",
                days=[
                    ScheduleDay(
                        weekdays=["mon", "tue"],
                        label="Monday, Tuesday",
                        decks=[ScheduleDeck("Intro", "intro", "slides_010_intro")],
                    )
                ],
            )
        ]
        out = render_csv(weeks)
        # The comma-joined token set is quoted by the CSV writer.
        assert '"mon,tue"' in out


class TestScheduleCli:
    def test_markdown_default_german(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["export", "schedule", str(SPEC_PATH)])
        assert result.exit_code == 0, result.output
        assert "# Mein Kurs" in result.output
        assert "## Woche 1" in result.output
        assert "| Montag | Folien von Test 1 | some_topic_from_test_1 |" in result.output
        assert "| Dienstag — Recht |" in result.output

    def test_markdown_english(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["export", "schedule", str(SPEC_PATH), "-L", "en"])
        assert result.exit_code == 0, result.output
        assert "# My Course" in result.output
        assert "| Monday | Some Topic from Test 1 | some_topic_from_test_1 |" in result.output

    def test_csv_format(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["export", "schedule", str(SPEC_PATH), "-f", "csv", "-L", "en"])
        assert result.exit_code == 0, result.output
        assert "week,week_title,weekday,video_title,topic,deck_file" in result.output
        assert "1,Week 1,mon,Some Topic from Test 1,some_topic_from_test_1," in result.output
        assert "2,Week 2,wed,Simple Notebook,simple_notebook," in result.output

    def test_output_to_file(self, tmp_path):
        out_file = tmp_path / "schedule.md"
        runner = CliRunner()
        result = runner.invoke(cli, ["export", "schedule", str(SPEC_PATH), "-o", str(out_file)])
        assert result.exit_code == 0, result.output
        assert out_file.exists()
        assert "## Woche 1" in out_file.read_text(encoding="utf-8")

    def test_spec_without_subsections_notes_empty(self):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["export", "schedule", "tests/test-data/course-specs/test-spec-1.xml", "-L", "en"]
        )
        assert result.exit_code == 0, result.output
        assert "No days scheduled." in result.output

    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["export", "schedule", "--help"])
        assert result.exit_code == 0
        assert "day-of-week" in result.output.lower() or "weekday" in result.output.lower()

    def test_appears_in_main_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "export" in result.output
        sub = runner.invoke(cli, ["export", "--help"])
        assert sub.exit_code == 0
        assert "schedule" in sub.output

    def test_no_topic_flag_markdown(self):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["export", "schedule", str(SPEC_PATH), "-L", "en", "--no-topic"]
        )
        assert result.exit_code == 0, result.output
        assert "| Day | Video (slides) |" in result.output
        # The Topic *column* (header and topic-id cells) is gone; deck titles
        # that merely contain the word "Topic" are unaffected.
        assert "| Topic |" not in result.output
        assert "some_topic_from_test_1" not in result.output

    def test_no_topic_flag_csv(self):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["export", "schedule", str(SPEC_PATH), "-f", "csv", "-L", "en", "--no-topic"]
        )
        assert result.exit_code == 0, result.output
        assert "week,week_title,weekday,video_title,deck_file" in result.output
        assert "topic" not in result.output.splitlines()[0]


OPTIONAL_SPEC_PATH = Path("tests/test-data/course-specs/subsection-optional-spec.xml")


class TestScheduleOptional:
    def test_optional_excluded_by_default(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["export", "schedule", str(OPTIONAL_SPEC_PATH), "-L", "en"])
        assert result.exit_code == 0, result.output
        # The multi-day subsection localizes to a joined label.
        assert "| Monday, Tuesday |" in result.output
        # Optional Week 2 (whole section) and the optional Wednesday subsection
        # are both omitted without the flag.
        assert "## Week 2" not in result.output
        assert "Wednesday" not in result.output

    def test_include_optional_adds_modules(self):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["export", "schedule", str(OPTIONAL_SPEC_PATH), "-L", "en", "--include-optional"]
        )
        assert result.exit_code == 0, result.output
        assert "## Week 2" in result.output
        assert "| Wednesday |" in result.output
        assert "| Thursday |" in result.output

    def test_excluded_optional_section_keeps_week_numbering(self):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["export", "schedule", str(OPTIONAL_SPEC_PATH), "-f", "csv", "-L", "en"]
        )
        assert result.exit_code == 0, result.output
        # Week 1 is present; the optional Week 2 leaves no week-2 rows.
        assert ",mon,tue," not in result.output  # weekday cell is the joined token set
        assert '"mon,tue"' in result.output
        assert "2,Week 2," not in result.output


DISABLED_SPEC_PATH = Path("tests/test-data/course-specs/subsection-disabled-spec.xml")


class TestScheduleIncludeDisabled:
    def test_disabled_subsection_hidden_by_default(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["export", "schedule", str(DISABLED_SPEC_PATH), "-L", "en"])
        assert result.exit_code == 0, result.output
        assert "| Monday | Some Topic from Test 1 |" in result.output
        assert "(disabled)" not in result.output
        assert "Tuesday" not in result.output

    def test_include_disabled_surfaces_subsection_from_filesystem(self):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["export", "schedule", str(DISABLED_SPEC_PATH), "-L", "en", "--include-disabled"]
        )
        assert result.exit_code == 0, result.output
        # The disabled Tuesday subsection's deck is read from disk and tagged.
        assert "| Tuesday (disabled) |" in result.output

    def test_include_disabled_csv_adds_disabled_column(self):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "export",
                "schedule",
                str(DISABLED_SPEC_PATH),
                "-f",
                "csv",
                "-L",
                "en",
                "--include-disabled",
            ],
        )
        assert result.exit_code == 0, result.output
        header = result.output.splitlines()[0]
        assert header.endswith(",disabled")
        # The enabled Monday deck has an empty disabled cell; the disabled
        # Tuesday deck is flagged "true".
        assert ",mon," in result.output
        assert result.output.rstrip().endswith(",true")


class TestScheduleOutputDir:
    def test_output_dir_writes_named_file(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["export", "schedule", str(SPEC_PATH), "-L", "en", "-d", str(tmp_path)]
        )
        assert result.exit_code == 0, result.output
        written = list(tmp_path.glob("*.md"))
        assert len(written) == 1
        assert written[0].name.endswith("-schedule-en.md")
        assert "# " in written[0].read_text(encoding="utf-8")

    def test_output_and_output_dir_mutually_exclusive(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "export",
                "schedule",
                str(SPEC_PATH),
                "-o",
                str(tmp_path / "x.md"),
                "-d",
                str(tmp_path),
            ],
        )
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output.lower()
