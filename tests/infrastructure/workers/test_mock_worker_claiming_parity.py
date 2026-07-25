"""Pin the properties ``MockWorker`` inherits from the real claim path.

``MockWorker._poll_job`` used to be a hand-rolled ``UPDATE … RETURNING`` that
had drifted from ``JobQueue.get_next_job`` in six ways (finding T7 of the
2026-07-24 adversarial review):

* no ``execution_mode`` filter (PR #564's cross-mode job-theft guard)
* no session-ownership filter (issue #620)
* no ``attempts < max_attempts`` guard
* no ``attempts`` increment
* no ``started_at`` stamp
* no ``priority`` ordering

and, on top of that, it wrote the container-id *string* into the integer
``jobs.worker_id`` column. The tier meant to exercise real claiming with real
workers together was the permanently-skipped file from finding T1, so nothing
caught the divergence.

``_poll_job`` now delegates to the real implementation. These tests fail if
that delegation is ever unpicked.
"""

import sqlite3
import time
from pathlib import Path

import pytest

from tests.fixtures.mock_workers import MockWorker, MockWorkerConfig

# Same rationale as ``test_lifecycle_mock.py``: worker threads polling
# committed SQLite state are CPU-starvation-sensitive under xdist load.
# ``AssertionError`` is deliberately absent from ``only_rerun`` (finding T6) —
# an intermittent claiming race is exactly what these tests exist to surface.
pytestmark = [
    pytest.mark.serial("workerpool"),
    pytest.mark.flaky(
        reruns=2,
        reruns_delay=1,
        only_rerun=["OSError", "PermissionError", "OperationalError", "TimeoutError"],
    ),
]

# How long to keep watching after the control job finishes, to give a
# mis-implemented claim time to happen before we assert it did not.
SETTLE_SECONDS = 0.3


def _insert_job(
    db_path: Path,
    *,
    content_hash: str,
    output_file: Path,
    execution_mode: str | None = None,
    session_id: str | None = None,
    priority: int = 0,
) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.execute(
            """INSERT INTO jobs
               (job_type, input_file, output_file, content_hash, payload, status,
                execution_mode, session_id, priority)
               VALUES (?, ?, ?, ?, '{}', 'pending', ?, ?, ?)""",
            (
                "notebook",
                f"{content_hash}.ipynb",
                str(output_file),
                content_hash,
                execution_mode,
                session_id,
                priority,
            ),
        )
        conn.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid
    finally:
        conn.close()


def _job_row(db_path: Path, job_id: int) -> sqlite3.Row:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        assert row is not None, f"job {job_id} disappeared"
        return row
    finally:
        conn.close()


def _wait_for_status(db_path: Path, job_id: int, status: str, timeout: float = 15.0) -> None:
    """Poll until *job_id* reaches *status*, or fail with what it reached instead."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = _job_row(db_path, job_id)["status"]
        if last == status:
            return
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} never reached {status!r} (last seen: {last!r})")


def test_claim_records_numeric_worker_id_and_bookkeeping(mock_db_path, mock_workspace_path):
    """A claim sets the integer worker row id, ``started_at`` and ``attempts``."""
    job_id = _insert_job(
        mock_db_path,
        content_hash="parity-basic",
        output_file=mock_workspace_path / "out" / "parity_basic.ipynb",
    )

    worker = MockWorker(MockWorkerConfig(worker_type="notebook"), mock_db_path, worker_id=0)
    try:
        worker.start()
        assert worker.wait_for_registration(), "Worker should register"
        _wait_for_status(mock_db_path, job_id, "completed")

        row = _job_row(mock_db_path, job_id)
        # The old implementation wrote the string "mock-notebook-0" here.
        assert row["worker_id"] == worker.db_worker_id
        assert isinstance(row["worker_id"], int)
        assert row["started_at"] is not None, "get_next_job stamps started_at"
        assert row["attempts"] == 1, "get_next_job increments attempts"
    finally:
        worker.stop()


def test_worker_does_not_claim_a_foreign_execution_mode(mock_db_path, mock_workspace_path):
    """A job tagged for another execution mode is left alone (PR #564)."""
    foreign = _insert_job(
        mock_db_path,
        content_hash="parity-docker",
        output_file=mock_workspace_path / "out" / "parity_docker.ipynb",
        execution_mode="docker",
    )
    untagged = _insert_job(
        mock_db_path,
        content_hash="parity-untagged",
        output_file=mock_workspace_path / "out" / "parity_untagged.ipynb",
    )

    worker = MockWorker(
        MockWorkerConfig(worker_type="notebook", processing_delay=0.01),
        mock_db_path,
        worker_id=0,
    )
    try:
        worker.start()
        assert worker.wait_for_registration(), "Worker should register"
        # The untagged job completing proves the worker really is polling —
        # without that control, "foreign job untouched" would also pass if the
        # worker had never started.
        _wait_for_status(mock_db_path, untagged, "completed")
        time.sleep(SETTLE_SECONDS)

        row = _job_row(mock_db_path, foreign)
        assert row["status"] == "pending"
        assert row["worker_id"] is None
    finally:
        worker.stop()


def test_worker_does_not_claim_another_sessions_job(mock_db_path, mock_workspace_path):
    """A session-stamped worker leaves another session's jobs alone (issue #620)."""
    other_session = _insert_job(
        mock_db_path,
        content_hash="parity-session-b",
        output_file=mock_workspace_path / "out" / "parity_session_b.ipynb",
        session_id="session-b",
    )
    own_session = _insert_job(
        mock_db_path,
        content_hash="parity-session-a",
        output_file=mock_workspace_path / "out" / "parity_session_a.ipynb",
        session_id="session-a",
    )

    worker = MockWorker(
        MockWorkerConfig(worker_type="notebook", processing_delay=0.01, session_id="session-a"),
        mock_db_path,
        worker_id=0,
    )
    try:
        worker.start()
        assert worker.wait_for_registration(), "Worker should register"
        _wait_for_status(mock_db_path, own_session, "completed")
        time.sleep(SETTLE_SECONDS)

        assert _job_row(mock_db_path, other_session)["status"] == "pending"
    finally:
        worker.stop()


def test_worker_respects_priority_order(mock_db_path, mock_workspace_path):
    """Higher-priority jobs are claimed first, regardless of insertion order."""
    low = _insert_job(
        mock_db_path,
        content_hash="parity-low",
        output_file=mock_workspace_path / "out" / "parity_low.ipynb",
        priority=0,
    )
    high = _insert_job(
        mock_db_path,
        content_hash="parity-high",
        output_file=mock_workspace_path / "out" / "parity_high.ipynb",
        priority=10,
    )

    # One worker, with a processing delay long enough that the ordering is
    # observable: the high-priority job must finish while the low-priority one
    # — inserted first — is still queued or only just started.
    worker = MockWorker(
        MockWorkerConfig(worker_type="notebook", processing_delay=0.5),
        mock_db_path,
        worker_id=0,
    )
    try:
        worker.start()
        assert worker.wait_for_registration(), "Worker should register"
        _wait_for_status(mock_db_path, high, "completed")

        assert _job_row(mock_db_path, low)["status"] in ("pending", "processing")
    finally:
        worker.stop()
