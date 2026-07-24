"""Failure summary when stage processing raises (issue #596).

When ``course.process_stage()`` raises (e.g. the worker-availability
``ExceptionGroup`` from issue #594), the ``finally`` block in ``_run_stages``
still renders the summary while the exception propagates. Previously it
printed "✓ Build completed successfully" with 0 errors for a failed build,
and the stale-output sweep ran against an incomplete write registry.
``BuildReporter.mark_aborted`` flips the summary to a failure and records a
fatal error so the sweep skips itself.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from clm.cli.build_data_classes import BuildSummary
from clm.cli.build_reporter import BuildReporter
from clm.cli.output_formatter import (
    DefaultOutputFormatter,
    JSONOutputFormatter,
    QuietOutputFormatter,
    VerboseOutputFormatter,
)


def _reporter() -> BuildReporter:
    return BuildReporter(MagicMock())


class TestMarkAborted:
    def test_summary_is_flagged_aborted_and_has_fatal_error(self):
        reporter = _reporter()
        reporter.mark_aborted(RuntimeError("No workers available"))

        summary = reporter.finish_build()
        assert summary.aborted is True
        assert summary.has_errors()
        assert summary.has_fatal_errors()
        [error] = summary.errors
        assert error.category == "build_aborted"
        assert error.error_type == "infrastructure"
        assert "No workers available" in error.message

    def test_exception_group_sub_exceptions_appear_in_message(self):
        # Reference the builtin via ``builtins`` — ruff's py310 target flags
        # the bare name (same workaround as the production code).
        import builtins

        group_type = builtins.BaseExceptionGroup  # type: ignore[attr-defined]
        exc = group_type(
            "unhandled errors in a TaskGroup",
            [
                RuntimeError("No workers available to process 'notebook' jobs"),
                RuntimeError("No workers available to process 'plantuml' jobs"),
            ],
        )
        reporter = _reporter()
        reporter.mark_aborted(exc)

        [error] = reporter.finish_build().errors
        assert "'notebook' jobs" in error.message
        assert "'plantuml' jobs" in error.message

    def test_untouched_reporter_is_not_aborted(self):
        summary = _reporter().finish_build()
        assert summary.aborted is False
        assert not summary.has_errors()


def _aborted_summary() -> BuildSummary:
    return BuildSummary(duration=11.0, total_files=145, aborted=True)


class TestFormatterHeadlines:
    def test_default_formatter_reports_abort(self, capsys):
        formatter = DefaultOutputFormatter(show_progress=False, use_color=False)
        formatter.show_summary(_aborted_summary())
        err = capsys.readouterr().err
        assert "Build aborted" in err
        assert "completed successfully" not in err

    def test_verbose_formatter_reports_abort(self, capsys):
        formatter = VerboseOutputFormatter(show_progress=False, use_color=False)
        formatter.show_summary(_aborted_summary())
        err = capsys.readouterr().err
        assert "Build aborted" in err
        assert "completed successfully" not in err

    def test_quiet_formatter_reports_abort(self, capsys):
        formatter = QuietOutputFormatter()
        formatter.show_summary(_aborted_summary())
        err = capsys.readouterr().err
        assert "Build aborted" in err
        assert "completed successfully" not in err

    def test_json_formatter_reports_abort(self, capsys):
        formatter = JSONOutputFormatter()
        formatter.show_summary(_aborted_summary())
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "aborted"
        assert data["aborted"] is True

    def test_json_formatter_emits_aborted_false_on_success(self, capsys):
        formatter = JSONOutputFormatter()
        formatter.show_summary(BuildSummary(duration=1.0, total_files=0))
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "success"
        assert data["aborted"] is False

    def test_summary_str_reports_abort(self):
        text = str(_aborted_summary())
        assert "✗ Build aborted" in text
        assert "completed successfully" not in text


class TestSweepSkipsAfterAbort:
    def test_stale_output_sweep_skips_when_build_aborted(self):
        """The abort error keeps the sweep from deleting outputs that the
        aborted build never got around to (re)writing."""
        from clm.cli.commands.build import _maybe_run_sweep

        reporter = _reporter()
        reporter.mark_aborted(RuntimeError("No workers available"))

        config = SimpleNamespace(sweep=True, clean=False, watch=False)
        with patch("clm.cli.output_sweep.sweep_stray_files") as sweep:
            sweep.return_value = MagicMock(skipped=True, skip_reason="errors")
            _maybe_run_sweep(
                config=config,
                root_dirs=[],
                backend=MagicMock(),
                build_reporter=reporter,
                only_sections_mode=False,
            )
        skip_reason = sweep.call_args.kwargs["skip_reason"]
        assert skip_reason is not None and "error" in skip_reason


class TestWiring:
    def test_run_stages_marks_abort_before_reraise(self):
        """Pin the ``mark_aborted`` call in the stage-processing exception
        handler (same style as the orphan-cassette-sweep pin test)."""
        import inspect

        from clm.cli.commands.build import process_course_with_backend

        source = inspect.getsource(process_course_with_backend)
        assert "mark_aborted" in source, (
            "process_course_with_backend must call build_reporter."
            "mark_aborted(exc) in its stage-processing exception handler "
            "before re-raising (issue #596). Without it the finally block "
            "renders 'Build completed successfully' for a failed build and "
            "runs the stale-output sweep on an incomplete write registry."
        )


class TestRecordTeardownOrphans:
    """Pool-teardown orphans must reach the exit policy even though they are
    discovered after finish_build has rendered the summary (issue #617)."""

    def test_orphans_are_recorded_as_errors_and_force_nonzero_exit(self):
        from clm.cli.commands.build import _record_teardown_orphans

        summary = BuildSummary(duration=1.0, total_files=3)
        assert summary.timed_out is False
        assert summary.errors == []

        orphans = [
            {"id": 7, "input_file": "slides/a.de.py", "status": "processing", "worker_id": 3},
            {"id": 8, "input_file": "slides/a.en.py", "status": "pending", "worker_id": 4},
        ]
        _record_teardown_orphans(summary, orphans)

        # Marked timed-out so the CLI exits non-zero unconditionally (an
        # incomplete output tree), matching the per-stage-timeout policy.
        assert summary.timed_out is True
        assert len(summary.errors) == 2
        categories = {e.category for e in summary.errors}
        assert categories == {"orphaned_job"}
        assert {e.job_id for e in summary.errors} == {7, 8}
        assert {e.error_type for e in summary.errors} == {"infrastructure"}
        assert any("a.de.py" in e.file_path for e in summary.errors)


class TestFormatExitFailure:
    """The exit-time failure message must distinguish teardown orphans from
    genuine per-stage timeouts (#617/#636 follow-up, Finding 4): orphans are
    appended after finish_build rendered the summary, so 'timed out … see the
    error summary above' is wrong on both counts for them."""

    def test_orphans_produce_dedicated_message_naming_the_files(self):
        from clm.cli.commands.build import _format_exit_failure, _record_teardown_orphans

        summary = BuildSummary(duration=1.0, total_files=3)
        orphans = [
            {"id": 7, "input_file": "slides/a.de.py", "status": "processing", "worker_id": 3},
            {"id": 8, "input_file": "slides/a.en.py", "status": "pending", "worker_id": 4},
        ]
        _record_teardown_orphans(summary, orphans)

        message = _format_exit_failure(summary)

        assert "orphaned" in message
        assert "2 worker job(s)" in message
        assert "slides/a.de.py" in message
        assert "slides/a.en.py" in message
        assert "timed out" not in message

    def test_genuine_timeout_keeps_the_timeout_message(self):
        from clm.cli.commands.build import _format_exit_failure

        summary = BuildSummary(duration=1.0, total_files=3, timed_out=True)

        message = _format_exit_failure(summary)

        assert "timed out" in message
        assert "See the error summary above" in message
        assert "orphaned" not in message
