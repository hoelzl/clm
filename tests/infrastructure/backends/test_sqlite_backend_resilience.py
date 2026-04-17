"""Resilience tests for SqliteBackend.

Focus areas (lines that are not exercised by ``test_sqlite_backend.py``):

- ``_cleanup_dead_worker_jobs`` — orphan recovery from dead workers.
- ``wait_for_completion`` edge cases (all_submitted gating, missing job row,
  TimeoutError, failed job path through error categorizer + db_manager).
- ``cancel_jobs_for_file`` watch-mode cancellation.
- ``_perform_session_start_cleanup`` and ``_perform_build_end_cleanup``.
- ``_get_available_workers`` (activated path, no activation, pre-registered wait).
- ``_get_output_metadata`` for every job type.
- ``_extract_and_report_job_warnings`` happy path.
- ``_report_cached_issues`` when stored issues exist.

All jobs are driven by poking the SQLite tables directly rather than running
real workers. ``skip_worker_check=True`` so the availability gate doesn't
fire, except on the dedicated worker-availability tests.
"""

from __future__ import annotations

import asyncio
import gc
import json
import sqlite3
import tempfile
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from attrs import frozen

from clm.cli.build_data_classes import BuildError
from clm.infrastructure.backends.sqlite_backend import SqliteBackend
from clm.infrastructure.database.db_operations import DatabaseManager
from clm.infrastructure.database.job_queue import JobQueue
from clm.infrastructure.database.schema import init_database
from clm.infrastructure.messaging.base_classes import Payload
from clm.infrastructure.operation import Operation

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@frozen
class _MockOp(Operation):
    service_name_value: str = "notebook-processor"

    @property
    def service_name(self) -> str:
        return self.service_name_value

    async def execute(self, backend, *args, **kwargs):
        pass


class _MockPayload(Payload):
    correlation_id: str = "cid"
    input_file: str = "in.py"
    input_file_name: str = "in.py"
    output_file: str = "out.ipynb"
    data: str = "data"


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
            for suffix in ["-wal", "-shm"]:
                Path(str(db_path) + suffix).unlink(missing_ok=True)
            break
        except PermissionError:
            if attempt < 2:
                time.sleep(0.1)


@pytest.fixture
def temp_workspace():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


def _backend(db, ws, **kwargs) -> SqliteBackend:
    return SqliteBackend(
        db_path=db,
        workspace_path=ws,
        skip_worker_check=True,
        poll_interval=0.02,
        max_wait_for_completion_duration=5.0,
        **kwargs,
    )


def _seed_job_processing_with_dead_worker(db_path: Path) -> int:
    """Insert a worker row in 'dead' state and a job in 'processing' bound to it."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO workers (worker_type, container_id, status, parent_pid)
            VALUES ('notebook', 'dead-worker', 'dead', 1234)
            """
        )
        worker_id = cur.lastrowid
        cur = conn.execute(
            """
            INSERT INTO jobs (
                job_type, status, input_file, output_file, content_hash,
                payload, worker_id, started_at
            )
            VALUES ('notebook', 'processing', 'f.py', 'f.ipynb', 'h',
                    '{}', ?, CURRENT_TIMESTAMP)
            """,
            (worker_id,),
        )
        conn.commit()
        return cur.lastrowid  # type: ignore[return-value]
    finally:
        conn.close()


_container_id_counter = [0]


def _seed_activated_worker(db_path: Path, worker_type: str = "notebook") -> int:
    _container_id_counter[0] += 1
    cid = f"live-{worker_type}-{_container_id_counter[0]}"
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO workers (worker_type, container_id, status, last_heartbeat)
            VALUES (?, ?, 'idle', CURRENT_TIMESTAMP)
            """,
            (worker_type, cid),
        )
        conn.commit()
        return cur.lastrowid  # type: ignore[return-value]
    finally:
        conn.close()


def _seed_created_worker(db_path: Path, worker_type: str = "notebook") -> int:
    _container_id_counter[0] += 1
    cid = f"pending-{worker_type}-{_container_id_counter[0]}"
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO workers (worker_type, container_id, status)
            VALUES (?, ?, 'created')
            """,
            (worker_type, cid),
        )
        conn.commit()
        return cur.lastrowid  # type: ignore[return-value]
    finally:
        conn.close()


