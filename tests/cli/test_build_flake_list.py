"""Flake list in the build summary (issue #330)."""

import json
from unittest.mock import MagicMock

from clm.cli.build_data_classes import BuildSummary, FlakyFileInfo
from clm.cli.build_reporter import BuildReporter
from clm.cli.output_formatter import DefaultOutputFormatter, JSONOutputFormatter


def _reporter() -> BuildReporter:
    return BuildReporter(MagicMock())


class TestReporterAggregation:
    def test_flakes_aggregate_per_file(self):
        reporter = _reporter()
        reporter.report_flaky_file(
            "C:/c/slides_a.py", attempts=2, failure_types=["dead_kernel"], language="de"
        )
        reporter.report_flaky_file(
            "C:/c/slides_a.py",
            attempts=4,
            failure_types=["dead_kernel", "cell_execution_error"],
            language="en",
        )
        reporter.report_flaky_file("C:/c/slides_b.py", attempts=2)

        summary = reporter.finish_build()
        assert [f.file_path for f in summary.flaky_files] == [
            "C:/c/slides_a.py",
            "C:/c/slides_b.py",
        ]
        flaky_a = summary.flaky_files[0]
        assert flaky_a.max_attempts == 4
        assert flaky_a.flake_count == 2
        assert flaky_a.failure_types == ["dead_kernel", "cell_execution_error"]
        assert flaky_a.languages == ["de", "en"]

    def test_no_flakes_means_empty_list(self):
        summary = _reporter().finish_build()
        assert summary.flaky_files == []

    def test_late_reports_after_finish_are_ignored(self):
        reporter = _reporter()
        reporter.finish_build()
        reporter.report_flaky_file("C:/c/slides_late.py", attempts=2)
        assert reporter._flaky_files == {}


def _summary_with_flakes() -> BuildSummary:
    return BuildSummary(
        duration=1.0,
        total_files=3,
        flaky_files=[
            FlakyFileInfo(
                file_path="C:/c/slides_a.py",
                max_attempts=3,
                failure_types=["dead_kernel"],
                languages=["de"],
                flake_count=1,
            )
        ],
    )


class TestFormatters:
    def test_json_formatter_emits_flaky_files(self, capsys):
        formatter = JSONOutputFormatter()
        formatter.show_summary(_summary_with_flakes())
        data = json.loads(capsys.readouterr().out)
        assert data["flaky_files"] == [
            {
                "file_path": "C:/c/slides_a.py",
                "max_attempts": 3,
                "failure_types": ["dead_kernel"],
                "languages": ["de"],
                "flake_count": 1,
            }
        ]

    def test_json_formatter_always_emits_key(self, capsys):
        formatter = JSONOutputFormatter()
        formatter.show_summary(BuildSummary(duration=1.0, total_files=0))
        data = json.loads(capsys.readouterr().out)
        assert data["flaky_files"] == []

    def test_default_formatter_shows_flake_section(self, capsys):
        formatter = DefaultOutputFormatter(show_progress=False, use_color=False)
        formatter.show_summary(_summary_with_flakes())
        err = capsys.readouterr().err
        assert "Flaky decks" in err
        assert "slides_a.py" in err
        assert "dead_kernel" in err

    def test_default_formatter_silent_without_flakes(self, capsys):
        formatter = DefaultOutputFormatter(show_progress=False, use_color=False)
        formatter.show_summary(BuildSummary(duration=1.0, total_files=0))
        assert "Flaky decks" not in capsys.readouterr().err


class TestSummaryString:
    def test_str_includes_flaky_decks(self):
        text = str(_summary_with_flakes())
        assert "Flaky decks (passed only after retry):" in text
        assert "slides_a.py" in text
