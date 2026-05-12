"""Integration tests for OutputWriteRegistry wiring in LocalOpsBackend.

These exercise the registry hook in
:meth:`LocalOpsBackend.copy_file_to_output` end-to-end against the
filesystem: that a first write proceeds and registers, a second
byte-identical write from a different source dedups (no overwrite), a
second differing-content write registers a conflict and overwrites
(last-writer-wins), source paths under ``img/`` bypass the registry,
and a missing source never produces a registry entry.

The standalone registry semantics are covered by
``tests/core/test_output_write_registry.py``; the tests here only assert
the hook's plumbing.
"""

import time
from pathlib import Path

import pytest

from clm.infrastructure.backends.local_ops_backend import LocalOpsBackend
from clm.infrastructure.messaging.base_classes import Payload
from clm.infrastructure.operation import Operation
from clm.infrastructure.utils.copy_file_data import CopyFileData


class PytestLocalOpsBackend(LocalOpsBackend):
    async def execute_operation(self, operation: Operation, payload: Payload) -> None:
        pass

    async def wait_for_completion(self, all_submitted=None) -> bool:
        return True


def _copy_data(source: Path, out: Path, base: Path) -> CopyFileData:
    return CopyFileData(
        input_path=source,
        output_path=out,
        relative_input_path=source.relative_to(base),
    )


class TestCopyFileDedup:
    async def test_first_write_registers_and_copies(self, tmp_path):
        src = tmp_path / "src.txt"
        out = tmp_path / "out.txt"
        src.write_text("hello", encoding="utf-8")

        async with PytestLocalOpsBackend() as backend:
            await backend.copy_file_to_output(_copy_data(src, out, tmp_path))

            assert out.read_text(encoding="utf-8") == "hello"
            assert backend.output_write_registry.total_dedups == 0
            entry = backend.output_write_registry.get(out.resolve())
            assert entry is not None
            assert entry.first_writer_source == src

    async def test_second_identical_write_dedups(self, tmp_path):
        src_a = tmp_path / "a.txt"
        src_b = tmp_path / "b.txt"
        out = tmp_path / "out.txt"
        src_a.write_text("same", encoding="utf-8")
        src_b.write_text("same", encoding="utf-8")

        async with PytestLocalOpsBackend() as backend:
            await backend.copy_file_to_output(_copy_data(src_a, out, tmp_path))
            mtime_after_first = out.stat().st_mtime_ns
            # Sleep past filesystem timestamp resolution so a real second
            # write would visibly advance mtime.
            time.sleep(0.05)
            await backend.copy_file_to_output(_copy_data(src_b, out, tmp_path))

            assert out.stat().st_mtime_ns == mtime_after_first
            assert backend.output_write_registry.total_dedups == 1
            assert backend.output_write_registry.total_conflicts == 0

    async def test_second_differing_write_records_conflict(self, tmp_path):
        src_a = tmp_path / "a.txt"
        src_b = tmp_path / "b.txt"
        out = tmp_path / "out.txt"
        src_a.write_text("first", encoding="utf-8")
        src_b.write_text("second", encoding="utf-8")

        async with PytestLocalOpsBackend() as backend:
            await backend.copy_file_to_output(_copy_data(src_a, out, tmp_path))
            await backend.copy_file_to_output(_copy_data(src_b, out, tmp_path))

            assert out.read_text(encoding="utf-8") == "second"
            assert backend.output_write_registry.total_conflicts == 1
            entries = backend.output_write_registry.conflict_entries
            assert len(entries) == 1
            assert entries[0].first_writer_source == src_a
            assert entries[0].last_writer_source == src_b

    async def test_image_path_skipped(self, tmp_path):
        img_dir = tmp_path / "topic" / "img"
        img_dir.mkdir(parents=True)
        src = img_dir / "diagram.png"
        out = tmp_path / "out.png"
        src.write_bytes(b"PNG bytes")

        async with PytestLocalOpsBackend() as backend:
            await backend.copy_file_to_output(_copy_data(src, out, tmp_path))
            await backend.copy_file_to_output(_copy_data(src, out, tmp_path))

            assert backend.output_write_registry.entries == {}
            assert out.read_bytes() == b"PNG bytes"

    async def test_missing_source_not_registered(self, tmp_path):
        src = tmp_path / "missing.txt"
        out = tmp_path / "out.txt"

        async with PytestLocalOpsBackend() as backend:
            with pytest.raises(FileNotFoundError):
                await backend.copy_file_to_output(_copy_data(src, out, tmp_path))

            assert backend.output_write_registry.entries == {}

    async def test_distinct_output_paths_independent(self, tmp_path):
        src = tmp_path / "src.txt"
        out_a = tmp_path / "a" / "out.txt"
        out_b = tmp_path / "b" / "out.txt"
        src.write_text("payload", encoding="utf-8")

        async with PytestLocalOpsBackend() as backend:
            await backend.copy_file_to_output(_copy_data(src, out_a, tmp_path))
            await backend.copy_file_to_output(_copy_data(src, out_b, tmp_path))

            assert backend.output_write_registry.total_dedups == 0
            assert len(backend.output_write_registry.entries) == 2
