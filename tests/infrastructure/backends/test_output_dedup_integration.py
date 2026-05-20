"""Integration tests for OutputWriteRegistry wiring in the backends.

These exercise the registry hooks end-to-end:

- :meth:`LocalOpsBackend.copy_file_to_output` (PR 2.2a) — pre-write
  registration, dedup-skip on identical content, last-writer-wins on
  conflict, image-path skip, missing-source no-register, and distinct
  output paths registering independently.
- :class:`SqliteBackend` cache-hit replay (PR 2.2a) — when a cached
  result is replayed to disk, the registry sees it and a second
  identical replay to the same output path dedups.
- :class:`SqliteBackend` worker readback (PR 2.2b) — after a worker
  subprocess writes its output and the orchestrator reads it back for
  DB caching, the registry sees the write so cross-worker output
  conflicts surface in the build summary.

Standalone registry semantics are covered by
``tests/core/test_output_write_registry.py``; the tests here only assert
the hooks' plumbing.
"""

import asyncio
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
from clm.infrastructure.database.job_queue import JobQueue
from clm.infrastructure.database.schema import init_database
from clm.infrastructure.messaging.base_classes import Payload, Result
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

    async def test_image_path_tracked_for_content_conflicts(self, tmp_path):
        # Image-path sources now flow through OutputWriteRegistry too so
        # content conflicts at the output path are detected (e.g. a
        # static ``img/X.png`` and a ``pu/X.pu``-rendered ``img/X.png``
        # writing to the same destination). ImageRegistry still records
        # the output path for the stray-file sweep.
        img_dir_a = tmp_path / "topic_a" / "img"
        img_dir_b = tmp_path / "topic_b" / "img"
        img_dir_a.mkdir(parents=True)
        img_dir_b.mkdir(parents=True)
        src_a = img_dir_a / "diagram.png"
        src_b = img_dir_b / "diagram.png"
        out = tmp_path / "out.png"
        src_a.write_bytes(b"PNG A")
        src_b.write_bytes(b"PNG B")

        async with PytestLocalOpsBackend() as backend:
            await backend.copy_file_to_output(_copy_data(src_a, out, tmp_path))
            await backend.copy_file_to_output(_copy_data(src_b, out, tmp_path))

            assert backend.output_write_registry.total_conflicts == 1
            assert out.resolve() in backend.image_registry.tracked_paths
            # Last-writer wins on disk.
            assert out.read_bytes() == b"PNG B"

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


