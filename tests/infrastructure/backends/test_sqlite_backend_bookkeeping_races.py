"""Regression tests for issue #917: job/worker bookkeeping races under load.

Three independent defects in the SQLite backend's bookkeeping showed up on a
loaded multi-worker Windows host, all filed together as #917:

1. **Instant completions were dropped by the progress tracker.** A submitted
   job was registered in ``active_jobs`` (inside the shielded submit task) and
   in the ``ProgressTracker`` (after the caller's ``await shield(...)``
   resumed) on two *different* event-loop turns. The completion poll loop
   could run in between, retire a job that had already completed, and call
   ``job_completed`` for a job the tracker had never seen — one warning per
   job and a progress count that stayed short for the rest of the stage.

2. **A healthy pool looked empty once every worker was mid-job.** The
   availability gate required ``workers.last_heartbeat`` under 30 s old, but
   busy workers do not refresh it mid-job (the health monitor documents and
   tolerates exactly this). When all workers of a type were busy for longer
   than 30 s — routine for executed notebooks, worse under load — the next
   submission raised "No workers available" and aborted the build.

3. **Cache-DB bookkeeping writes ran on the event loop and were lossy.**
   ``clear_issues`` / ``store_warning`` / ``store_error`` ran inline in the
   poll loop, each able to block the loop for the full SQLite busy timeout
   under contention, and the background result-cache writer gave up on the
   first ``database is locked``, silently dropping the cache entry (so the
   next build re-executed that notebook).

These tests pin the invariants that eliminate each root cause.
"""

from __future__ import annotations

import asyncio
import gc
import json
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from attrs import frozen

from clm.core.build_data_classes import BuildWarning
from clm.core.messaging.base_classes import Payload
from clm.core.operation import Operation
from clm.infrastructure.backends import sqlite_backend as sqlite_backend_module
from clm.infrastructure.backends.sqlite_backend import SqliteBackend
from clm.infrastructure.database import busy_retry
from clm.infrastructure.database.db_operations import DatabaseManager
from clm.infrastructure.database.job_queue import JobQueue
from clm.infrastructure.database.schema import init_database

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


class _StubReporter:
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.cache_hits = []
        self.started = []
        self.completed = []
        self.progress_updates = []

    def on_progress_update(self, update):
        self.progress_updates.append(update)

    def report_cache_hit(self, file_path, job_type, detail=None):
        self.cache_hits.append((file_path, job_type))

    def report_file_started(self, file_path, job_type, job_id=None):
        self.started.append((file_path, job_type, job_id))

    def report_file_completed(self, file_path, job_type, job_id=None, success=True):
        self.completed.append((file_path, job_type, job_id, success))

    def report_error(self, error):
        self.errors.append(error)

    def report_warning(self, warning):
        self.warnings.append(warning)


def _backend(db, ws, **kwargs) -> SqliteBackend:
    kwargs.setdefault("skip_worker_check", True)
    kwargs.setdefault("poll_interval", 0.02)
    kwargs.setdefault("max_wait_for_completion_duration", 10.0)
    return SqliteBackend(db_path=db, workspace_path=ws, **kwargs)


def _mark_completed(db_path: Path, job_id: int, result: str | None = None) -> None:
    jq = JobQueue(db_path)
    try:
        jq.update_job_status(job_id, "completed", result=result)
    finally:
        jq.close()


def _new_cache_manager(tmp_path: Path) -> DatabaseManager:
    manager = DatabaseManager(tmp_path / "cache.db")
    manager.__enter__()
    return manager


def _warning(message: str) -> BuildWarning:
    return BuildWarning(category="general", message=message, severity="low", file_path="f.py")


_worker_counter = [0]


