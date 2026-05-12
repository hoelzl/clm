"""End-to-end pipeline tests for OutputWriteRegistry (PR 2.4).

These tests stitch the per-component coverage together at the
build-pipeline level:

- Many topics producing the same output path with identical content
  (the C# NUnitTestRunner pattern) collapse to one write with the
  remaining N-1 counted as dedups.
- Differing-content writes to the same output path surface as one
  ``output_path_conflict`` warning (plus an ``OutputConflictInfo``
  entry) with last-writer-wins semantics on disk.
- ImageRegistry-owned paths still produce ``image_collision`` and do
  NOT produce ``output_path_conflict`` (no double-warning).
- The JSON formatter end-to-end shows the registry totals after a
  build summary is finalized.
"""

import json as _json
from pathlib import Path

from clm.cli.build_data_classes import BuildSummary
from clm.cli.build_reporter import BuildReporter
from clm.cli.output_formatter import JSONOutputFormatter, QuietOutputFormatter
from clm.core.image_registry import ImageRegistry
from clm.core.output_write_registry import OutputWriteRegistry
from clm.infrastructure.backends.local_ops_backend import LocalOpsBackend
from clm.infrastructure.messaging.base_classes import Payload
from clm.infrastructure.operation import Operation
from clm.infrastructure.utils.copy_file_data import CopyFileData


class PytestLocalOpsBackend(LocalOpsBackend):
    async def execute_operation(self, operation: Operation, payload: Payload) -> None:
        pass

    async def wait_for_completion(self, all_submitted=None) -> bool:
        return True


def _make_copy_data(source: Path, out: Path, base: Path) -> CopyFileData:
    return CopyFileData(
        input_path=source,
        output_path=out,
        relative_input_path=source.relative_to(base),
    )


class TestNUnitTestRunnerPattern:
    """N topics that produce the same shared output file (the C#
    course's NUnitTestRunner.cs pattern). Identical bytes → first
    write proceeds; remaining N-1 dedup-skip."""

    async def test_six_topics_same_runner_file(self, tmp_path):
        # Simulate six topic source directories each holding a byte-
        # identical copy of NUnitTestRunner.cs.
        sources: list[Path] = []
        for i in range(6):
            src_dir = tmp_path / f"topic_{i:03d}" / "src"
            src_dir.mkdir(parents=True)
            runner = src_dir / "NUnitTestRunner.cs"
            runner.write_text(
                "// shared test runner — identical across topics\n",
                encoding="utf-8",
            )
            sources.append(runner)

        # All six writes target the same shared output path (typical
        # for course-wide shared runner files).
        out = tmp_path / "out" / "NUnitTestRunner.cs"

        async with PytestLocalOpsBackend() as backend:
            for src in sources:
                await backend.copy_file_to_output(_make_copy_data(src, out, tmp_path))

            registry = backend.output_write_registry
            assert registry.total_dedups == 5
            assert registry.total_conflicts == 0

            reporter = BuildReporter(QuietOutputFormatter())
            reporter.start_build(course_name="cs-course", total_files=6)
            reporter.report_output_writes(registry)
            summary = reporter.finish_build()

            assert summary.output_dedup_count == 5
            assert summary.output_conflicts == []
            assert "duplicate output writes deduplicated" in str(summary)

    async def test_drift_creates_conflict(self, tmp_path):
        # Same pattern, but topic 3 ships a drifted version.
        sources: list[Path] = []
        for i in range(5):
            src_dir = tmp_path / f"topic_{i:03d}" / "src"
            src_dir.mkdir(parents=True)
            runner = src_dir / "NUnitTestRunner.cs"
            payload = "// drifted on topic 3\n" if i == 3 else "// shared canonical runner\n"
            runner.write_text(payload, encoding="utf-8")
            sources.append(runner)

        out = tmp_path / "out" / "NUnitTestRunner.cs"

        async with PytestLocalOpsBackend() as backend:
            for src in sources:
                await backend.copy_file_to_output(_make_copy_data(src, out, tmp_path))

            registry = backend.output_write_registry
            assert registry.total_conflicts >= 1

            reporter = BuildReporter(QuietOutputFormatter())
            reporter.start_build(course_name="cs-course", total_files=5)
            reporter.report_output_writes(registry)
            summary = reporter.finish_build()

            # Last-writer-wins: the file on disk should reflect topic 4's
            # (the last) version. Since 3 is drifted and 4 is canonical,
            # the final state is the canonical text again.
            assert out.read_text(encoding="utf-8") == "// shared canonical runner\n"

            # At least one output_path_conflict warning surfaced.
            assert any(w.category == "output_path_conflict" for w in summary.warnings)