class TestCopyDirGroupRegistry:
    """PR 2.2c: <dir-group> writes register per-file post-copy."""

    async def test_recursive_copy_registers_each_file(self, tmp_path):
        from clm.infrastructure.utils.copy_dir_group_data import CopyDirGroupData

        src_root = tmp_path / "src"
        (src_root / "sub").mkdir(parents=True)
        (src_root / "a.txt").write_text("alpha", encoding="utf-8")
        (src_root / "b.txt").write_text("beta", encoding="utf-8")
        (src_root / "sub" / "c.txt").write_text("gamma", encoding="utf-8")

        output_dir = tmp_path / "out"
        copy_data = CopyDirGroupData(
            name="grp",
            source_dirs=(src_root,),
            relative_paths=(Path("grp"),),
            lang="en",
            output_dir=output_dir,
            recursive=True,
        )

        async with PytestLocalOpsBackend() as backend:
            await backend.copy_dir_group_to_output(copy_data)

            entries = backend.output_write_registry.entries
            written = set(entries)
            expected_files = {
                (output_dir / "grp" / "a.txt").resolve(),
                (output_dir / "grp" / "b.txt").resolve(),
                (output_dir / "grp" / "sub" / "c.txt").resolve(),
            }
            assert expected_files <= written
            assert backend.output_write_registry.total_dedups == 0

    async def test_non_recursive_copy_registers_top_level_only(self, tmp_path):
        from clm.infrastructure.utils.copy_dir_group_data import CopyDirGroupData

        src_root = tmp_path / "src"
        (src_root / "sub").mkdir(parents=True)
        (src_root / "a.txt").write_text("alpha", encoding="utf-8")
        (src_root / "sub" / "c.txt").write_text("gamma", encoding="utf-8")

        output_dir = tmp_path / "out"
        copy_data = CopyDirGroupData(
            name="grp",
            source_dirs=(src_root,),
            relative_paths=(Path("grp"),),
            lang="en",
            output_dir=output_dir,
            recursive=False,
        )

        async with PytestLocalOpsBackend() as backend:
            await backend.copy_dir_group_to_output(copy_data)

            keys = set(backend.output_write_registry.entries)
            assert (output_dir / "grp" / "a.txt").resolve() in keys
            assert (output_dir / "grp" / "sub" / "c.txt").resolve() not in keys

    async def test_recursive_copy_tracks_image_paths_in_output_registry(self, tmp_path):
        # Image-path writes now go through both registries: ImageRegistry
        # for sweep tracking, OutputWriteRegistry for content-conflict
        # detection at the output destination.
        from clm.infrastructure.utils.copy_dir_group_data import CopyDirGroupData

        src_root = tmp_path / "src"
        (src_root / "img").mkdir(parents=True)
        (src_root / "a.txt").write_text("alpha", encoding="utf-8")
        (src_root / "img" / "diagram.png").write_bytes(b"PNG bytes")

        output_dir = tmp_path / "out"
        copy_data = CopyDirGroupData(
            name="grp",
            source_dirs=(src_root,),
            relative_paths=(Path("grp"),),
            lang="en",
            output_dir=output_dir,
            recursive=True,
        )

        async with PytestLocalOpsBackend() as backend:
            await backend.copy_dir_group_to_output(copy_data)

            keys = set(backend.output_write_registry.entries)
            img_out = (output_dir / "grp" / "img" / "diagram.png").resolve()
            assert (output_dir / "grp" / "a.txt").resolve() in keys
            assert img_out in keys
            assert img_out in backend.image_registry.tracked_paths

    async def test_two_overlapping_dir_groups_detect_conflict(self, tmp_path):
        from clm.infrastructure.utils.copy_dir_group_data import CopyDirGroupData

        src_a = tmp_path / "src_a"
        src_b = tmp_path / "src_b"
        src_a.mkdir()
        src_b.mkdir()
        (src_a / "shared.txt").write_text("version A", encoding="utf-8")
        (src_b / "shared.txt").write_text("version B", encoding="utf-8")

        output_dir = tmp_path / "out"
        cd_a = CopyDirGroupData(
            name="a",
            source_dirs=(src_a,),
            relative_paths=(Path("grp"),),
            lang="en",
            output_dir=output_dir,
            recursive=False,
        )
        cd_b = CopyDirGroupData(
            name="b",
            source_dirs=(src_b,),
            relative_paths=(Path("grp"),),
            lang="en",
            output_dir=output_dir,
            recursive=False,
        )

        async with PytestLocalOpsBackend() as backend:
            await backend.copy_dir_group_to_output(cd_a)
            await backend.copy_dir_group_to_output(cd_b)

            assert backend.output_write_registry.total_conflicts == 1
            conflicts = backend.output_write_registry.conflict_entries
            assert len(conflicts) == 1
            assert conflicts[0].first_writer_source == src_a / "shared.txt"
            assert conflicts[0].last_writer_source == src_b / "shared.txt"

    async def test_recursive_copy_ignores_pre_existing_output_files(self, tmp_path):
        # Regression test for issue #103: a recursive dir-group whose
        # output dir is shared with other pipelines (e.g. a top-level
        # `div/toplevel/<course>` include whose target_dir overlaps the
        # output root) must not register every pre-existing output file
        # as if the dir-group had written it. The misattribution showed
        # up as "Multiple writers" warnings naming a phantom source path
        # under the dir-group that did not exist on disk.
        from clm.infrastructure.utils.copy_dir_group_data import CopyDirGroupData

        src_root = tmp_path / "src"
        src_root.mkdir()
        # Source dir contains only top-level static files — no Folien/
        # or other subtree the slide pipeline writes to.
        (src_root / "README.md").write_text("readme", encoding="utf-8")
        (src_root / "setup.sh").write_text("#!/bin/sh", encoding="utf-8")

        output_dir = tmp_path / "out"
        target_dir = output_dir / "grp"
        # Simulate output files written by another pipeline (e.g. the
        # slide processor) before the dir-group copy runs.
        (target_dir / "Folien" / "Notebooks" / "Recording").mkdir(parents=True)
        phantom_output = target_dir / "Folien" / "Notebooks" / "Recording" / "lesson.ipynb"
        phantom_output.write_text("from-slide-pipeline", encoding="utf-8")

        copy_data = CopyDirGroupData(
            name="grp",
            source_dirs=(src_root,),
            relative_paths=(Path("grp"),),
            lang="en",
            output_dir=output_dir,
            recursive=True,
        )

        async with PytestLocalOpsBackend() as backend:
            await backend.copy_dir_group_to_output(copy_data)

            entries = backend.output_write_registry.entries
            # The dir-group's own static files should be registered…
            assert (target_dir / "README.md").resolve() in entries
            assert (target_dir / "setup.sh").resolve() in entries
            # …but the slide-pipeline output must NOT be re-registered
            # under the dir-group with a phantom source path.
            phantom_entry = backend.output_write_registry.get(phantom_output.resolve())
            phantom_source = src_root / "Folien" / "Notebooks" / "Recording" / "lesson.ipynb"
            if phantom_entry is not None:
                # If the phantom output was registered at all, it must
                # not be attributed to the nonexistent dir-group source.
                assert phantom_entry.first_writer_source != phantom_source
                assert phantom_entry.last_writer_source != phantom_source

    async def test_base_path_files_registered(self, tmp_path):
        from clm.infrastructure.utils.copy_dir_group_data import CopyDirGroupData

        base = tmp_path / "base"
        base.mkdir()
        (base / "README.md").write_text("hello", encoding="utf-8")
        (base / "VERSION").write_text("1.0", encoding="utf-8")

        output_dir = tmp_path / "out"
        copy_data = CopyDirGroupData(
            name="grp",
            source_dirs=(),
            relative_paths=(),
            lang="en",
            output_dir=output_dir,
            recursive=False,
            base_path=base,
        )

        async with PytestLocalOpsBackend() as backend:
            await backend.copy_dir_group_to_output(copy_data)

            keys = set(backend.output_write_registry.entries)
            assert (output_dir / "README.md").resolve() in keys
            assert (output_dir / "VERSION").resolve() in keys