def _seed_worker(
    db_path: Path,
    worker_type: str = "notebook",
    *,
    status: str = "idle",
    heartbeat_age_seconds: int = 0,
    session_id: str | None = None,
    execution_mode: str = "direct",
    cell_heartbeat_age_seconds: int | None = None,
) -> int:
    """Insert a worker row whose ``last_heartbeat`` is *age* seconds old."""
    _worker_counter[0] += 1
    prefix = "direct" if execution_mode == "direct" else "docker"
    cid = f"{prefix}-{worker_type}-{_worker_counter[0]}"
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO workers (worker_type, container_id, status, session_id,
                                 last_heartbeat, started_at)
            VALUES (?, ?, ?, ?,
                    datetime('now', ?), datetime('now', '-1 hour'))
            """,
            (worker_type, cid, status, session_id, f"-{heartbeat_age_seconds} seconds"),
        )
        worker_id = cur.lastrowid
        if cell_heartbeat_age_seconds is not None:
            conn.execute(
                """
                INSERT INTO worker_heartbeats (worker_id, heartbeat_at)
                VALUES (?, datetime('now', ?))
                """,
                (worker_id, f"-{cell_heartbeat_age_seconds} seconds"),
            )
        conn.commit()
        return worker_id  # type: ignore[return-value]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Symptom 1 — instant completions must be counted by the progress tracker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_instant_completion_is_counted_by_progress_tracker(temp_db, temp_workspace, caplog):
    """Regression test for #917 (symptom 1).

    A job that is already ``completed`` by the time the submit thread returns
    (a fast worker, a warm execution cache) must still be counted by the
    tracker. ``poll_interval=0`` guarantees the poll loop gets a turn between
    the shielded submit task finishing and the caller resuming — the exact
    window in which the tracker used to be ignorant of the job.
    """
    reporter = _StubReporter()
    backend = _backend(temp_db, temp_workspace, poll_interval=0, build_reporter=reporter)
    assert backend.progress_tracker is not None
    real_submit = SqliteBackend._submit_job_blocking

    def instant_worker(self, payload, job_type, force_execution=False):
        outcome, job_id = real_submit(self, payload, job_type, force_execution)
        # Simulate a worker that claims and finishes the job before the
        # event loop ever sees it.
        assert self.job_queue is not None
        self.job_queue.update_job_status(job_id, "completed")
        return outcome, job_id

    n_jobs = 5
    all_submitted = asyncio.Event()
    try:
        with patch.object(SqliteBackend, "_submit_job_blocking", instant_worker):
            waiter = asyncio.create_task(backend.wait_for_completion(all_submitted=all_submitted))
            await asyncio.sleep(0)
            for i in range(n_jobs):
                await backend.execute_operation(
                    _MockOp(), _MockPayload(input_file=f"in{i}.py", output_file=f"out{i}.ipynb")
                )
            all_submitted.set()
            assert await waiter is True

        summary = backend.progress_tracker.get_summary()
        assert summary["total"] == n_jobs
        assert summary["completed"] == n_jobs, summary
        assert summary["active"] == 0, summary
        assert "not found in tracked jobs" not in caplog.text
        # The reporter saw every completion advance the bar.
        assert reporter.progress_updates[-1].completed == n_jobs
    finally:
        backend.active_jobs.clear()
        await backend.shutdown()


@pytest.mark.asyncio
async def test_job_is_tracked_before_it_becomes_visible_to_the_poll_loop(temp_db, temp_workspace):
    """Structural pin for #917 (symptom 1): tracker first, ``active_jobs`` second.

    The poll loop only looks at ``active_jobs``; the tracker must already know
    a job at the instant it lands there, or a completion observed on the very
    next loop turn is lost.
    """
    backend = _backend(temp_db, temp_workspace, build_reporter=_StubReporter())
    tracker = backend.progress_tracker
    assert tracker is not None
    known_at_registration: list[bool] = []

    class _ObservingDict(dict):
        def __setitem__(self, job_id, info):
            known_at_registration.append(job_id in tracker._jobs)
            super().__setitem__(job_id, info)

    backend.active_jobs = _ObservingDict()
    try:
        await backend.execute_operation(_MockOp(), _MockPayload())
        assert known_at_registration == [True]
    finally:
        backend.active_jobs.clear()
        await backend.shutdown()


# ---------------------------------------------------------------------------
# Symptom 2 — liveness is status-based for workers this build owns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_busy_owned_worker_with_stale_heartbeat_is_available(temp_db, temp_workspace):
    """Regression test for #917 (symptom 2).

    A busy worker owned by this build session has not heartbeat for minutes —
    the normal state mid-job. It must still count as available: the owning
    pool's health monitor is the only component that declares it dead.
    """
    _seed_worker(temp_db, status="busy", heartbeat_age_seconds=300, session_id="sess-A")
    backend = _backend(
        temp_db,
        temp_workspace,
        skip_worker_check=False,
        worker_session_id="sess-A",
        worker_execution_modes={"notebook": "direct"},
    )
    try:
        assert backend._get_available_workers("notebook", wait_for_activation=False) == 1
        # And a real submission goes through instead of aborting the build.
        await backend.execute_operation(_MockOp(), _MockPayload())
        assert len(backend.active_jobs) == 1
    finally:
        backend.active_jobs.clear()
        await backend.shutdown()


@pytest.mark.asyncio
async def test_idle_owned_worker_with_stale_heartbeat_is_available(temp_db, temp_workspace):
    """Heartbeat writes queue behind the jobs-DB lock under load; the row's
    status — maintained by the health monitor — is the liveness authority
    for owned workers."""
    _seed_worker(temp_db, status="idle", heartbeat_age_seconds=90, session_id="sess-A")
    backend = _backend(temp_db, temp_workspace, worker_session_id="sess-A")
    try:
        assert backend._get_available_workers("notebook", wait_for_activation=False) == 1
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_dead_and_hung_workers_are_never_available(temp_db, temp_workspace):
    _seed_worker(temp_db, status="dead", heartbeat_age_seconds=0, session_id="sess-A")
    _seed_worker(temp_db, status="hung", heartbeat_age_seconds=0, session_id="sess-A")
    backend = _backend(temp_db, temp_workspace, worker_session_id="sess-A")
    try:
        assert backend._get_available_workers("notebook", wait_for_activation=False) == 0
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_other_sessions_workers_do_not_count(temp_db, temp_workspace):
    """Jobs are stamped with the build session (#620), so a worker owned by a
    different session can never claim them — counting it would let a build
    submit into a void."""
    _seed_worker(temp_db, status="idle", heartbeat_age_seconds=0, session_id="sess-B")
    backend = _backend(temp_db, temp_workspace, worker_session_id="sess-A")
    try:
        assert backend._get_available_workers("notebook", wait_for_activation=False) == 0
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_unowned_worker_needs_a_fresh_heartbeat_on_either_channel(temp_db, temp_workspace):
    """Unowned (legacy / externally started) rows have no health monitor
    vouching for them, so a heartbeat within the grace period — on either
    the workers row or the per-cell channel — is required."""
    # Long dead: stale on both channels.
    _seed_worker(temp_db, status="idle", heartbeat_age_seconds=3600)
    backend = _backend(temp_db, temp_workspace, worker_session_id="sess-A")
    try:
        assert backend._get_available_workers("notebook", wait_for_activation=False) == 0
    finally:
        await backend.shutdown()

    # Busy for a long time but writing per-cell heartbeats: alive.
    _seed_worker(
        temp_db,
        status="busy",
        heartbeat_age_seconds=3600,
        cell_heartbeat_age_seconds=5,
    )
    backend = _backend(temp_db, temp_workspace, worker_session_id="sess-A")
    try:
        assert backend._get_available_workers("notebook", wait_for_activation=False) == 1
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_availability_respects_execution_mode(temp_db, temp_workspace):
    _seed_worker(
        temp_db,
        status="busy",
        heartbeat_age_seconds=300,
        session_id="sess-A",
        execution_mode="docker",
    )
    backend = _backend(
        temp_db,
        temp_workspace,
        worker_session_id="sess-A",
        worker_execution_modes={"notebook": "direct"},
    )
    try:
        assert backend._get_available_workers("notebook", wait_for_activation=False) == 0
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_no_workers_error_describes_the_real_condition(temp_db, temp_workspace):
    backend = _backend(temp_db, temp_workspace, skip_worker_check=False, worker_session_id="sess-A")
    try:
        with pytest.raises(RuntimeError) as excinfo:
            await backend.execute_operation(_MockOp(), _MockPayload())
        message = str(excinfo.value)
        assert "notebook" in message
        assert "10 seconds" not in message  # the old, wrong claim
        assert "sess-A" in message
    finally:
        backend.active_jobs.clear()
        await backend.shutdown()


# ---------------------------------------------------------------------------
# Symptom 3 — cache-DB bookkeeping runs off the loop, in order, with retries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completion_bookkeeping_writes_run_on_the_writer_thread(
    temp_db, temp_workspace, tmp_path
):
    """Regression test for #917 (symptom 3).

    Every cache-DB write the completion loop triggers — clearing superseded
    issues, storing fresh warnings, storing the result blob — must run on the
    background writer thread, never on the event loop, and in submission
    order (clear before store) so a fresh run's warnings survive.
    """
    backend = _backend(temp_db, temp_workspace, build_reporter=_StubReporter())
    backend.db_manager = _new_cache_manager(tmp_path)
    loop_thread = threading.current_thread()
    calls: list[tuple[str, threading.Thread]] = []

    def _spy(name):
        real = getattr(DatabaseManager, name)

        def wrapper(self, *args, **kwargs):
            calls.append((name, threading.current_thread()))
            return real(self, *args, **kwargs)

        return wrapper

    try:
        await backend.execute_operation(_MockOp(), _MockPayload())
        job_id = next(iter(backend.active_jobs))
        output = temp_workspace / "out.ipynb"
        output.write_text("{}", encoding="utf-8")
        result_json = json.dumps(
            {"warnings": [{"category": "duplicate", "message": "dup", "severity": "high"}]}
        )
        _mark_completed(temp_db, job_id, result=result_json)

        with (
            patch.object(DatabaseManager, "clear_issues", _spy("clear_issues")),
            patch.object(DatabaseManager, "store_warning", _spy("store_warning")),
            patch.object(DatabaseManager, "store_latest_result", _spy("store_latest_result")),
        ):
            assert await backend.wait_for_completion() is True

        names = [name for name, _ in calls]
        assert names == ["clear_issues", "store_warning", "store_latest_result"], names
        for name, thread in calls:
            assert thread is not loop_thread, f"{name} ran on the event-loop thread"
            assert thread.name == "ResultCacheWriter", (name, thread.name)
        # The warning was still reported on the loop, synchronously.
        assert len(backend.build_reporter.warnings) == 1
    finally:
        backend.active_jobs.clear()
        await backend.shutdown()
        backend.db_manager.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_failed_job_error_is_stored_on_the_writer_thread(temp_db, temp_workspace, tmp_path):
    backend = _backend(temp_db, temp_workspace, build_reporter=_StubReporter())
    backend.db_manager = _new_cache_manager(tmp_path)
    loop_thread = threading.current_thread()
    seen: list[threading.Thread] = []
    real_store_error = DatabaseManager.store_error

    def spy(self, *args, **kwargs):
        seen.append(threading.current_thread())
        return real_store_error(self, *args, **kwargs)

    try:
        payload = _MockPayload()
        await backend.execute_operation(_MockOp(), payload)
        job_id = next(iter(backend.active_jobs))
        jq = JobQueue(temp_db)
        try:
            jq.update_job_status(job_id, "failed", error="SyntaxError: invalid syntax")
        finally:
            jq.close()
        with patch.object(DatabaseManager, "store_error", spy):
            assert await backend.wait_for_completion() is False
        assert len(seen) == 1
        assert seen[0] is not loop_thread
        # Drained before wait_for_completion returned: the error is readable now.
        output_metadata = backend._get_output_metadata("notebook", payload.model_dump(mode="json"))
        errors, _ = backend.db_manager.get_issues(
            payload.input_file, payload.content_hash(), output_metadata
        )
        assert len(errors) == 1
    finally:
        backend.active_jobs.clear()
        await backend.shutdown()
        backend.db_manager.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_result_cache_writer_retries_transient_lock_errors(
    temp_db, temp_workspace, tmp_path, monkeypatch
):
    """Regression test for #917 (symptom 3): a ``database is locked`` on the
    result-cache write is retried, not dropped. Dropping it silently lost the
    cache entry, so the next build re-executed the notebook."""
    monkeypatch.setattr(busy_retry, "DEFAULT_BUSY_RETRY_DELAYS", (0.01, 0.01, 0.01))
    backend = _backend(temp_db, temp_workspace, build_reporter=_StubReporter())
    backend.db_manager = _new_cache_manager(tmp_path)
    real_store = DatabaseManager.store_latest_result
    failures_left = [2]

    def flaky_store(self, *args, **kwargs):
        if failures_left[0] > 0:
            failures_left[0] -= 1
            raise sqlite3.OperationalError("database is locked")
        return real_store(self, *args, **kwargs)

    try:
        payload = _MockPayload()
        await backend.execute_operation(_MockOp(), payload)
        job_id = next(iter(backend.active_jobs))
        (temp_workspace / "out.ipynb").write_text("{}", encoding="utf-8")
        _mark_completed(temp_db, job_id)
        with patch.object(DatabaseManager, "store_latest_result", flaky_store):
            assert await backend.wait_for_completion() is True

        assert failures_left[0] == 0
        conn = sqlite3.connect(tmp_path / "cache.db")
        try:
            rows = conn.execute(
                "SELECT COUNT(*) FROM processed_files WHERE file_path = ?",
                (payload.input_file,),
            ).fetchone()[0]
        finally:
            conn.close()
        assert rows == 1, "result was dropped instead of retried"
    finally:
        backend.active_jobs.clear()
        await backend.shutdown()
        backend.db_manager.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_result_cache_writer_commits_queued_writes_as_one_batch(
    temp_db, temp_workspace, tmp_path
):
    """Writes that pile up while the writer is busy are committed in one
    transaction — one lock acquisition instead of one per row — which is
    what keeps the parent's share of cache-DB lock churn bounded under load."""
    backend = _backend(temp_db, temp_workspace, build_reporter=_StubReporter())
    backend.db_manager = _new_cache_manager(tmp_path)
    commits: list[str] = []
    real_enter = DatabaseManager.__enter__

    def traced_enter(self):
        result = real_enter(self)
        assert self.conn is not None
        self.conn.set_trace_callback(
            lambda stmt: commits.append(stmt) if stmt.strip().upper().startswith("COMMIT") else None
        )
        return result

    gate = threading.Event()
    released = threading.Event()

    def blocker(db):
        released.set()
        gate.wait(timeout=10)

    try:
        with patch.object(DatabaseManager, "__enter__", traced_enter):
            backend._enqueue_cache_write("gate", blocker)
            assert released.wait(timeout=10)
            n_writes = 10
            for i in range(n_writes):
                backend._enqueue_cache_write(
                    f"warning-{i}",
                    lambda db, i=i: db.store_warning(
                        file_path="f.py",
                        content_hash="h",
                        output_metadata=f"m{i}",
                        warning=_warning(f"w{i}"),
                    ),
                )
            gate.set()
            await backend._drain_result_cache_writes()

        # One transaction for the gate item, one for the whole batch behind it.
        assert len(commits) == 2, commits
        conn = sqlite3.connect(tmp_path / "cache.db")
        try:
            count = conn.execute("SELECT COUNT(*) FROM processing_issues").fetchone()[0]
        finally:
            conn.close()
        assert count == n_writes
    finally:
        backend.active_jobs.clear()
        await backend.shutdown()
        backend.db_manager.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_dead_worker_cleanup_runs_off_the_event_loop(temp_db, temp_workspace):
    """The periodic ``BEGIN IMMEDIATE`` sweep for dead-worker jobs takes the
    jobs-DB write lock; under contention that can wait for the full busy
    timeout, so it must not run on the loop thread."""
    backend = _backend(temp_db, temp_workspace, build_reporter=_StubReporter())
    loop_thread = threading.current_thread()
    seen: list[threading.Thread] = []
    real_cleanup = SqliteBackend._cleanup_dead_worker_jobs

    def spy(self):
        seen.append(threading.current_thread())
        return real_cleanup(self)

    try:
        await backend.execute_operation(_MockOp(), _MockPayload())
        job_id = next(iter(backend.active_jobs))

        async def complete_later():
            await asyncio.sleep(0.2)
            _mark_completed(temp_db, job_id)

        with (
            patch.object(SqliteBackend, "_cleanup_dead_worker_jobs", spy),
            patch.object(sqlite_backend_module, "DEAD_WORKER_SWEEP_INTERVAL_SECONDS", 0.0),
        ):
            completer = asyncio.create_task(complete_later())
            assert await backend.wait_for_completion() is True
            await completer
        assert seen, "the dead-worker sweep never ran"
        assert all(t is not loop_thread for t in seen)
    finally:
        backend.active_jobs.clear()
        await backend.shutdown()


# ---------------------------------------------------------------------------
# Review follow-ups (adversarial review of the #917 fix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_writer_thread_survives_a_non_lock_database_error(temp_db, temp_workspace, tmp_path):
    """A non-lock ``sqlite3.DatabaseError`` raised by the batch machinery
    itself (BEGIN/COMMIT on a corrupt WAL, say) must not kill the writer
    thread: a dead writer turns every later ``queue.join()`` — end of stage,
    shutdown — into a hang."""
    backend = _backend(temp_db, temp_workspace, build_reporter=_StubReporter())
    backend.db_manager = _new_cache_manager(tmp_path)
    real_batch = DatabaseManager.batch
    poisoned = [True]

    def poisoned_batch(self):
        if poisoned[0]:
            poisoned[0] = False
            raise sqlite3.DatabaseError("database disk image is malformed")
        return real_batch(self)

    try:
        with patch.object(DatabaseManager, "batch", poisoned_batch):
            backend._enqueue_cache_write(
                "first", lambda db: db.store_warning("f.py", "h", "m0", _warning("w0"))
            )
            await asyncio.wait_for(backend._drain_result_cache_writes(), timeout=10)
        # The thread is still alive and keeps serving.
        assert backend._result_cache_thread is not None
        assert backend._result_cache_thread.is_alive()
        backend._enqueue_cache_write(
            "second", lambda db: db.store_warning("f.py", "h", "m1", _warning("w1"))
        )
        await asyncio.wait_for(backend._drain_result_cache_writes(), timeout=10)
        conn = sqlite3.connect(tmp_path / "cache.db")
        try:
            count = conn.execute("SELECT COUNT(*) FROM processing_issues").fetchone()[0]
        finally:
            conn.close()
        assert count == 1  # the poisoned batch was dropped, the next one landed
    finally:
        backend.active_jobs.clear()
        await asyncio.wait_for(backend.shutdown(), timeout=30)
        backend.db_manager.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_result_prepare_failure_drops_only_that_item(temp_db, temp_workspace, tmp_path):
    """A result whose output file vanished (or whose jobs row is gone) is
    dropped on its own, outside the batch transaction — it must not roll
    back or delay the other writes queued with it."""
    backend = _backend(temp_db, temp_workspace, build_reporter=_StubReporter())
    backend.db_manager = _new_cache_manager(tmp_path)
    try:
        await backend.execute_operation(_MockOp(), _MockPayload())
        job_id = next(iter(backend.active_jobs))
        missing = temp_workspace / "does-not-exist.ipynb"
        backend._enqueue_result_cache(job_id, dict(backend.active_jobs[job_id]), missing)
        backend._enqueue_cache_write(
            "warning", lambda db: db.store_warning("f.py", "h", "m", _warning("w"))
        )
        await asyncio.wait_for(backend._drain_result_cache_writes(), timeout=10)
        conn = sqlite3.connect(tmp_path / "cache.db")
        try:
            warnings = conn.execute("SELECT COUNT(*) FROM processing_issues").fetchone()[0]
            results = conn.execute("SELECT COUNT(*) FROM processed_files").fetchone()[0]
        finally:
            conn.close()
        assert (warnings, results) == (1, 0)
    finally:
        backend.active_jobs.clear()
        await backend.shutdown()
        backend.db_manager.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_no_workers_error_names_other_sessions_live_workers(temp_db, temp_workspace):
    """Persistent workers left by an earlier build are alive but stamped with
    that build's session, so they can never claim ours. The error must say
    so instead of sending the user to hunt for a startup crash."""
    _seed_worker(temp_db, status="idle", heartbeat_age_seconds=0, session_id="sess-OLD")
    _seed_worker(temp_db, status="busy", heartbeat_age_seconds=0, session_id="sess-OLD")
    backend = _backend(temp_db, temp_workspace, skip_worker_check=False, worker_session_id="sess-A")
    try:
        with pytest.raises(RuntimeError) as excinfo:
            await backend.execute_operation(_MockOp(), _MockPayload())
        message = str(excinfo.value)
        assert "2 live notebook worker(s)" in message
        assert "other build sessions" in message
        assert "startup crash" not in message
    finally:
        backend.active_jobs.clear()
        await backend.shutdown()


@pytest.mark.asyncio
async def test_dead_worker_sweep_does_not_queue_behind_submissions(temp_db, temp_workspace):
    """The sweep runs on its own maintenance thread, not the single submit
    thread: parked behind a slow submission it would stall the awaiting poll
    loop and freeze the progress bar."""
    backend = _backend(temp_db, temp_workspace, build_reporter=_StubReporter())
    seen: list[str] = []
    real_cleanup = SqliteBackend._cleanup_dead_worker_jobs

    def spy(self):
        seen.append(threading.current_thread().name)
        return real_cleanup(self)

    try:
        await backend.execute_operation(_MockOp(), _MockPayload())
        job_id = next(iter(backend.active_jobs))

        async def complete_later():
            await asyncio.sleep(0.2)
            _mark_completed(temp_db, job_id)

        with (
            patch.object(SqliteBackend, "_cleanup_dead_worker_jobs", spy),
            patch.object(sqlite_backend_module, "DEAD_WORKER_SWEEP_INTERVAL_SECONDS", 0.0),
        ):
            completer = asyncio.create_task(complete_later())
            assert await backend.wait_for_completion() is True
            await completer
        assert seen
        assert all(name.startswith("clm-jobs-maint") for name in seen), seen
    finally:
        backend.active_jobs.clear()
        await backend.shutdown()