class TestImageRegistryDoesNotDoubleWarn:
    """Image-path collisions continue to fire image_collision via
    ImageRegistry, and the new output_path_conflict channel does NOT
    fire for the same paths."""

    async def test_image_collision_only_in_image_registry(self, tmp_path):
        # Two topics, both with img/diagram.png but DIFFERENT content
        # (an ImageRegistry collision).
        topic_a = tmp_path / "topic_a"
        topic_b = tmp_path / "topic_b"
        (topic_a / "img").mkdir(parents=True)
        (topic_b / "img").mkdir(parents=True)
        img_a = topic_a / "img" / "diagram.png"
        img_b = topic_b / "img" / "diagram.png"
        img_a.write_bytes(b"PNG A")
        img_b.write_bytes(b"PNG B")

        image_registry = ImageRegistry()
        image_registry.register(img_a)
        image_registry.register(img_b)
        assert image_registry.has_collisions()

        # Now hypothetically copy both into the same output path. The
        # output registry SHOULD NOT register them at all.
        out = tmp_path / "out" / "img" / "diagram.png"
        async with PytestLocalOpsBackend() as backend:
            await backend.copy_file_to_output(_make_copy_data(img_a, out, tmp_path))
            await backend.copy_file_to_output(_make_copy_data(img_b, out, tmp_path))

            out_registry = backend.output_write_registry
            assert out_registry.entries == {}
            assert out_registry.total_conflicts == 0

            reporter = BuildReporter(QuietOutputFormatter())
            reporter.start_build(course_name="course", total_files=2)
            reporter.report_output_writes(out_registry)
            summary = reporter.finish_build()

            assert all(w.category != "output_path_conflict" for w in summary.warnings)


class TestJsonEndToEnd:
    def test_json_summary_carries_registry_data(self, tmp_path, capsys):
        src_a = tmp_path / "a.txt"
        src_b = tmp_path / "b.txt"
        src_a.write_text("same payload", encoding="utf-8")
        src_b.write_text("same payload", encoding="utf-8")
        out = tmp_path / "out.txt"

        # First, populate a registry via real copy_file_to_output calls.
        # We need a separate event loop here so we can drive the rest of
        # the assertions synchronously; pytest-asyncio handles this for
        # us when the test function is async — but capsys interacts with
        # stdout so keep the JSON section sync after the copies.
        registry = OutputWriteRegistry()
        # Two identical writes to the same output.
        registry.record_write(out, content_source=src_a, source=src_a)
        registry.record_write(out, content_source=src_b, source=src_b)

        reporter = BuildReporter(JSONOutputFormatter())
        reporter.start_build(course_name="json-course", total_files=2)
        reporter.report_output_writes(registry)
        summary: BuildSummary = reporter.finish_build()
        assert summary.output_dedup_count == 1

        captured = capsys.readouterr().out
        data = _json.loads(captured)
        assert data["output_dedup_count"] == 1
        assert data["output_conflicts"] == []
        assert data["output_large_file_collision_count"] == 0


class TestConcurrentMediatedWrites:
    """Sanity check that even with many concurrent copy_file_to_output
    awaits, the registry sees the right total events (the per-call
    record_write runs on the event loop, not the executor, so no real
    threading concern — but worth pinning behavior)."""

    async def test_many_concurrent_writes_count_correctly(self, tmp_path):
        import asyncio

        sources = []
        for i in range(20):
            src = tmp_path / f"src_{i}.txt"
            src.write_text("identical", encoding="utf-8")
            sources.append(src)

        out = tmp_path / "out.txt"
        async with PytestLocalOpsBackend() as backend:
            await asyncio.gather(
                *[backend.copy_file_to_output(_make_copy_data(s, out, tmp_path)) for s in sources]
            )

            # 1 first write + 19 dedups, regardless of scheduling order.
            registry = backend.output_write_registry
            assert registry.total_dedups == 19
            assert registry.total_conflicts == 0
            assert len(registry.entries) == 1
