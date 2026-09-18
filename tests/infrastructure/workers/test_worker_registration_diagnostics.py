"""A worker-registration timeout names what the workers did (#847, direction 2).

The direct-worker integration tests wait for the ``workers`` table to flip
``created → idle``. When that timed out under xdist load the error carried
only a count ("expected 2, got 0"), which cannot distinguish a worker that is
slow to activate from one that died on import — and the per-worker log, the
only place the latter says why, was never shown. ``WorkerPoolManager.
describe_workers`` now reports, per worker, the db row, whether the process
is alive, and the log tail; the wait helpers attach it to their
``TimeoutError``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from clm.infrastructure.database.job_queue import JobQueue
from clm.infrastructure.database.schema import init_database
from clm.infrastructure.workers.pool_manager import WorkerPoolManager
from clm.infrastructure.workers.worker_executor import DirectWorkerExecutor

# ---------------------------------------------------------------------------
# DirectWorkerExecutor.get_container_logs
# ---------------------------------------------------------------------------


def _direct_executor_with(worker_info: dict) -> DirectWorkerExecutor:
    executor = DirectWorkerExecutor.__new__(DirectWorkerExecutor)
    executor.worker_info = worker_info  # type: ignore[attr-defined]
    executor.processes = {}  # type: ignore[attr-defined]
    return executor


def test_direct_log_tail_returns_last_lines(tmp_path: Path):
    log = tmp_path / "notebook-0.log"
    log.write_text("\n".join(f"line {i}" for i in range(50)) + "\n", encoding="utf-8")
    executor = _direct_executor_with({"w1": {"log_path": str(log), "log_file": None}})

    tail = executor.get_container_logs("w1", tail=3)

    assert tail == "line 47\nline 48\nline 49"


def test_direct_log_tail_flushes_the_open_handle_first(tmp_path: Path):
    log = tmp_path / "notebook-0.log"
    handle = open(log, "a", encoding="utf-8")  # noqa: SIM115 - the executor owns it
    try:
        handle.write("Traceback: ModuleNotFoundError: no module named 'nbformat'")
        executor = _direct_executor_with({"w1": {"log_path": str(log), "log_file": handle}})
        assert "ModuleNotFoundError" in (executor.get_container_logs("w1") or "")
    finally:
        handle.close()


def test_direct_log_tail_unknown_worker_or_missing_file_is_none(tmp_path: Path):
    executor = _direct_executor_with(
        {"gone": {"log_path": str(tmp_path / "nope.log"), "log_file": None}}
    )
    assert executor.get_container_logs("unknown") is None
    assert executor.get_container_logs("gone") is None


def test_direct_log_tail_empty_file_is_empty_string(tmp_path: Path):
    log = tmp_path / "empty.log"
    log.write_text("", encoding="utf-8")
    executor = _direct_executor_with({"w1": {"log_path": str(log), "log_file": None}})
    assert executor.get_container_logs("w1") == ""


# ---------------------------------------------------------------------------
# WorkerPoolManager.describe_workers
# ---------------------------------------------------------------------------


class _StubExecutor:
    def __init__(self, *, alive: bool, log: str | None, raise_on_stats: bool = False):
        self._alive = alive
        self._log = log
        self._raise = raise_on_stats

    def get_worker_stats(self, worker_id: str):
        if self._raise:
            raise RuntimeError("stats exploded")
        return {"pid": 4242, "is_alive": self._alive}

    def get_container_logs(self, worker_id: str, tail: int = 100):
        return self._log


@pytest.fixture
def pool(tmp_path: Path):
    """A pool manager skeleton over a real jobs DB — no processes started."""
    db_path = tmp_path / "jobs.db"
    init_database(db_path)
    manager = WorkerPoolManager.__new__(WorkerPoolManager)
    manager.job_queue = JobQueue(db_path)
    manager.workers = {}
    manager.executors = {}
    yield manager
    manager.job_queue.close()


def _insert_worker_row(db_path: Path, *, container_id: str, status: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO workers (worker_type, container_id, status) VALUES (?, ?, ?)",
            ("notebook", container_id, status),
        )
        conn.commit()
        return int(cur.lastrowid)  # type: ignore[arg-type]
    finally:
        conn.close()


def _worker_info(db_id: int, executor_id: str, mode: str = "direct") -> dict:
    return {
        "executor_id": executor_id,
        "db_worker_id": db_id,
        "config": SimpleNamespace(execution_mode=mode),
    }


def test_describe_workers_reports_row_process_and_log_tail(pool: WorkerPoolManager, tmp_path):
    db_id = _insert_worker_row(pool.job_queue.db_path, container_id="direct-nb-0", status="created")
    pool.workers = {"notebook": [_worker_info(db_id, "direct-nb-0")]}
    pool.executors = {
        "direct": _StubExecutor(alive=False, log="starting…\nImportError: cannot import name X")
    }

    report = pool.describe_workers(tail=5)

    assert f"notebook worker db_id={db_id}" in report
    assert "status=created" in report
    assert "pid=4242 alive=False" in report
    assert "ImportError: cannot import name X" in report


def test_describe_workers_marks_missing_row_and_empty_log(pool: WorkerPoolManager):
    pool.workers = {"notebook": [_worker_info(999, "direct-nb-9")]}
    pool.executors = {"direct": _StubExecutor(alive=True, log="")}

    report = pool.describe_workers()

    assert "db row: MISSING" in report
    assert "alive=True" in report
    assert "log: empty" in report


def test_describe_workers_never_raises(pool: WorkerPoolManager):
    """Diagnostics on a failure path must not replace the original failure."""
    db_id = _insert_worker_row(pool.job_queue.db_path, container_id="x", status="idle")
    pool.workers = {"notebook": [_worker_info(db_id, "x")]}
    pool.executors = {"direct": _StubExecutor(alive=True, log=None, raise_on_stats=True)}

    report = pool.describe_workers()

    assert "process: unreadable (stats exploded)" in report
    assert "status=idle" in report


def test_describe_workers_with_nothing_started(pool: WorkerPoolManager):
    assert pool.describe_workers() == "(no workers started by this pool)"


# ---------------------------------------------------------------------------
# The wait helpers attach the description to their timeout
# ---------------------------------------------------------------------------


def test_registration_wait_timeout_carries_the_worker_report(tmp_path: Path):
    from tests.infrastructure.workers.test_direct_integration import (
        _wait_for_registered_workers,
    )

    db_path = tmp_path / "jobs.db"
    init_database(db_path)
    queue = JobQueue(db_path)
    try:
        manager = SimpleNamespace(
            job_queue=queue,
            describe_workers=lambda: "notebook worker db_id=1 … alive=False\n  log tail: boom",
        )
        with pytest.raises(TimeoutError) as excinfo:
            _wait_for_registered_workers(manager, 1, timeout=0.05, interval=0.01)  # type: ignore[arg-type]
    finally:
        queue.close()

    message = str(excinfo.value)
    assert "got 0" in message
    assert "Worker state at timeout:" in message
    assert "log tail: boom" in message


def test_healthy_wait_timeout_carries_the_report_when_given_a_describer(tmp_path: Path):
    from tests.infrastructure.workers.test_lifecycle_integration import (
        _wait_for_healthy_workers,
    )

    db_path = tmp_path / "jobs.db"
    init_database(db_path)

    with pytest.raises(TimeoutError) as excinfo:
        _wait_for_healthy_workers(
            db_path, expected_count=1, timeout=0.05, interval=0.01, describe=lambda: "DIAG-847"
        )
    assert "DIAG-847" in str(excinfo.value)

    # Without a describer the message keeps its previous shape.
    with pytest.raises(TimeoutError) as excinfo2:
        _wait_for_healthy_workers(db_path, expected_count=1, timeout=0.05, interval=0.01)
    assert "Worker state at timeout" not in str(excinfo2.value)
