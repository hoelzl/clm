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
            cli, ["calendar", "generate", str(SPEC_PATH), "--calendar", str(cal), "-L", "en"]
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
            [
                "calendar",
                "generate",
                str(SPEC_PATH),
                "--calendar",
                str(cal),
                "-L",
                "en",
                "-f",
                "csv",
            ],
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
            [
                "calendar",
                "generate",
                str(SPEC_PATH),
                "--calendar",
                str(cal),
                "-L",
                "en",
                "-f",
                "ics",
            ],
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
                "calendar",
                "generate",
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
            cli, ["calendar", "generate", str(SPEC_PATH), "--calendar", str(cal), "-L", "en"]
        )
        assert result.exit_code != 0
        assert "merge" in result.output  # quantified deficit reported to stderr

    def test_channel_without_release_channels_errors(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["calendar", "generate", str(SPEC_PATH), "--channel", "jan"])
        assert result.exit_code != 0
        assert "release-channels" in result.output

    def test_requires_channel_or_calendar(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["calendar", "generate", str(SPEC_PATH)])
        assert result.exit_code != 0
        assert "--channel" in result.output or "--calendar" in result.output

    def test_registered_in_calendar_group_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["calendar", "--help"])
        assert result.exit_code == 0
        assert "generate" in result.output


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


class TestCalendarPush:
    """``clm calendar push`` — Google API boundary mocked at the google_sync seam."""

    CAL_WITH_ID = CAL_OK + '\n[google]\ncalendar_id = "cal-id"\n'

    @staticmethod
    def _creds(tmp_path) -> Path:
        # Content is irrelevant: load_credentials is monkeypatched in tests
        # that get past the option checks; click only verifies existence.
        p = tmp_path / "creds.json"
        p.write_text("{}", encoding="utf-8")
        return p

    @staticmethod
    def _patch_api(monkeypatch, existing=None):
        from clm.cohort_calendar import google_sync

        monkeypatch.setattr(google_sync, "load_credentials", lambda path, **kw: object())
        monkeypatch.setattr(google_sync, "build_service", lambda creds: object())
        monkeypatch.setattr(
            google_sync, "fetch_managed_events", lambda service, cal_id, ns: existing or []
        )

    def test_requires_calendar_id(self, tmp_path):
        cal = _write(tmp_path, CAL_OK)
        result = CliRunner().invoke(
            cli,
            [
                "calendar",
                "push",
                str(SPEC_PATH),
                "--calendar",
                str(cal),
                "--credentials",
                str(self._creds(tmp_path)),
            ],
        )
        assert result.exit_code != 0
        assert "--calendar-id" in result.output

    def test_requires_credentials(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CLM_GOOGLE_CREDENTIALS", raising=False)
        cal = _write(tmp_path, self.CAL_WITH_ID)
        result = CliRunner().invoke(
            cli, ["calendar", "push", str(SPEC_PATH), "--calendar", str(cal)]
        )
        assert result.exit_code != 0
        assert "CLM_GOOGLE_CREDENTIALS" in result.output

    def test_dry_run_prints_plan_and_changes_nothing(self, tmp_path, monkeypatch):
        self._patch_api(monkeypatch)
        from clm.cohort_calendar import google_sync

        def _no_apply(*args, **kwargs):
            raise AssertionError("apply_plan must not run under --dry-run")

        monkeypatch.setattr(google_sync, "apply_plan", _no_apply)
        cal = _write(tmp_path, self.CAL_WITH_ID)
        result = CliRunner().invoke(
            cli,
            [
                "calendar",
                "push",
                str(SPEC_PATH),
                "--calendar",
                str(cal),
                "-L",
                "en",
                "--credentials",
                str(self._creds(tmp_path)),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "+ 2026-03-02" in result.output
        assert "Some Topic from Test 1" in result.output
        assert "Dry run" in result.output

    def test_push_applies_plan(self, tmp_path, monkeypatch):
        self._patch_api(monkeypatch)
        from clm.cohort_calendar import google_sync

        applied = {}
        monkeypatch.setattr(
            google_sync,
            "apply_plan",
            lambda service, cal_id, plan: applied.update(calendar_id=cal_id, plan=plan),
        )
        cal = _write(tmp_path, self.CAL_WITH_ID)
        result = CliRunner().invoke(
            cli,
            [
                "calendar",
                "push",
                str(SPEC_PATH),
                "--calendar",
                str(cal),
                "-L",
                "en",
                "--credentials",
                str(self._creds(tmp_path)),
            ],
        )
        assert result.exit_code == 0, result.output
        assert applied["calendar_id"] == "cal-id"
        assert len(applied["plan"].inserts) == 3
        assert "Pushed: 3 insert(s)" in result.output

    def test_cli_calendar_id_overrides_toml(self, tmp_path, monkeypatch):
        self._patch_api(monkeypatch)
        from clm.cohort_calendar import google_sync

        applied = {}
        monkeypatch.setattr(
            google_sync,
            "apply_plan",
            lambda service, cal_id, plan: applied.update(calendar_id=cal_id),
        )
        cal = _write(tmp_path, self.CAL_WITH_ID)
        result = CliRunner().invoke(
            cli,
            [
                "calendar",
                "push",
                str(SPEC_PATH),
                "--calendar",
                str(cal),
                "--calendar-id",
                "other-cal",
                "--credentials",
                str(self._creds(tmp_path)),
            ],
        )
        assert result.exit_code == 0, result.output
        assert applied["calendar_id"] == "other-cal"

    def test_google_error_becomes_clean_cli_error(self, tmp_path, monkeypatch):
        from clm.cohort_calendar import google_sync

        def _boom(path, **kwargs):
            raise google_sync.GoogleSyncError("Google Calendar push requires the [gcal] extra")

        monkeypatch.setattr(google_sync, "load_credentials", _boom)
        cal = _write(tmp_path, self.CAL_WITH_ID)
        result = CliRunner().invoke(
            cli,
            [
                "calendar",
                "push",
                str(SPEC_PATH),
                "--calendar",
                str(cal),
                "--credentials",
                str(self._creds(tmp_path)),
            ],
        )
        assert result.exit_code != 0
        assert "[gcal]" in result.output

    def test_projection_errors_block_push(self, tmp_path):
        cal = _write(tmp_path, CAL_TOO_SHORT + '[google]\ncalendar_id = "cal-id"\n')
        result = CliRunner().invoke(
            cli,
            [
                "calendar",
                "push",
                str(SPEC_PATH),
                "--calendar",
                str(cal),
                "--credentials",
                str(self._creds(tmp_path)),
            ],
        )
        assert result.exit_code != 0
        assert "errors" in result.output

    def test_push_registered_in_group_help(self):
        result = CliRunner().invoke(cli, ["calendar", "--help"])
        assert result.exit_code == 0
        assert "push" in result.output
