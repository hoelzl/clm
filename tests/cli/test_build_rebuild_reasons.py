"""Aggregated rebuild-reason breakdown in the build summary (--explain-rebuilds)."""

import json
from unittest.mock import MagicMock

from clm.cli.build_data_classes import BuildSummary
from clm.cli.build_reporter import BuildReporter
from clm.cli.output_formatter import DefaultOutputFormatter, JSONOutputFormatter


def _reporter() -> BuildReporter:
    return BuildReporter(MagicMock())


class TestReporterAggregation:
    def test_reasons_aggregate_by_code(self):
        reporter = _reporter()
        reporter.report_rebuild_reason("a.py", "notebook", "content hash changed", "hash_mismatch")
        reporter.report_rebuild_reason("b.py", "notebook", "content hash changed", "hash_mismatch")
        reporter.report_rebuild_reason("c.py", "notebook", "no cache entry", "no_entry")

        summary = reporter.finish_build()
        assert summary.rebuild_reasons == {"hash_mismatch": 2, "no_entry": 1}
        assert summary.total_rebuilds_explained == 3

    def test_no_reasons_means_empty_dict(self):
        """A normal build (flag off) never calls report_rebuild_reason."""
        summary = _reporter().finish_build()
        assert summary.rebuild_reasons == {}
        assert summary.total_rebuilds_explained == 0

    def test_start_build_resets_counts(self):
        reporter = _reporter()
        reporter.report_rebuild_reason("a.py", "notebook", "x", "no_entry")
        reporter.start_build("course", total_files=1)
        assert reporter._rebuild_reasons == {}


class TestBreakdown:
    def test_breakdown_sorted_by_count_then_label(self):
        summary = BuildSummary(
            duration=1.0,
            total_files=5,
            rebuild_reasons={"no_entry": 1, "hash_mismatch": 3, "metadata_mismatch": 3},
        )
        breakdown = summary.rebuild_reason_breakdown()
        # Most frequent first; ties broken by label (alphabetical).
        assert [count for _, count in breakdown] == [3, 3, 1]
        assert breakdown[0][0] == "content changed (source or a dependency)"
        assert breakdown[1][0] == "new output target (kind/format/language)"
        assert breakdown[2][0].startswith("no cache entry")

    def test_unknown_code_falls_back_to_raw(self):
        summary = BuildSummary(duration=1.0, total_files=1, rebuild_reasons={"weird": 2})
        assert summary.rebuild_reason_breakdown() == [("weird", 2)]


def _summary_with_reasons() -> BuildSummary:
    return BuildSummary(
        duration=1.0,
        total_files=4,
        rebuild_reasons={"hash_mismatch": 2, "no_entry": 1},
    )


class TestFormatters:
    def test_json_formatter_emits_rebuild_reasons(self, capsys):
        formatter = JSONOutputFormatter()
        formatter.show_summary(_summary_with_reasons())
        data = json.loads(capsys.readouterr().out)
        assert data["rebuild_reasons"] == {"hash_mismatch": 2, "no_entry": 1}

    def test_json_formatter_always_emits_key(self, capsys):
        formatter = JSONOutputFormatter()
        formatter.show_summary(BuildSummary(duration=1.0, total_files=0))
        data = json.loads(capsys.readouterr().out)
        assert data["rebuild_reasons"] == {}

    def test_default_formatter_shows_rebuild_section(self, capsys):
        formatter = DefaultOutputFormatter(show_progress=False, use_color=False)
        formatter.show_summary(_summary_with_reasons())
        err = capsys.readouterr().err
        assert "Rebuild reasons (3 cache misses):" in err
        assert "content changed" in err

    def test_default_formatter_silent_without_reasons(self, capsys):
        formatter = DefaultOutputFormatter(show_progress=False, use_color=False)
        formatter.show_summary(BuildSummary(duration=1.0, total_files=0))
        assert "Rebuild reasons" not in capsys.readouterr().err


class TestSummaryString:
    def test_str_includes_rebuild_reasons(self):
        text = str(_summary_with_reasons())
        assert "Rebuild reasons (3 cache misses):" in text
        assert "content changed (source or a dependency)" in text

    def test_str_omits_section_without_reasons(self):
        text = str(BuildSummary(duration=1.0, total_files=0))
        assert "Rebuild reasons" not in text
