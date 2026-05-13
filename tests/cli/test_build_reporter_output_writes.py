"""Tests for BuildReporter.report_output_writes (PR 2.3).

These cover the registry → reporter bridge: how a populated
:class:`OutputWriteRegistry` is drained into the :class:`BuildSummary`
counters, the structured ``output_conflicts`` list, and the standard
``BuildWarning`` channel.

The standalone registry semantics are covered by
``tests/core/test_output_write_registry.py``; these tests only assert
the bridge.
"""

from pathlib import Path

from clm.cli.build_data_classes import BuildSummary
from clm.cli.build_reporter import BuildReporter
from clm.cli.output_formatter import QuietOutputFormatter
from clm.core.output_write_registry import OutputWriteRegistry


def _make_reporter() -> BuildReporter:
    reporter = BuildReporter(QuietOutputFormatter())
    reporter.start_build(course_name="test", total_files=0, total_stages=1)
    return reporter


class TestEmptyRegistry:
    def test_empty_registry_produces_zero_summary(self, tmp_path):
        reporter = _make_reporter()
        registry = OutputWriteRegistry()

        reporter.report_output_writes(registry)
        summary = reporter.finish_build()

        assert summary.output_dedup_count == 0
        assert summary.output_conflicts == []
        assert summary.output_large_file_collision_count == 0
        # No new warnings emitted for an empty registry.
        assert all(w.category != "output_path_conflict" for w in summary.warnings)


class TestDedupCount:
    def test_dedup_count_propagates_to_summary(self, tmp_path):
        src_a = tmp_path / "a.txt"
        src_b = tmp_path / "b.txt"
        src_a.write_text("same", encoding="utf-8")
        src_b.write_text("same", encoding="utf-8")
        out = tmp_path / "out.txt"

        registry = OutputWriteRegistry()
        registry.record_write(out, content_source=src_a, source=src_a)
        registry.record_write(out, content_source=src_b, source=src_b)

        reporter = _make_reporter()
        reporter.report_output_writes(registry)
        summary = reporter.finish_build()

        assert summary.output_dedup_count == 1
        assert summary.output_conflicts == []


class TestConflictsBecomeWarnings:
    def test_conflict_added_as_warning_and_to_summary(self, tmp_path):
        src_a = tmp_path / "a.txt"
        src_b = tmp_path / "b.txt"
        src_a.write_text("first", encoding="utf-8")
        src_b.write_text("second", encoding="utf-8")
        out = tmp_path / "out.txt"

        registry = OutputWriteRegistry()
        registry.record_write(out, content_source=src_a, source=src_a)
        registry.record_write(out, content_source=src_b, source=src_b)

        reporter = _make_reporter()
        reporter.report_output_writes(registry)
        summary = reporter.finish_build()

        assert len(summary.output_conflicts) == 1
        conflict = summary.output_conflicts[0]
        assert conflict.output_path == str(out)
        assert conflict.first_writer == str(src_a)
        assert conflict.last_writer == str(src_b)
        assert conflict.conflict_count == 1
        assert conflict.first_hash != conflict.last_hash

        # The conflict also shows up via the standard warning channel.
        path_conflicts = [w for w in summary.warnings if w.category == "output_path_conflict"]
        assert len(path_conflicts) == 1
        assert path_conflicts[0].file_path == str(out)
        assert "first writer" in path_conflicts[0].message.lower()


class TestLargeFileSummary:
    def test_large_file_collision_count_propagates(self, tmp_path, monkeypatch):
        # Force every write through the large-file fast path.
        monkeypatch.setenv("CLM_OUTPUT_DEDUP_HASH_LIMIT_MB", "0")
        registry = OutputWriteRegistry()

        out = tmp_path / "big.bin"
        src_a = tmp_path / "a.bin"
        src_b = tmp_path / "b.bin"
        src_a.write_bytes(b"first")
        src_b.write_bytes(b"second")

        registry.record_write(out, content_source=src_a, source=src_a)
        registry.record_write(out, content_source=src_b, source=src_b)

        reporter = _make_reporter()
        reporter.report_output_writes(registry)
        summary = reporter.finish_build()

        assert summary.output_large_file_collision_count == 1
        # Surfaces as one summary-level low-severity warning, not per event.
        large_warnings = [
            w for w in summary.warnings if w.category == "output_large_file_collision"
        ]
        assert len(large_warnings) == 1


