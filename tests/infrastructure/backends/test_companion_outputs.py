"""Companion output files across the worker/host boundary (#928 phase 3).

The C++ code export writes a deck header and one file per workshop next to
the main output. The worker reports their names in the job result JSON and
the job-cache metadata; the host must (1) register them so the stray-file
sweep keeps them, on both the worker-readback and the job-cache-hit paths,
(2) store them with the cached result, and (3) write them back on a
database-cache replay. Each path is exercised here against a real jobs DB.
"""

import asyncio
import gc
import json
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock

import pytest
from attrs import frozen

from clm.core.messaging.base_classes import Payload
from clm.core.messaging.notebook_classes import NotebookResult
from clm.core.operation import Operation
from clm.infrastructure.backends.sqlite_backend import SqliteBackend, _companion_paths
from clm.infrastructure.database.job_queue import JobQueue
from clm.infrastructure.database.schema import init_database


@frozen
class _Op(Operation):
    @property
    def service_name(self) -> str:
        return "notebook-processor"

    async def execute(self, backend, *args, **kwargs):
        pass


class _DeckPayload(Payload):
    correlation_id: str = "cid"
    input_file: str = "slides/deck.cpp"
    input_file_name: str = "deck.cpp"
    output_file: str = "out/Cpp/Completed/01 Intro/01 Deck.cpp"
    data: str = "int main() {}"


@pytest.fixture
def temp_db():
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
def temp_workspace():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


def _backend(temp_db, temp_workspace, **kwargs) -> SqliteBackend:
    return SqliteBackend(
        db_path=temp_db,
        workspace_path=temp_workspace,
        ignore_db=False,
        incremental=False,
        skip_worker_check=True,
        **kwargs,
    )


COMPANIONS = ["01 Deck.hpp", "01 Deck_workshop_1.cpp"]


class TestCompanionPaths:
    def test_names_resolve_next_to_the_output(self):
        out = Path("/w/out/01 Deck.cpp")
        assert _companion_paths(out, {"companion_files": COMPANIONS}) == [
            Path("/w/out/01 Deck.hpp"),
            Path("/w/out/01 Deck_workshop_1.cpp"),
        ]

    def test_missing_or_empty_metadata_yields_nothing(self):
        out = Path("/w/out/01 Deck.cpp")
        assert _companion_paths(out, None) == []
        assert _companion_paths(out, {}) == []
        assert _companion_paths(out, {"companion_files": []}) == []

    def test_names_with_path_components_are_dropped(self):
        # A stored entry must never address a file outside the output dir.
        out = Path("/w/out/01 Deck.cpp")
        meta = {"companion_files": ["../evil.hpp", "sub/x.cpp", "ok.hpp", "", 3]}
        assert _companion_paths(out, meta) == [Path("/w/out/ok.hpp")]


@pytest.mark.asyncio
async def test_jobcache_hit_requires_every_companion_on_disk(temp_db, temp_workspace):
    """A cached entry whose companion file is missing is not a hit: the job
    is submitted again so the worker rewrites the whole file set."""
    backend = _backend(temp_db, temp_workspace)
    try:
        payload = _DeckPayload()
        queue = JobQueue(temp_db)
        try:
            queue.add_to_cache(
                payload.output_file,
                payload.content_hash(),
                {"format": "code", "companion_files": COMPANIONS},
            )
        finally:
            queue.close()
        output_path = temp_workspace / payload.output_file
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("main", encoding="utf-8")
        (output_path.parent / COMPANIONS[0]).write_text("header", encoding="utf-8")
        # The workshop file is missing.

        await backend.execute_operation(_Op(), payload)

        assert len(backend.active_jobs) == 1
    finally:
        backend.active_jobs.clear()
        await backend.shutdown()


@pytest.mark.asyncio
async def test_jobcache_hit_registers_companions_for_the_sweep(temp_db, temp_workspace):
    backend = _backend(temp_db, temp_workspace)
    try:
        payload = _DeckPayload()
        queue = JobQueue(temp_db)
        try:
            queue.add_to_cache(
                payload.output_file,
                payload.content_hash(),
                {"format": "code", "companion_files": COMPANIONS},
            )
        finally:
            queue.close()
        output_path = temp_workspace / payload.output_file
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("main", encoding="utf-8")
        for name in COMPANIONS:
            (output_path.parent / name).write_text(name, encoding="utf-8")

        await backend.execute_operation(_Op(), payload)

        assert len(backend.active_jobs) == 0
        entries = backend.output_write_registry.entries
        assert output_path in entries
        for name in COMPANIONS:
            assert output_path.parent / name in entries
    finally:
        backend.active_jobs.clear()
        await backend.shutdown()