@frozen
class _MockOp(Operation):
    service_name_value: str = "notebook-processor"

    @property
    def service_name(self) -> str:
        return self.service_name_value

    async def execute(self, backend, *args, **kwargs):
        pass


class _MockPayload(Payload):
    correlation_id: str = "test-correlation-id"
    input_file: str = "src/topic_a.py"
    input_file_name: str = "topic_a.py"
    output_file: str = "output/topic.ipynb"
    data: str = "test content"


class _MockResult(Result):
    """In-memory cached result; ``data`` is the bytes to replay."""

    data: bytes = b"cached payload"

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


class TestSqliteCacheHitReplayRegistry:
    """PR 2.2a: cache-hit replay path registers writes."""

    async def test_cache_hit_replay_registers_first_write(self, _sqlite_temp_db, _sqlite_workspace):
        backend = SqliteBackend(
            db_path=_sqlite_temp_db,
            workspace_path=_sqlite_workspace,
            ignore_db=False,
            incremental=False,
            skip_worker_check=True,
        )
        try:
            backend.db_manager = Mock()
            backend.db_manager.get_result.return_value = _MockResult(
                correlation_id="x",
                output_file="output/topic.ipynb",
                input_file="src/topic_a.py",
                content_hash="h1",
            )

            payload = _MockPayload()
            (_sqlite_workspace / "output").mkdir(parents=True, exist_ok=True)

            await backend.execute_operation(_MockOp(), payload)

            written = _sqlite_workspace / payload.output_file
            assert written.read_bytes() == b"cached payload"
            entry = backend.output_write_registry.get(written)
            assert entry is not None
            assert entry.first_writer_source == Path("src/topic_a.py")
        finally:
            await backend.shutdown()

    async def test_second_identical_cache_hit_dedups(self, _sqlite_temp_db, _sqlite_workspace):
        backend = SqliteBackend(
            db_path=_sqlite_temp_db,
            workspace_path=_sqlite_workspace,
            ignore_db=False,
            incremental=False,
            skip_worker_check=True,
        )
        try:
            backend.db_manager = Mock()
            backend.db_manager.get_result.return_value = _MockResult(
                correlation_id="x",
                output_file="output/topic.ipynb",
                input_file="src/topic_a.py",
                content_hash="h1",
            )
            (_sqlite_workspace / "output").mkdir(parents=True, exist_ok=True)

            await backend.execute_operation(_MockOp(), _MockPayload())
            written = _sqlite_workspace / "output" / "topic.ipynb"
            mtime_after_first = written.stat().st_mtime_ns

            # Second execute with same bytes but different source: identical
            # content → dedup → no second write.
            backend.db_manager.get_result.return_value = _MockResult(
                correlation_id="y",
                output_file="output/topic.ipynb",
                input_file="src/topic_b.py",
                content_hash="h1",
            )
            payload2 = _MockPayload()
            object.__setattr__(payload2, "input_file", "src/topic_b.py")
            time.sleep(0.05)
            await backend.execute_operation(_MockOp(), payload2)

            assert written.stat().st_mtime_ns == mtime_after_first
            assert backend.output_write_registry.total_dedups == 1
        finally:
            await backend.shutdown()


