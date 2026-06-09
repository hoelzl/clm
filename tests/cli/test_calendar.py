"""Tests for the ``clm export calendar`` command (issue #283, phase 4)."""

from pathlib import Path

from click.testing import CliRunner

from clm.cli.main import cli

SPEC_PATH = Path("tests/test-data/course-specs/subsection-spec.xml")

# The fixture spec: Week 1 mon (two decks) + tue (Law), Week 2 wed. Starting on
# Monday 2 Mar 2026 with the derived mon/tue/wed pattern lands them on the 2nd,
# 3rd, 4th.
CAL_OK = "start = 2026-03-02\n"
CAL_TOO_SHORT = "start = 2026-03-02\nend = 2026-03-02\n"


def _write(tmp_path, text) -> Path:
    p = tmp_path / "jan.calendar.toml"
    p.write_text(text, encoding="utf-8")
    return p


class TestExportCalendar:
    def test_markdown_default(self, tmp_path):
        cal = _write(tmp_path, CAL_OK)
        runner = CliRunner()
        result = runner.invoke(
            cli, ["export", "calendar", str(SPEC_PATH), "--calendar", str(cal), "-L", "en"]
        )
        assert result.exit_code == 0, result.output
        assert "— Calendar" in result.output
        assert "Monday 2026-03-02" in result.output
        assert "Tuesday 2026-03-03" in result.output
        assert "Wednesday 2026-03-04" in result.output
        assert "Some Topic from Test 1" in result.output
        assert "Simple Notebook" in result.output

    def test_csv(self, tmp_path):
        cal = _write(tmp_path, CAL_OK)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["export", "calendar", str(SPEC_PATH), "--calendar", str(cal), "-L", "en", "-f", "csv"],
        )
        assert result.exit_code == 0, result.output
        assert "date,end_date,weekday,kind,label,video_title,topic,deck_file" in result.output
        assert (
            "2026-03-02,2026-03-02,mon,video,,Some Topic from Test 1,some_topic_from_test_1,"
            in (result.output)
        )

    def test_ics(self, tmp_path):
        cal = _write(tmp_path, CAL_OK)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["export", "calendar", str(SPEC_PATH), "--calendar", str(cal), "-L", "en", "-f", "ics"],
        )
        assert result.exit_code == 0, result.output
        assert "BEGIN:VCALENDAR" in result.output
        assert "DTSTART;VALUE=DATE:20260302" in result.output
        assert "SUMMARY:Some Topic from Test 1" in result.output

    def test_output_to_file(self, tmp_path):
        cal = _write(tmp_path, CAL_OK)
        out = tmp_path / "out.ics"
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "export",
                "calendar",
                str(SPEC_PATH),
                "--calendar",
                str(cal),
                "-f",
                "ics",
                "-o",
                str(out),
            ],
        )
        assert result.exit_code == 0, result.output
        assert out.exists()
        assert "BEGIN:VCALENDAR" in out.read_text(encoding="utf-8")

    def test_projection_errors_exit_nonzero(self, tmp_path):
        cal = _write(tmp_path, CAL_TOO_SHORT)
        runner = CliRunner()
        result = runner.invoke(
            cli, ["export", "calendar", str(SPEC_PATH), "--calendar", str(cal), "-L", "en"]
        )
        assert result.exit_code != 0
        assert "merge" in result.output  # quantified deficit reported to stderr

    def test_channel_without_release_channels_errors(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["export", "calendar", str(SPEC_PATH), "--channel", "jan"])
        assert result.exit_code != 0
        assert "release-channels" in result.output

    def test_requires_channel_or_calendar(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["export", "calendar", str(SPEC_PATH)])
        assert result.exit_code != 0
        assert "--channel" in result.output or "--calendar" in result.output

    def test_registered_in_export_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["export", "--help"])
        assert result.exit_code == 0
        assert "calendar" in result.output


class TestCalendarGroup:
    def test_check_ok(self, tmp_path):
        cal = _write(tmp_path, CAL_OK)
        runner = CliRunner()
        result = runner.invoke(cli, ["calendar", "check", str(SPEC_PATH), "--calendar", str(cal)])
        assert result.exit_code == 0, result.output
        assert "Calendar OK" in result.output

    def test_check_reports_errors_and_exits_nonzero(self, tmp_path):
        cal = _write(tmp_path, CAL_TOO_SHORT)
        runner = CliRunner()
        result = runner.invoke(cli, ["calendar", "check", str(SPEC_PATH), "--calendar", str(cal)])
        assert result.exit_code != 0
        assert "error:" in result.output
        assert "merge" in result.output

    def test_status_today(self, tmp_path):
        cal = _write(tmp_path, CAL_OK)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "calendar",
                "status",
                str(SPEC_PATH),
                "--calendar",
                str(cal),
                "-L",
                "en",
                "--as-of",
                "2026-03-02",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "As of 2026-03-02:" in result.output
        assert "Today: Monday 2026-03-02" in result.output
        assert "Some Topic from Test 1" in result.output
        assert "Upcoming:" in result.output

    def test_status_no_class_today(self, tmp_path):
        cal = _write(tmp_path, CAL_OK)
        runner = CliRunner()
        # Thu 5 Mar: no class (mon/tue/wed); all three are in the past -> finished.
        result = runner.invoke(
            cli,
            [
                "calendar",
                "status",
                str(SPEC_PATH),
                "--calendar",
                str(cal),
                "-L",
                "en",
                "--as-of",
                "2026-03-05",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "finished" in result.output.lower()

    def test_group_registered(self):
        runner = CliRunner()
        top = runner.invoke(cli, ["--help"])
        assert top.exit_code == 0
        assert "calendar" in top.output
        grp = runner.invoke(cli, ["calendar", "--help"])
        assert grp.exit_code == 0
        assert "check" in grp.output
        assert "status" in grp.output
