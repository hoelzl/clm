"""Integration tests for the git-friendly hash-aware-writes feature (PR1).

These exercise the cross-build mtime-preservation property through both
write call sites that PR1 affects:

- :meth:`LocalOpsBackend.copy_file_to_output` — used for plain file
  copies (data files, asset files, dir-group leaf files).
- :class:`SqliteBackend` cache-hit replay — used for processed worker
  outputs (notebooks, plantuml, drawio) on rebuilds.

The shape of each test is: do a "build 1" that populates the output
tree, snapshot every output file's mtime_ns + size, then do a "build
2" through the same code path with ``CLM_HASH_AWARE_WRITES=1`` set,
and assert that unchanged files keep their mtime — the signal git
relies on to keep its stat-cache valid.

Mtime granularity on NTFS is 100ns; the tests sleep 50ms between
builds so a real second write would visibly advance the timestamp.

These run in the fast suite. They are integration-flavored (cross
two-build flow, two backend instances, real filesystem) but they
neither spin up workers nor invoke the CLI, so they complete in well
under a second and provide useful pre-commit signal.
"""

from __future__ import annotations

import gc
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock

import pytest
from attrs import frozen

from clm.infrastructure.backends.local_ops_backend import LocalOpsBackend
from clm.infrastructure.backends.sqlite_backend import SqliteBackend
from clm.infrastructure.database.schema import init_database
from clm.infrastructure.messaging.base_classes import Payload, Result
from clm.infrastructure.operation import Operation
from clm.infrastructure.utils.copy_file_data import CopyFileData


class _Backend(LocalOpsBackend):
    async def execute_operation(self, operation, payload):
        pass

    async def wait_for_completion(self, all_submitted=None) -> bool:
        return True


def _copy_data(source: Path, out: Path, base: Path) -> CopyFileData:
    return CopyFileData(
        input_path=source,
        output_path=out,
        relative_input_path=source.relative_to(base),
    )


def _snapshot(paths: list[Path]) -> dict[Path, tuple[int, int]]:
    """Return {path: (size, mtime_ns)} for every existing path."""
    return {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in paths if p.exists()}