@pytest.mark.asyncio
async def test_db_cache_replay_writes_and_registers_companions(temp_db, temp_workspace):
    backend = _backend(temp_db, temp_workspace)
    try:
        payload = _DeckPayload()
        backend.db_manager = Mock()
        backend.db_manager.get_result.return_value = NotebookResult(
            correlation_id="cid",
            output_file=payload.output_file,
            input_file=payload.input_file,
            content_hash=payload.content_hash(),
            result='#include "01 Deck.hpp"\n',
            output_metadata_tags=("completed", "cpp", "en", "code"),
            companion_files={
                "01 Deck.hpp": "#pragma once\n",
                "01 Deck_workshop_1.cpp": "int main() {}\n",
            },
        )

        await backend.execute_operation(_Op(), payload)

        assert len(backend.active_jobs) == 0
        output_path = temp_workspace / payload.output_file
        assert output_path.read_text(encoding="utf-8") == '#include "01 Deck.hpp"\n'
        header = output_path.parent / "01 Deck.hpp"
        workshop = output_path.parent / "01 Deck_workshop_1.cpp"
        assert header.read_bytes() == b"#pragma once\n"
        assert workshop.read_bytes() == b"int main() {}\n"
        entries = backend.output_write_registry.entries
        assert {output_path, header, workshop} <= set(entries)
        assert entries[header].first_writer_source == Path(payload.input_file)
    finally:
        backend.active_jobs.clear()
        await backend.shutdown()


@pytest.mark.asyncio
async def test_pre_phase3_cached_result_replays_without_companions(temp_db, temp_workspace):
    """A pickled NotebookResult from before the field existed has no
    ``companion_files`` in its ``__dict__``; replay must not blow up."""
    backend = _backend(temp_db, temp_workspace)
    try:
        payload = _DeckPayload()
        result = NotebookResult(
            correlation_id="cid",
            output_file=payload.output_file,
            input_file=payload.input_file,
            content_hash=payload.content_hash(),
            result="int main() {}\n",
            output_metadata_tags=("completed", "cpp", "en", "code"),
        )
        del result.__dict__["companion_files"]
        assert result.companion_bytes() == {}
        backend.db_manager = Mock()
        backend.db_manager.get_result.return_value = result

        await backend.execute_operation(_Op(), payload)

        output_path = temp_workspace / payload.output_file
        assert output_path.read_text(encoding="utf-8") == "int main() {}\n"
        assert list(output_path.parent.iterdir()) == [output_path]
    finally:
        backend.active_jobs.clear()
        await backend.shutdown()


@pytest.mark.asyncio
async def test_worker_readback_registers_and_caches_companions(temp_db, temp_workspace):
    """The worker-written file set is registered on completion, and the
    result stored in the database cache carries the companion texts."""
    backend = _backend(temp_db, temp_workspace)
    try:
        backend.db_manager = Mock()
        backend.db_manager.get_result.return_value = None
        payload = _DeckPayload()
        await backend.execute_operation(_Op(), payload)
        job_id = next(iter(backend.active_jobs))
        job_info = dict(backend.active_jobs[job_id])

        output_path = temp_workspace / payload.output_file
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text('#include "01 Deck.hpp"\n', encoding="utf-8")
        header = output_path.parent / "01 Deck.hpp"
        workshop = output_path.parent / "01 Deck_workshop_1.cpp"
        header.write_text("#pragma once\n", encoding="utf-8")
        workshop.write_text("int main() {}\n", encoding="utf-8")
        # A stale file from an earlier build that the worker did not report.
        stale = output_path.parent / "01 Deck_workshop_2.cpp"
        stale.write_text("stale\n", encoding="utf-8")

        async def _complete():
            await asyncio.sleep(0.1)
            queue = JobQueue(temp_db)
            try:
                queue.update_job_status(
                    job_id, "completed", result=json.dumps({"companion_files": COMPANIONS})
                )
            finally:
                queue.close()

        done = asyncio.create_task(_complete())
        assert await backend.wait_for_completion() is True
        await done

        entries = backend.output_write_registry.entries
        assert {output_path, header, workshop} <= set(entries)
        assert stale not in entries  # the sweep may remove it

        prepared = backend._prepare_result_for_cache(job_id, job_info, output_path)
        assert prepared is not None
        result, _content_hash, _cid = prepared
        assert isinstance(result, NotebookResult)
        assert result.result == '#include "01 Deck.hpp"\n'
        assert result.companion_files == {
            "01 Deck.hpp": "#pragma once\n",
            "01 Deck_workshop_1.cpp": "int main() {}\n",
        }
    finally:
        backend.active_jobs.clear()
        await backend.shutdown()