class TestStartBuildResetsState:
    def test_second_build_does_not_carry_over_first(self, tmp_path):
        src_a = tmp_path / "a.txt"
        src_b = tmp_path / "b.txt"
        src_a.write_text("first", encoding="utf-8")
        src_b.write_text("second", encoding="utf-8")
        out = tmp_path / "out.txt"

        reporter = BuildReporter(QuietOutputFormatter())
        reporter.start_build(course_name="run1", total_files=0)

        registry = OutputWriteRegistry()
        registry.record_write(out, content_source=src_a, source=src_a)
        registry.record_write(out, content_source=src_b, source=src_b)
        reporter.report_output_writes(registry)
        summary1 = reporter.finish_build()
        assert len(summary1.output_conflicts) == 1

        # Start a second build with an empty registry; no state should
        # carry over.
        reporter.start_build(course_name="run2", total_files=0)
        empty_registry = OutputWriteRegistry()
        reporter.report_output_writes(empty_registry)
        summary2 = reporter.finish_build()
        assert summary2.output_dedup_count == 0
        assert summary2.output_conflicts == []
        assert summary2.output_large_file_collision_count == 0


class TestSummaryStringIncludesCounts:
    def test_str_summary_includes_dedup_line_when_nonzero(self, tmp_path):
        summary = BuildSummary(
            duration=1.0,
            total_files=3,
            output_dedup_count=4,
        )
        text = str(summary)
        assert "duplicate output writes deduplicated" in text

    def test_str_summary_includes_dedup_line_when_zero(self, tmp_path):
        # Always-visible since v1.4.1: lets users confirm the output-
        # write registry ran, mirroring the unconditional errors and
        # warnings counts.
        summary = BuildSummary(
            duration=1.0,
            total_files=3,
            output_dedup_count=0,
            output_conflicts=[],
        )
        text = str(summary)
        assert "0 duplicate output writes deduplicated" in text
        assert "0 output paths had conflicting writes" in text


class TestJsonFormatterIncludesOutputKeys:
    def test_json_formatter_emits_keys_even_when_empty(self, tmp_path, capsys):
        from clm.cli.output_formatter import JSONOutputFormatter

        formatter = JSONOutputFormatter()
        formatter.show_build_start("test", total_files=0, output_dirs=[])
        summary = BuildSummary(duration=0.1, total_files=0)
        formatter.show_summary(summary)
        captured = capsys.readouterr().out

        import json as _json

        data = _json.loads(captured)
        assert data["output_dedup_count"] == 0
        assert data["output_large_file_collision_count"] == 0
        assert data["output_conflicts"] == []

    def test_json_formatter_emits_conflict_records(self, tmp_path, capsys):
        from clm.cli.build_data_classes import OutputConflictInfo
        from clm.cli.output_formatter import JSONOutputFormatter

        formatter = JSONOutputFormatter()
        formatter.show_build_start("test", total_files=0, output_dirs=[])

        summary = BuildSummary(
            duration=0.1,
            total_files=0,
            output_dedup_count=2,
            output_conflicts=[
                OutputConflictInfo(
                    output_path=str(Path("/tmp/out.txt")),
                    first_writer="src_a",
                    last_writer="src_b",
                    first_hash="h1",
                    last_hash="h2",
                    conflict_count=1,
                )
            ],
        )
        formatter.show_summary(summary)
        captured = capsys.readouterr().out

        import json as _json

        data = _json.loads(captured)
        assert data["output_dedup_count"] == 2
        assert len(data["output_conflicts"]) == 1
        record = data["output_conflicts"][0]
        assert record["first_writer"] == "src_a"
        assert record["last_writer"] == "src_b"
        assert record["first_hash"] == "h1"
        assert record["last_hash"] == "h2"
        assert record["conflict_count"] == 1