class _StubReporter:
    """Minimal BuildReporter-shaped double (has every method backend calls)."""

    def __init__(self):
        self.errors = []
        self.warnings = []
        self.cache_hits = []
        self.started = []
        self.completed = []
        self.progress_updates = []

    def on_progress_update(self, update):
        self.progress_updates.append(update)

    def report_cache_hit(self, file_path, job_type):
        self.cache_hits.append((file_path, job_type))

    def report_file_started(self, file_path, job_type, job_id=None):
        self.started.append((file_path, job_type, job_id))

    def report_file_completed(self, file_path, job_type, job_id=None, success=True):
        self.completed.append((file_path, job_type, job_id, success))

    def report_error(self, error):
        self.errors.append(error)

    def report_warning(self, warning):
        self.warnings.append(warning)


# ---------------------------------------------------------------------------
# _cleanup_dead_worker_jobs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_dead_worker_jobs_resets_stuck_jobs(temp_db, temp_workspace):
    job_id = _seed_job_processing_with_dead_worker(temp_db)
    backend = _backend(temp_db, temp_workspace)
    try:
        count = backend._cleanup_dead_worker_jobs()
        assert count == 1

        # DB state: job reset to pending with worker_id NULL.
        conn = sqlite3.connect(temp_db)
        try:
            row = conn.execute(
                "SELECT status, worker_id, started_at FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        finally:
            conn.close()
        assert row == ("pending", None, None)
    finally:
        backend.active_jobs.clear()
        await backend.shutdown()


@pytest.mark.asyncio
async def test_cleanup_dead_worker_jobs_no_stuck_is_noop(temp_db, temp_workspace):
    backend = _backend(temp_db, temp_workspace)
    try:
        assert backend._cleanup_dead_worker_jobs() == 0
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_cleanup_dead_worker_jobs_handles_sql_error(temp_db, temp_workspace):
    backend = _backend(temp_db, temp_workspace)
    try:
        # Patch _get_conn to raise on execute so the except-branch fires.
        with patch.object(backend.job_queue, "_get_conn", side_effect=RuntimeError("db gone")):
            assert backend._cleanup_dead_worker_jobs() == 0
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_cleanup_dead_worker_jobs_no_queue_returns_zero(temp_db, temp_workspace):
    backend = _backend(temp_db, temp_workspace)
    try:
        backend.job_queue = None  # type: ignore[assignment]
        assert backend._cleanup_dead_worker_jobs() == 0
    finally:
        pass  # shutdown will try to use job_queue; bypass.


# ---------------------------------------------------------------------------
# wait_for_completion edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_for_completion_honours_all_submitted_gate(temp_db, temp_workspace):
    """With no active jobs, returns True immediately if all_submitted is set
    (or None); waits while empty otherwise."""
    backend = _backend(temp_db, temp_workspace)
    try:
        evt = asyncio.Event()

        # Submit a job *after* wait_for_completion starts polling.
        async def submit_later():
            await asyncio.sleep(0.05)
            await backend.execute_operation(_MockOp(), _MockPayload())
            job_id = next(iter(backend.active_jobs))
            await asyncio.sleep(0.05)
            # Mark it completed.
            jq = JobQueue(temp_db)
            try:
                jq.update_job_status(job_id, "completed")
            finally:
                jq.close()
            evt.set()

        submitter = asyncio.create_task(submit_later())
        result = await backend.wait_for_completion(all_submitted=evt)
        await submitter
        assert result is True
    finally:
        backend.active_jobs.clear()
        await backend.shutdown()


@pytest.mark.asyncio
async def test_wait_for_completion_missing_job_row_treated_as_done(temp_db, temp_workspace):
    """When a job disappears from the DB mid-wait, it is logged + removed."""
    backend = _backend(temp_db, temp_workspace)
    try:
        await backend.execute_operation(_MockOp(), _MockPayload())
        job_id = next(iter(backend.active_jobs))

        # Delete the row before the next poll.
        conn = sqlite3.connect(temp_db)
        try:
            conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            conn.commit()
        finally:
            conn.close()

        result = await backend.wait_for_completion()
        # All "active" jobs drained; wait returns successfully.
        assert result is True
        assert len(backend.active_jobs) == 0
    finally:
        backend.active_jobs.clear()
        await backend.shutdown()


@pytest.mark.asyncio
async def test_wait_for_completion_timeout(temp_db, temp_workspace):
    backend = _backend(temp_db, temp_workspace)
    backend.max_wait_for_completion_duration = 0.1
    try:
        await backend.execute_operation(_MockOp(), _MockPayload())
        with pytest.raises(TimeoutError):
            await backend.wait_for_completion()
    finally:
        backend.active_jobs.clear()
        await backend.shutdown()


@pytest.mark.asyncio
async def test_wait_for_completion_failed_job_reports_error(temp_db, temp_workspace, tmp_path):
    """A failed notebook job with a user-type error is stored in db_manager."""
    cache_db = tmp_path / "cache.db"

    backend = _backend(temp_db, temp_workspace)
    backend.db_manager = DatabaseManager(cache_db)
    backend.db_manager.__enter__()
    try:
        await backend.execute_operation(_MockOp(), _MockPayload())
        job_id = next(iter(backend.active_jobs))

        # Mark failed with a notebook-style syntax error so the categorizer
        # tags it as "user" and db_manager.store_error is invoked.
        jq = JobQueue(temp_db)
        try:
            jq.update_job_status(job_id, "failed", error="SyntaxError: invalid syntax")
        finally:
            jq.close()

        result = await backend.wait_for_completion()
        assert result is False  # failed_jobs non-empty → False
    finally:
        backend.active_jobs.clear()
        backend.db_manager.__exit__(None, None, None)
        await backend.shutdown()


# ---------------------------------------------------------------------------
# cancel_jobs_for_file (watch mode)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_jobs_for_file_removes_from_active(temp_db, temp_workspace):
    backend = _backend(temp_db, temp_workspace)
    try:
        await backend.execute_operation(_MockOp(), _MockPayload(input_file="a.py"))
        await backend.execute_operation(
            _MockOp(), _MockPayload(input_file="a.py", output_file="a.html")
        )
        assert len(backend.active_jobs) == 2

        count = await backend.cancel_jobs_for_file(Path("a.py"))
        assert count == 2
        assert backend.active_jobs == {}
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_cancel_jobs_for_file_no_queue_returns_zero(temp_db, temp_workspace):
    backend = _backend(temp_db, temp_workspace)
    try:
        backend.job_queue = None  # type: ignore[assignment]
        assert await backend.cancel_jobs_for_file(Path("a.py")) == 0
    finally:
        pass


# ---------------------------------------------------------------------------
# _perform_session_start_cleanup / _perform_build_end_cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_start_cleanup_resets_hung(temp_db, temp_workspace, caplog):
    # Seed a hung job: started_at older than the timeout.
    conn = sqlite3.connect(temp_db)
    try:
        conn.execute(
            """
            INSERT INTO jobs (
                job_type, status, input_file, output_file, content_hash,
                payload, started_at
            ) VALUES ('notebook', 'processing', 'h.py', 'h.ipynb', 'x', '{}',
                      datetime('now', '-1 hour'))
            """
        )
        conn.commit()
    finally:
        conn.close()

    backend = _backend(temp_db, temp_workspace)
    try:
        import logging as _logging

        with caplog.at_level(_logging.INFO, logger="clm.infrastructure.backends.sqlite_backend"):
            backend._perform_session_start_cleanup()
        assert any("hung job" in rec.message for rec in caplog.records)
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_session_start_cleanup_handles_exception(temp_db, temp_workspace):
    backend = _backend(temp_db, temp_workspace)
    try:
        with patch.object(backend.job_queue, "reset_hung_jobs", side_effect=RuntimeError("boom")):
            # Should not raise.
            backend._perform_session_start_cleanup()
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_build_end_cleanup_prunes_and_vacuums(temp_db, temp_workspace, tmp_path, monkeypatch):
    # Point config cache_db_path at tmp_path so ExecutedNotebookCache works.
    from clm.infrastructure import config as config_mod

    monkeypatch.setattr(
        config_mod,
        "_config",
        None,
    )
    monkeypatch.setenv("CLM_RETENTION__AUTO_VACUUM_AFTER_CLEANUP", "true")
    monkeypatch.setenv("CLM_PATHS__CACHE_DB_PATH", str(tmp_path / "cache.db"))

    cache_db = tmp_path / "cache.db"
    backend = _backend(temp_db, temp_workspace)
    backend.db_manager = DatabaseManager(cache_db)
    backend.db_manager.__enter__()
    try:
        # Should not raise — exercises both job_queue + db_manager cleanup
        # and the vacuum branch.
        backend._perform_build_end_cleanup()
    finally:
        backend.db_manager.__exit__(None, None, None)
        await backend.shutdown()
        # Reset the cached config.
        config_mod._config = None


@pytest.mark.asyncio
async def test_build_end_cleanup_handles_exception(temp_db, temp_workspace):
    backend = _backend(temp_db, temp_workspace)
    try:
        with patch.object(backend.job_queue, "cleanup_all", side_effect=RuntimeError("nope")):
            # Top-level except swallows the error.
            backend._perform_build_end_cleanup()
    finally:
        await backend.shutdown()


# ---------------------------------------------------------------------------
# _get_available_workers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_available_workers_counts_activated(temp_db, temp_workspace):
    _seed_activated_worker(temp_db, "notebook")
    _seed_activated_worker(temp_db, "notebook")
    _seed_activated_worker(temp_db, "plantuml")  # different type

    backend = _backend(temp_db, temp_workspace)
    try:
        assert backend._get_available_workers("notebook") == 2
        assert backend._get_available_workers("plantuml") == 1
        # No drawio workers — with wait_for_activation=False to skip the 30s sleep.
        assert backend._get_available_workers("drawio", wait_for_activation=False) == 0
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_get_available_workers_waits_for_pre_registered(temp_db, temp_workspace):
    """A 'created' worker is pre-registered; the backend polls until it activates."""
    _seed_created_worker(temp_db, "notebook")

    backend = _backend(temp_db, temp_workspace)
    try:
        # Activate the worker after a short delay (from another thread).
        import threading

        def activate_later():
            time.sleep(0.1)
            conn = sqlite3.connect(temp_db)
            try:
                conn.execute(
                    "UPDATE workers SET status='idle', "
                    "last_heartbeat=CURRENT_TIMESTAMP WHERE worker_type='notebook'"
                )
                conn.commit()
            finally:
                conn.close()

        thread = threading.Thread(target=activate_later)
        thread.start()
        try:
            # Shorten module-level sleeps so the test finishes fast.
            real_sleep = time.sleep

            def short_sleep(*_a, **_kw):
                real_sleep(0.02)

            with patch(
                "clm.infrastructure.backends.sqlite_backend.time.sleep",
                side_effect=short_sleep,
            ):
                count = backend._get_available_workers("notebook")
            assert count >= 1
        finally:
            thread.join()
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_get_available_workers_no_queue(temp_db, temp_workspace):
    backend = _backend(temp_db, temp_workspace)
    try:
        backend.job_queue = None  # type: ignore[assignment]
        assert backend._get_available_workers("notebook") == 0
    finally:
        pass


# ---------------------------------------------------------------------------
# _get_output_metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_output_metadata_all_types(temp_db, temp_workspace):
    backend = _backend(temp_db, temp_workspace)
    try:
        assert backend._get_output_metadata(
            "notebook",
            {
                "kind": "completed",
                "prog_lang": "python",
                "language": "en",
                "format": "notebook",
            },
        ) == str(("completed", "python", "en", "notebook"))

        assert backend._get_output_metadata("plantuml", {"output_format": "svg"}) == "svg"
        assert backend._get_output_metadata("drawio", {"output_format": "png"}) == "png"

        jl_meta = backend._get_output_metadata(
            "jupyterlite",
            {
                "target_name": "playground",
                "language": "en",
                "kinds": ["completed", "code-along"],
                "kernel": "pyodide",
            },
        )
        assert jl_meta == "jupyterlite:playground:en:code-along+completed:pyodide"

        # Unknown job type → empty string.
        assert backend._get_output_metadata("unknown", {}) == ""
    finally:
        await backend.shutdown()


# ---------------------------------------------------------------------------
# _extract_and_report_job_warnings and _report_cached_issues
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_and_report_job_warnings_happy_path(temp_db, temp_workspace, tmp_path):
    cache_db = tmp_path / "cache.db"
    backend = _backend(temp_db, temp_workspace, build_reporter=_StubReporter())
    backend.db_manager = DatabaseManager(cache_db)
    backend.db_manager.__enter__()

    try:
        # Submit + immediately stash a result JSON with warnings.
        await backend.execute_operation(_MockOp(), _MockPayload())
        job_id = next(iter(backend.active_jobs))

        result_payload = {
            "warnings": [
                {
                    "category": "duplicate",
                    "message": "duplicate topic",
                    "severity": "high",
                }
            ]
        }
        conn = sqlite3.connect(temp_db)
        try:
            conn.execute(
                "UPDATE jobs SET result = ? WHERE id = ?",
                (json.dumps(result_payload), job_id),
            )
            conn.commit()
        finally:
            conn.close()

        backend._extract_and_report_job_warnings(job_id, backend.active_jobs[job_id])
        assert len(backend.build_reporter.warnings) == 1
        assert backend.build_reporter.warnings[0].category == "duplicate"
    finally:
        backend.active_jobs.clear()
        backend.db_manager.__exit__(None, None, None)
        await backend.shutdown()


@pytest.mark.asyncio
async def test_extract_and_report_job_warnings_no_row(temp_db, temp_workspace):
    backend = _backend(temp_db, temp_workspace)
    try:
        # Returns silently when no row found.
        backend._extract_and_report_job_warnings(
            999999, {"input_file": "x.py", "job_type": "notebook"}
        )
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_report_cached_issues_reports_stored_errors_and_warnings(
    temp_db, temp_workspace, tmp_path
):
    cache_db = tmp_path / "cache.db"
    backend = _backend(temp_db, temp_workspace, build_reporter=_StubReporter())
    backend.db_manager = DatabaseManager(cache_db)
    backend.db_manager.__enter__()
    try:
        from clm.cli.build_data_classes import BuildWarning

        backend.db_manager.store_error(
            file_path="f.py",
            content_hash="hh",
            output_metadata="('x','python','en','notebook')",
            error=BuildError(
                error_type="user",
                category="c",
                severity="error",
                file_path="f.py",
                message="m",
                actionable_guidance="fix",
            ),
        )
        backend.db_manager.store_warning(
            file_path="f.py",
            content_hash="hh",
            output_metadata="('x','python','en','notebook')",
            warning=BuildWarning(category="c", message="w", severity="low"),
        )

        backend._report_cached_issues("f.py", "hh", "('x','python','en','notebook')")
        assert len(backend.build_reporter.errors) == 1
        assert backend.build_reporter.errors[0].details.get("from_cache") is True
        assert len(backend.build_reporter.warnings) == 1
    finally:
        backend.db_manager.__exit__(None, None, None)
        await backend.shutdown()


# ---------------------------------------------------------------------------
# copy_dir_group_to_output warning forwarding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_copy_dir_group_forwards_warnings_to_reporter(temp_db, temp_workspace, tmp_path):
    """When ``LocalOpsBackend.copy_dir_group_to_output`` returns warnings,
    they must be forwarded to the build reporter.

    Exercised by pointing the dir group at a non-existent source dir.
    """
    from clm.infrastructure.utils.copy_dir_group_data import CopyDirGroupData

    backend = _backend(temp_db, temp_workspace, build_reporter=_StubReporter())
    try:
        missing = tmp_path / "does-not-exist"
        out = tmp_path / "out"
        out.mkdir()
        copy_data = CopyDirGroupData(
            name="missing",
            source_dirs=(missing,),
            relative_paths=(Path("."),),
            output_dir=out,
            lang="en",
        )
        result = await backend.copy_dir_group_to_output(copy_data)
        # Warnings forwarded; same list returned.
        assert len(result) == len(backend.build_reporter.warnings)
    finally:
        await backend.shutdown()


# ---------------------------------------------------------------------------
# Incremental copy_file_to_output skip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_incremental_copy_skips_existing_output(temp_db, temp_workspace, tmp_path):
    from clm.infrastructure.utils.copy_file_data import CopyFileData

    src = tmp_path / "src.txt"
    src.write_text("hello")
    dest = tmp_path / "out" / "src.txt"
    dest.parent.mkdir()
    dest.write_text("already here")

    backend = _backend(temp_db, temp_workspace)
    backend.incremental = True
    try:
        data = CopyFileData(
            input_path=src,
            output_path=dest,
            relative_input_path=Path("src.txt"),
        )
        await backend.copy_file_to_output(data)
        # File content unchanged — incremental skipped the copy.
        assert dest.read_text() == "already here"
    finally:
        await backend.shutdown()