class TestSqliteWorkerReadbackRegistry:
    """PR 2.2b: completed-job readback path registers worker outputs."""

    async def test_worker_readback_registers_output(self, _sqlite_temp_db, _sqlite_workspace):
        backend = SqliteBackend(
            db_path=_sqlite_temp_db,
            workspace_path=_sqlite_workspace,
            ignore_db=False,
            incremental=False,
            skip_worker_check=True,
        )
        try:
            # Real db_manager mock so the cache-storage block runs and the
            # registry hook fires. get_result returns None so execute_operation
            # falls through to the worker-submission path (we want to exercise
            # the readback hook, not the cache-hit-replay hook).
            backend.db_manager = Mock()
            backend.db_manager.get_result.return_value = None

            payload = _MockPayload()
            await backend.execute_operation(_MockOp(), payload)
            job_id = next(iter(backend.active_jobs.keys()))

            # Simulate the worker writing its output, then completing the
            # job in the queue.
            output_path = _sqlite_workspace / payload.output_file
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("worker output", encoding="utf-8")

            async def _complete():
                await asyncio.sleep(0.1)
                jq = JobQueue(_sqlite_temp_db)
                try:
                    jq.update_job_status(job_id, "completed")
                finally:
                    jq.close()

            done = asyncio.create_task(_complete())
            assert await backend.wait_for_completion() is True
            await done

            entry = backend.output_write_registry.get(output_path)
            assert entry is not None
            assert entry.first_writer_source == Path("src/topic_a.py")
        finally:
            await backend.shutdown()


class TestSqliteCacheHitReplayHashAware:
    """Hash-aware skip in cache-replay (default-on since the D3 flip).

    When the destination on disk already holds the same bytes the cache
    replay would write (typically a leftover from a prior build), the
    write is skipped so the file's mtime is preserved and git's
    stat-cache stays valid.
    """

    async def test_skips_when_identical(self, _sqlite_temp_db, _sqlite_workspace):
        backend = SqliteBackend(
            db_path=_sqlite_temp_db,
            workspace_path=_sqlite_workspace,
            ignore_db=False,
            incremental=False,
            skip_worker_check=True,
        )
        try:
            backend.db_manager = Mock()
            backend.db_manager.get_result.return_value = _MockResult(
                correlation_id="x",
                output_file="output/topic.ipynb",
                input_file="src/topic_a.py",
                content_hash="h1",
            )

            output_path = _sqlite_workspace / "output" / "topic.ipynb"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"cached payload")
            mtime_before = output_path.stat().st_mtime_ns

            time.sleep(0.05)
            await backend.execute_operation(_MockOp(), _MockPayload())

            assert output_path.stat().st_mtime_ns == mtime_before
            # Registry still records the intent so the future stray-file
            # sweep knows this path was produced by the build.
            entry = backend.output_write_registry.get(output_path)
            assert entry is not None
        finally:
            await backend.shutdown()

    async def test_writes_when_content_differs(self, _sqlite_temp_db, _sqlite_workspace):
        backend = SqliteBackend(
            db_path=_sqlite_temp_db,
            workspace_path=_sqlite_workspace,
            ignore_db=False,
            incremental=False,
            skip_worker_check=True,
        )
        try:
            backend.db_manager = Mock()
            backend.db_manager.get_result.return_value = _MockResult(
                correlation_id="x",
                output_file="output/topic.ipynb",
                input_file="src/topic_a.py",
                content_hash="h1",
            )

            output_path = _sqlite_workspace / "output" / "topic.ipynb"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"stale content")

            await backend.execute_operation(_MockOp(), _MockPayload())

            assert output_path.read_bytes() == b"cached payload"
        finally:
            await backend.shutdown()

    async def test_writes_when_dest_missing(self, _sqlite_temp_db, _sqlite_workspace):
        backend = SqliteBackend(
            db_path=_sqlite_temp_db,
            workspace_path=_sqlite_workspace,
            ignore_db=False,
            incremental=False,
            skip_worker_check=True,
        )
        try:
            backend.db_manager = Mock()
            backend.db_manager.get_result.return_value = _MockResult(
                correlation_id="x",
                output_file="output/topic.ipynb",
                input_file="src/topic_a.py",
                content_hash="h1",
            )

            (_sqlite_workspace / "output").mkdir(parents=True, exist_ok=True)
            output_path = _sqlite_workspace / "output" / "topic.ipynb"
            assert not output_path.exists()

            await backend.execute_operation(_MockOp(), _MockPayload())

            assert output_path.read_bytes() == b"cached payload"
        finally:
            await backend.shutdown()
