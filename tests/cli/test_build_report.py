"""``clm build --report FILE`` — the JSON build envelope as a file (issue #968).

The envelope ``--output-mode json`` prints was only available on stdout and
only when the human progress output was given up. ``--report FILE`` persists
the same document whatever the output mode, and also for the outcomes that
never reach a build summary: a spec that fails to parse or validate, and an
exception that escapes before ``finish_build``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from clm.build import engine as engine_module
from clm.build.config import BuildConfig
from clm.build.engine import initialize_paths_and_course, run_build, write_build_report
from clm.build.errors import SpecValidationFailure
from clm.build.output_formatter import (
    JSONOutputFormatter,
    JSONReportCollector,
    QuietOutputFormatter,
    TeeOutputFormatter,
)
from clm.build.reporter import BuildReporter
from clm.cli.commands import build as build_module
from clm.cli.main import cli
from clm.core.build_data_classes import BuildError, BuildSummary


def _config(tmp_path: Path, **overrides) -> BuildConfig:
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    spec_file = tmp_path / "course.xml"
    spec_file.write_text("<course/>", encoding="utf-8")
    defaults: dict = {
        "spec_file": spec_file,
        "data_dir": data,
        "output_dir": tmp_path / "out",
        "log_level": "INFO",
        "cache_db_path": tmp_path / "cache.db",
        "jobs_db_path": tmp_path / "jobs.db",
        "ignore_cache": False,
        "clear_cache": False,
        "watch": False,
        "print_correlation_ids": False,
        "workers": None,
        "notebook_workers": None,
        "plantuml_workers": None,
        "drawio_workers": None,
        "notebook_image": None,
        "plantuml_image": None,
        "drawio_image": None,
        "report_path": tmp_path / "reports" / "build.json",
    }
    defaults.update(overrides)
    return BuildConfig(**defaults)


def _error(message: str = "boom") -> BuildError:
    return BuildError(
        error_type="user",
        category="notebook_execution",
        severity="error",
        file_path="slides/a.py",
        message=message,
        actionable_guidance="fix it",
    )


def _summary(**overrides) -> BuildSummary:
    defaults: dict = {"duration": 1.5, "total_files": 3}
    defaults.update(overrides)
    return BuildSummary(**defaults)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# JSONReportCollector
# ---------------------------------------------------------------------------


class TestJSONReportCollector:
    def test_show_summary_records_without_printing(self, capsys: pytest.CaptureFixture[str]):
        collector = JSONReportCollector()
        collector.show_summary(_summary(errors=[_error()]))

        assert capsys.readouterr().out == ""
        assert collector.has_outcome
        assert collector.output_data["status"] == "failed"
        assert collector.output_data["error_count"] == 1

    def test_envelope_matches_json_output_mode_document(self, capsys: pytest.CaptureFixture[str]):
        """Parity: the file carries exactly what ``-O json`` prints, plus the
        report-only keys. A consumer parses one shape either way."""
        summary = _summary(errors=[_error()], warnings=[])
        stdout_formatter = JSONOutputFormatter()
        stdout_formatter.show_build_start("Course", 3, ["c-de", "c-en"])
        stdout_formatter.show_stage_start("Notebooks", 1, 2, 3, 1)
        stdout_formatter.show_summary(summary)
        printed = json.loads(capsys.readouterr().out)

        collector = JSONReportCollector()
        collector.show_build_start("Course", 3, ["c-de", "c-en"])
        collector.show_stage_start("Notebooks", 1, 2, 3, 1)
        collector.show_summary(summary)
        envelope = collector.envelope()

        assert envelope.pop("provenance_manifests") == []
        assert envelope == printed

    def test_record_summary_is_idempotent_over_a_mutated_summary(self):
        """Teardown orphans (issue #617) mutate the summary after it was
        rendered; re-recording re-derives the summary-owned keys."""
        collector = JSONReportCollector()
        summary = _summary()
        collector.show_summary(summary)
        assert collector.output_data["status"] == "success"

        summary.timed_out = True
        summary.errors.append(_error("orphaned job"))
        collector.record_summary(summary)

        assert collector.output_data["status"] == "timed_out"
        assert collector.output_data["timed_out"] is True
        assert collector.output_data["error_count"] == 1

    def test_record_failure_adopts_the_load_failure_envelope(self):
        collector = JSONReportCollector()
        collector.record_failure({"status": "validation_failed", "error_count": 2})

        assert collector.has_outcome
        assert collector.envelope()["status"] == "validation_failed"

    def test_record_exception_marks_an_outcome_less_build_aborted(self):
        collector = JSONReportCollector()
        collector.record_exception(RuntimeError("No workers available"))

        data = collector.envelope()
        assert data["status"] == "aborted"
        assert data["aborted"] is True
        [err] = data["errors"]
        assert err["category"] == "build_aborted"
        assert "No workers available" in err["message"]
        assert data["error_count"] == 1

    def test_record_exception_keeps_a_recorded_outcome(self):
        """An exception that escapes *after* the summary (e.g. a raise past
        the finally block) must not overwrite the summary's verdict."""
        collector = JSONReportCollector()
        collector.show_summary(_summary(errors=[_error()]))
        collector.record_exception(RuntimeError("late"))

        assert collector.envelope()["status"] == "failed"
        assert collector.envelope()["errors"][0]["message"] == "boom"

    def test_record_exception_distinguishes_interrupt(self):
        collector = JSONReportCollector()
        collector.record_exception(KeyboardInterrupt())
        assert collector.envelope()["status"] == "interrupted"

    def test_envelope_always_carries_the_report_only_keys(self):
        data = JSONReportCollector().envelope()
        assert data["provenance_manifests"] == []
        assert "log_directory" in data
        assert "worker_log_directory" in data


# ---------------------------------------------------------------------------
# TeeOutputFormatter
# ---------------------------------------------------------------------------


class TestTeeOutputFormatter:
    def test_forwards_every_event_to_both(self):
        primary = MagicMock()
        secondary = MagicMock()
        tee = TeeOutputFormatter(primary, secondary)
        summary = _summary()
        error = _error()

        tee.show_startup_message("hi")
        tee.show_build_start("Course", 3, ["c-de"])
        tee.show_stage_start("Notebooks", 1, 2, 3, 1)
        tee.update_progress(1, 3, 2, 1)
        tee.show_error(error)
        tee.show_file_started("a.py", "notebook", 7)
        tee.show_file_completed("a.py", "notebook", 7, True)
        tee.show_cache_hit("a.py", "notebook", "L1")
        tee.show_rebuild_reason("a.py", "notebook", "no entry")
        tee.show_summary(summary)
        tee.cleanup()

        for target in (primary, secondary):
            target.show_startup_message.assert_called_once_with("hi")
            target.show_build_start.assert_called_once_with("Course", 3, ["c-de"])
            target.show_stage_start.assert_called_once_with("Notebooks", 1, 2, 3, 1)
            target.update_progress.assert_called_once_with(1, 3, 2, 1)
            target.show_error.assert_called_once_with(error)
            target.show_file_started.assert_called_once_with("a.py", "notebook", 7)
            target.show_file_completed.assert_called_once_with("a.py", "notebook", 7, True)
            target.show_cache_hit.assert_called_once_with("a.py", "notebook", "L1")
            target.show_rebuild_reason.assert_called_once_with("a.py", "notebook", "no entry")
            target.show_summary.assert_called_once_with(summary)
            target.cleanup.assert_called_once_with()

    def test_primary_decides_what_is_shown_live(self):
        primary = MagicMock()
        primary.should_show_error.return_value = True
        primary.should_show_warning.return_value = False
        tee = TeeOutputFormatter(primary, JSONReportCollector())

        assert tee.should_show_error(_error()) is True
        assert tee.should_show_warning(MagicMock()) is False

    def test_quiet_console_output_is_unchanged_while_the_collector_records(
        self, capsys: pytest.CaptureFixture[str]
    ):
        """The tee must not alter what the user sees; the collector is silent."""
        collector = JSONReportCollector()
        reporter = BuildReporter(TeeOutputFormatter(QuietOutputFormatter(), collector))
        reporter.start_build("Course", 1)
        reporter.report_error(_error())
        reporter.finish_build()

        assert capsys.readouterr().out == ""
        assert collector.output_data["status"] == "failed"
        assert collector.output_data["course_name"] == "Course"


# ---------------------------------------------------------------------------
# run_build: the report file
# ---------------------------------------------------------------------------


class TestRunBuildWritesReport:
    def test_success_report_carries_summary_manifests_and_spec(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        config = _config(tmp_path)
        manifest = tmp_path / "out" / ".clm-manifest.json"

        async def fake_impl(cfg, *, output_formatter, build_reporter, watch_runner, report):
            assert isinstance(output_formatter, TeeOutputFormatter)
            assert output_formatter.secondary is report
            reporter = BuildReporter(output_formatter)
            reporter.start_build("Course", 3, output_dirs=["c-de"])
            summary = reporter.finish_build()
            report.record_manifests([manifest])
            return summary

        monkeypatch.setattr(engine_module, "_run_build_impl", fake_impl)

        result = asyncio.run(run_build(config))

        assert result is not None and result.total_files == 3
        data = _read(config.report_path)
        assert data["status"] == "success"
        assert data["course_name"] == "Course"
        assert data["output_dirs"] == ["c-de"]
        assert data["provenance_manifests"] == [str(manifest)]
        assert data["spec_file"] == str(config.spec_file.absolute())
        assert "log_directory" in data
        assert "report_written_at" in data

    def test_report_is_written_in_every_output_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        """The whole point: the file exists even when stdout is human output —
        and under ``-O json`` stdout still carries the same document."""
        seen: dict[str, dict] = {}
        for mode in ("default", "quiet", "verbose", "json"):
            config = _config(
                tmp_path, output_mode=mode, no_progress=True, report_path=tmp_path / f"{mode}.json"
            )

            async def fake_impl(cfg, *, output_formatter, build_reporter, watch_runner, report):
                reporter = BuildReporter(output_formatter)
                reporter.start_build("Course", 1)
                reporter.report_error(_error())
                return reporter.finish_build()

            monkeypatch.setattr(engine_module, "_run_build_impl", fake_impl)
            asyncio.run(run_build(config))
            seen[mode] = _read(config.report_path)

        captured = capsys.readouterr()
        for mode, data in seen.items():
            assert data["status"] == "failed", mode
            assert data["errors"][0]["message"] == "boom", mode
        # ``-O json`` still prints exactly one document to stdout.
        assert json.loads(captured.out)["status"] == "failed"

    def test_exception_before_any_summary_yields_an_aborted_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        config = _config(tmp_path)

        async def fake_impl(cfg, **kwargs):
            raise RuntimeError("database is locked")

        monkeypatch.setattr(engine_module, "_run_build_impl", fake_impl)

        with pytest.raises(RuntimeError, match="database is locked"):
            asyncio.run(run_build(config))

        data = _read(config.report_path)
        assert data["status"] == "aborted"
        assert data["errors"][0]["message"] == "database is locked"

    def test_spec_validation_failure_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Runs the real engine up to the spec gate: the validation envelope
        reaches the file although no build summary ever exists."""
        config = _config(tmp_path, output_mode="default")
        fake_spec = MagicMock()
        fake_spec.validate.return_value = ["missing <name>", "bad target"]
        monkeypatch.setattr("clm.core.course_spec.CourseSpec.from_file", lambda *a, **kw: fake_spec)

        with pytest.raises(SpecValidationFailure):
            asyncio.run(run_build(config))

        data = _read(config.report_path)
        assert data["status"] == "validation_failed"
        assert data["error_count"] == 2
        assert [e["message"] for e in data["errors"]] == ["missing <name>", "bad target"]
        assert data["errors"][0]["category"] == "spec_validation"
        assert data["spec_file"] == str(config.spec_file.absolute())

    def test_spec_parse_failure_is_reported_in_json_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        """JSON mode exits via SystemExit after printing; the file gets the
        same parse-error document, not a generic "aborted"."""
        from clm.core.course_spec import CourseSpecError

        config = _config(tmp_path, output_mode="json")

        def fail(*args, **kwargs):
            raise CourseSpecError("bad xml")

        monkeypatch.setattr("clm.core.course_spec.CourseSpec.from_file", fail)

        with pytest.raises(SystemExit):
            asyncio.run(run_build(config))

        data = _read(config.report_path)
        assert data["status"] == "error"
        assert data["error_type"] == "spec_parsing"
        assert data["message"] == "bad xml"
        assert json.loads(capsys.readouterr().out)["error_type"] == "spec_parsing"

    def test_no_report_path_means_no_tee_and_no_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        config = _config(tmp_path, report_path=None)

        async def fake_impl(cfg, *, output_formatter, build_reporter, watch_runner, report):
            assert report is None
            assert not isinstance(output_formatter, TeeOutputFormatter)
            return _summary()

        monkeypatch.setattr(engine_module, "_run_build_impl", fake_impl)
        asyncio.run(run_build(config))
        assert not (tmp_path / "reports").exists()

    def test_unwritable_report_path_does_not_change_the_outcome(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        """The report is a side channel: a failed write is reported on stderr,
        never raised, so the build's exit code stays what the build earned."""
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("", encoding="utf-8")
        config = _config(tmp_path, report_path=blocker / "report.json")

        async def fake_impl(cfg, **kwargs):
            return _summary()

        monkeypatch.setattr(engine_module, "_run_build_impl", fake_impl)
        result = asyncio.run(run_build(config))

        assert result is not None
        assert "Could not write the build report" in capsys.readouterr().err


class TestWriteBuildReport:
    def test_creates_parent_directories_and_utf8(self, tmp_path: Path):
        collector = JSONReportCollector()
        collector.show_summary(_summary(errors=[_error("Umlaut: äöü")]))
        target = tmp_path / "a" / "b" / "report.json"

        assert write_build_report(collector, target, spec_file=tmp_path / "c.xml") == target
        data = _read(target)
        assert data["errors"][0]["message"] == "Umlaut: äöü"
        assert "äöü" in target.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# initialize_paths_and_course: the load-failure callback
# ---------------------------------------------------------------------------


class TestLoadFailureCallback:
    def test_validation_failure_invokes_callback_before_raising(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        config = _config(tmp_path, output_mode="default", report_path=None)
        fake_spec = MagicMock()
        fake_spec.validate.return_value = ["err"]
        monkeypatch.setattr("clm.core.course_spec.CourseSpec.from_file", lambda *a, **kw: fake_spec)
        seen: list[dict] = []

        with pytest.raises(SpecValidationFailure):
            initialize_paths_and_course(config, on_load_failure=seen.append)

        [envelope] = seen
        assert envelope["status"] == "validation_failed"
        assert envelope["errors"][0]["message"] == "err"

    def test_callback_is_optional(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        config = _config(tmp_path, output_mode="default", report_path=None)
        fake_spec = MagicMock()
        fake_spec.validate.return_value = ["err"]
        monkeypatch.setattr("clm.core.course_spec.CourseSpec.from_file", lambda *a, **kw: fake_spec)
        with pytest.raises(SpecValidationFailure):
            initialize_paths_and_course(config)


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------


class TestCliFlag:
    def test_report_in_help(self):
        result = CliRunner().invoke(cli, ["build", "--help"])
        assert result.exit_code == 0
        assert "--report" in result.output

    def test_report_path_reaches_the_build_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        spec_file = tmp_path / "spec.xml"
        spec_file.write_text("<course/>", encoding="utf-8")
        captured: dict[str, Path | None] = {}

        async def fake_run_build(config, **kwargs):
            captured["report_path"] = config.report_path
            return None

        monkeypatch.setattr(build_module, "run_build", fake_run_build)
        target = tmp_path / "reports" / "build.json"

        result = CliRunner().invoke(cli, ["build", str(spec_file), "--report", str(target)])

        assert result.exit_code == 0, result.output
        assert captured["report_path"] == target

    def test_default_is_no_report(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        spec_file = tmp_path / "spec.xml"
        spec_file.write_text("<course/>", encoding="utf-8")
        captured: dict[str, Path | None] = {}

        async def fake_run_build(config, **kwargs):
            captured["report_path"] = config.report_path
            return None

        monkeypatch.setattr(build_module, "run_build", fake_run_build)
        result = CliRunner().invoke(cli, ["build", str(spec_file)])

        assert result.exit_code == 0, result.output
        assert captured["report_path"] is None

    def test_report_is_written_even_when_the_build_exits_nonzero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """A timed-out build exits 1 (issue #143) — and the report says so."""
        spec_file = tmp_path / "spec.xml"
        spec_file.write_text("<course/>", encoding="utf-8")
        target = tmp_path / "build.json"

        async def fake_impl(cfg, *, output_formatter, build_reporter, watch_runner, report):
            reporter = BuildReporter(output_formatter)
            reporter.start_build("Course", 1)
            reporter.mark_timed_out()
            return reporter.finish_build()

        monkeypatch.setattr(engine_module, "_run_build_impl", fake_impl)
        result = CliRunner().invoke(
            cli, ["build", str(spec_file), "--report", str(target), "-O", "quiet"]
        )

        assert result.exit_code == 1
        data = _read(target)
        assert data["status"] == "timed_out"
        assert data["timed_out"] is True
        assert data["spec_file"] == str(spec_file.absolute())