class TestCopyFileToOutputMtimePreservation:
    """Site 1: ``LocalOpsBackend.copy_file_to_output``. Two consecutive
    "builds" that copy the same source files to the same outputs — with
    the flag on, the second pass preserves mtimes."""

    async def test_flag_off_rewrites_every_file(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CLM_HASH_AWARE_WRITES", raising=False)
        sources = []
        outs = []
        for i in range(8):
            src = tmp_path / f"src_{i}.txt"
            out = tmp_path / "out" / f"out_{i}.txt"
            src.write_bytes(f"content {i}".encode())
            sources.append(src)
            outs.append(out)

        async with _Backend() as build1:
            for src, out in zip(sources, outs, strict=True):
                await build1.copy_file_to_output(_copy_data(src, out, tmp_path))
        before = _snapshot(outs)

        time.sleep(0.05)

        async with _Backend() as build2:
            for src, out in zip(sources, outs, strict=True):
                await build2.copy_file_to_output(_copy_data(src, out, tmp_path))
        after = _snapshot(outs)

        # Every file's mtime advanced — no hash-aware skip in play.
        assert all(after[p][1] != before[p][1] for p in outs)

    async def test_flag_on_preserves_unchanged_files(self, tmp_path, monkeypatch):
        sources = []
        outs = []
        for i in range(8):
            src = tmp_path / f"src_{i}.txt"
            out = tmp_path / "out" / f"out_{i}.txt"
            src.write_bytes(f"content {i}".encode())
            sources.append(src)
            outs.append(out)

        # Build 1 populates the output tree (flag value irrelevant here —
        # dest doesn't exist yet, so the skip path can't fire).
        monkeypatch.setenv("CLM_HASH_AWARE_WRITES", "1")
        async with _Backend() as build1:
            for src, out in zip(sources, outs, strict=True):
                await build1.copy_file_to_output(_copy_data(src, out, tmp_path))
        before = _snapshot(outs)

        time.sleep(0.05)

        # Build 2 — every dest already holds identical content → all skips.
        async with _Backend() as build2:
            for src, out in zip(sources, outs, strict=True):
                await build2.copy_file_to_output(_copy_data(src, out, tmp_path))
        after = _snapshot(outs)

        assert all(after[p] == before[p] for p in outs), (
            f"expected every file's (size, mtime_ns) preserved, got diffs at "
            f"{[p for p in outs if after[p] != before[p]]}"
        )

    async def test_flag_on_rewrites_only_modified_source(self, tmp_path, monkeypatch):
        sources = []
        outs = []
        for i in range(5):
            src = tmp_path / f"src_{i}.txt"
            out = tmp_path / "out" / f"out_{i}.txt"
            src.write_bytes(f"content {i}".encode())
            sources.append(src)
            outs.append(out)

        monkeypatch.setenv("CLM_HASH_AWARE_WRITES", "1")
        async with _Backend() as build1:
            for src, out in zip(sources, outs, strict=True):
                await build1.copy_file_to_output(_copy_data(src, out, tmp_path))
        before = _snapshot(outs)

        time.sleep(0.05)

        # Modify just one source — its output must get a fresh mtime,
        # the others must stay frozen.
        sources[2].write_bytes(b"genuinely new content for #2")

        async with _Backend() as build2:
            for src, out in zip(sources, outs, strict=True):
                await build2.copy_file_to_output(_copy_data(src, out, tmp_path))
        after = _snapshot(outs)

        modified_out = outs[2]
        assert after[modified_out][1] != before[modified_out][1]
        assert after[modified_out][0] != before[modified_out][0]  # size also differs
        for i, out in enumerate(outs):
            if i == 2:
                continue
            assert after[out] == before[out], f"unchanged file {out} was rewritten"


@frozen
class _MockOp(Operation):
    @property
    def service_name(self) -> str:
        return "notebook-processor"

    async def execute(self, backend, *args, **kwargs):
        pass


class _MockPayload(Payload):
    correlation_id: str = "cid"
    input_file: str = "src/topic.py"
    input_file_name: str = "topic.py"
    output_file: str = "output/topic.ipynb"
    data: str = ""


class _MockResult(Result):
    data: bytes = b"cached notebook bytes"

    def result_bytes(self) -> bytes:
        return self.data

    def output_metadata(self) -> str:
        return "default"


@pytest.fixture
def _sqlite_temp_db():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = Path(f.name)
    init_database(db_path)
    yield db_path
    gc.collect()
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    except Exception:
        pass
    for attempt in range(3):
        try:
            db_path.unlink(missing_ok=True)
            for suffix in ("-wal", "-shm"):
                Path(str(db_path) + suffix).unlink(missing_ok=True)
            break
        except PermissionError:
            if attempt < 2:
                time.sleep(0.1)


@pytest.fixture
def _sqlite_workspace():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


def _new_sqlite_backend(db_path: Path, workspace: Path) -> SqliteBackend:
    backend = SqliteBackend(
        db_path=db_path,
        workspace_path=workspace,
        ignore_db=False,
        incremental=False,
        skip_worker_check=True,
    )
    backend.db_manager = Mock()
    backend.db_manager.get_result.return_value = _MockResult(
        correlation_id="cid",
        output_file="output/topic.ipynb",
        input_file="src/topic.py",
        content_hash="h1",
    )
    return backend


class TestSqliteCacheReplayMtimePreservation:
    """Site 3: ``SqliteBackend`` cache-hit replay. The hot path for
    rebuilds — every cached notebook/plantuml/drawio output replays
    through here. Hash-aware skip must preserve mtime on the second
    build."""

    async def test_flag_on_preserves_mtime_when_identical(
        self, _sqlite_temp_db, _sqlite_workspace, monkeypatch
    ):
        monkeypatch.setenv("CLM_HASH_AWARE_WRITES", "1")

        (_sqlite_workspace / "output").mkdir(parents=True, exist_ok=True)
        output_path = _sqlite_workspace / "output" / "topic.ipynb"

        # Build 1.
        build1 = _new_sqlite_backend(_sqlite_temp_db, _sqlite_workspace)
        try:
            await build1.execute_operation(_MockOp(), _MockPayload())
        finally:
            await build1.shutdown()
        before = (output_path.stat().st_size, output_path.stat().st_mtime_ns)

        time.sleep(0.05)

        # Build 2 — fresh backend (fresh registry); dest already has
        # the cached bytes → hash-aware skip → mtime preserved.
        build2 = _new_sqlite_backend(_sqlite_temp_db, _sqlite_workspace)
        try:
            await build2.execute_operation(_MockOp(), _MockPayload())
        finally:
            await build2.shutdown()
        after = (output_path.stat().st_size, output_path.stat().st_mtime_ns)

        assert after == before

    async def test_flag_off_rewrites_even_when_identical(
        self, _sqlite_temp_db, _sqlite_workspace, monkeypatch
    ):
        monkeypatch.delenv("CLM_HASH_AWARE_WRITES", raising=False)

        (_sqlite_workspace / "output").mkdir(parents=True, exist_ok=True)
        output_path = _sqlite_workspace / "output" / "topic.ipynb"

        build1 = _new_sqlite_backend(_sqlite_temp_db, _sqlite_workspace)
        try:
            await build1.execute_operation(_MockOp(), _MockPayload())
        finally:
            await build1.shutdown()
        mtime_before = output_path.stat().st_mtime_ns

        time.sleep(0.05)

        build2 = _new_sqlite_backend(_sqlite_temp_db, _sqlite_workspace)
        try:
            await build2.execute_operation(_MockOp(), _MockPayload())
        finally:
            await build2.shutdown()

        assert output_path.stat().st_mtime_ns != mtime_before
