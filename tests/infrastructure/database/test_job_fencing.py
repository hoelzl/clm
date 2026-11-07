"""A job must not run twice, and a stale worker must not overwrite it (#799 C3 + C5).

The interleavings are forced by hand against a real SQLite queue: each test
drives two workers through a schedule that used to end in a double run or in a
stale write.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from clm.infrastructure.api.job_queue_adapter import ApiJobQueue
from clm.infrastructure.api.server import WorkerApiServer
from clm.infrastructure.database.job_queue import JobQueue
from clm.infrastructure.database.schema import init_database
from clm.infrastructure.workers.worker_base import Worker
from tests.infrastructure.api.conftest import authed_client


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "jobs.db"
    init_database(path)
    return path


@pytest.fixture
def queue(db_path: Path) -> Iterator[JobQueue]:
    q = JobQueue(db_path)
    yield q
    q.close()


def _worker(
    queue: JobQueue,
    name: str,
    *,
    session_id: str | None = None,
    status: str = "busy",
    heartbeat_age: int = 0,
) -> int:
    cursor = queue._get_conn().execute(
        """
        INSERT INTO workers (worker_type, container_id, status, session_id, last_heartbeat)
        VALUES ('notebook', ?, ?, ?, datetime('now', ?))
        """,
        (f"direct-notebook-{name}", status, session_id, f"-{heartbeat_age} seconds"),
    )
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def _job(queue: JobQueue, *, session_id: str | None = None, max_attempts: int = 3) -> int:
    job_id = queue.add_job(
        job_type="notebook",
        input_file="deck.py",
        output_file="deck.html",
        content_hash="hash",
        payload={},
        session_id=session_id,
    )
    queue._get_conn().execute(
        "UPDATE jobs SET max_attempts = ? WHERE id = ?", (max_attempts, job_id)
    )
    return job_id


def _claim(queue: JobQueue, worker_id: int, job_id: int) -> None:
    job = queue.get_next_job("notebook", worker_id=worker_id)
    assert job is not None
    assert job.id == job_id


def _age_job(queue: JobQueue, job_id: int, seconds: int = 700) -> None:
    queue._get_conn().execute(
        "UPDATE jobs SET started_at = datetime('now', ?) WHERE id = ?",
        (f"-{seconds} seconds", job_id),
    )


def _status(queue: JobQueue, job_id: int) -> str:
    job = queue.get_job(job_id)
    assert job is not None
    return job.status


class TestStatusFence:
    """C5: only the worker holding a processing job can finish it."""

    def test_a_stale_worker_cannot_overwrite_the_new_holder(self, queue: JobQueue) -> None:
        stale = _worker(queue, "stale")
        holder = _worker(queue, "holder")
        job_id = _job(queue)
        _claim(queue, stale, job_id)

        # The dead-worker sweep requeues the job; another worker takes it.
        queue._get_conn().execute(
            "UPDATE jobs SET status='pending', worker_id=NULL, started_at=NULL WHERE id=?",
            (job_id,),
        )
        _claim(queue, holder, job_id)

        assert queue.update_job_status(job_id, "completed", result="{}", worker_id=stale) is False
        job = queue.get_job(job_id)
        assert job is not None
        assert job.status == "processing"
        assert job.worker_id == holder

        assert queue.update_job_status(job_id, "completed", result="{}", worker_id=holder) is True
        assert _status(queue, job_id) == "completed"

    def test_a_stale_failure_does_not_fail_the_new_holders_job(self, queue: JobQueue) -> None:
        stale = _worker(queue, "stale")
        holder = _worker(queue, "holder")
        job_id = _job(queue)
        _claim(queue, holder, job_id)

        assert queue.update_job_status(job_id, "failed", error="boom", worker_id=stale) is False

        job = queue.get_job(job_id)
        assert job is not None
        assert job.status == "processing"
        assert job.error is None

    def test_finishing_does_not_undo_a_cancellation(self, queue: JobQueue) -> None:
        worker = _worker(queue, "w")
        job_id = _job(queue)
        _claim(queue, worker, job_id)
        queue.cancel_jobs_for_file("deck.py")

        assert queue.update_job_status(job_id, "completed", result="{}", worker_id=worker) is False

        assert _status(queue, job_id) == "cancelled"

    def test_a_second_terminal_write_does_not_change_the_first(self, queue: JobQueue) -> None:
        worker = _worker(queue, "w")
        job_id = _job(queue)
        _claim(queue, worker, job_id)
        assert queue.update_job_status(job_id, "failed", error="boom", worker_id=worker) is True

        assert queue.update_job_status(job_id, "completed", result="{}", worker_id=worker) is False

        assert _status(queue, job_id) == "failed"

    def test_a_worker_whose_row_was_deleted_can_still_finish_its_job(self, queue: JobQueue) -> None:
        """Another build's stale-row cleanup can delete a live worker's row (#799 C2).

        The job row keeps the worker id and ids are never reused, so the fence
        must not strand this job.
        """
        worker = _worker(queue, "w")
        job_id = _job(queue)
        _claim(queue, worker, job_id)
        conn = queue._get_conn()
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DELETE FROM workers WHERE id = ?", (worker,))

        assert queue.update_job_status(job_id, "completed", result="{}", worker_id=worker) is True

        assert _status(queue, job_id) == "completed"

    def test_maintenance_callers_stay_unfenced(self, queue: JobQueue) -> None:
        job_id = _job(queue)

        assert queue.update_job_status(job_id, "failed", error="by hand") is True

        assert _status(queue, job_id) == "failed"

    def test_an_unknown_job_reports_false(self, queue: JobQueue) -> None:
        assert queue.update_job_status(12345, "failed", error="gone") is False


class TestRoutePassesTheFence:
    @pytest.fixture
    def client(self, db_path: Path) -> Iterator[TestClient]:
        with authed_client(WorkerApiServer(db_path), raise_server_exceptions=False) as c:
            yield c

    def test_a_rejected_update_is_not_acknowledged(
        self, client: TestClient, queue: JobQueue
    ) -> None:
        stale = _worker(queue, "stale")
        holder = _worker(queue, "holder")
        job_id = _job(queue)
        _claim(queue, holder, job_id)

        response = client.post(
            f"/api/worker/jobs/{job_id}/status",
            json={"worker_id": stale, "status": "completed", "result": {}},
        )

        assert response.status_code == 200
        assert response.json() == {"acknowledged": False}
        assert _status(queue, job_id) == "processing"

    def test_the_holders_update_is_acknowledged(self, client: TestClient, queue: JobQueue) -> None:
        holder = _worker(queue, "holder")
        job_id = _job(queue)
        _claim(queue, holder, job_id)

        response = client.post(
            f"/api/worker/jobs/{job_id}/status",
            json={"worker_id": holder, "status": "completed", "result": {}},
        )

        assert response.json() == {"acknowledged": True}
        assert _status(queue, job_id) == "completed"


class TestWorkerSidesReportTheOutcome:
    def test_the_adapter_returns_the_servers_answer(self) -> None:
        client = MagicMock()
        client.complete_job.return_value = False
        client.fail_job.return_value = True
        adapter = ApiJobQueue("http://host:1", worker_id=7)
        adapter._client = client

        assert adapter.update_job_status(1, "completed", result="{}", worker_id=7) is False
        assert adapter.update_job_status(1, "failed", error="{}", worker_id=7) is True

    def test_a_worker_sends_its_id_and_warns_when_rejected(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        worker = MagicMock(spec=Worker)
        worker.worker_id = 7
        worker.job_queue = MagicMock()
        worker.job_queue.update_job_status.return_value = False

        with caplog.at_level("WARNING"):
            accepted = Worker._write_terminal_status(worker, 3, "completed", result="{}")

        assert accepted is False
        worker.job_queue.update_job_status.assert_called_once_with(
            3, "completed", error=None, result="{}", worker_id=7
        )
        assert "job 3" in caplog.text.lower()


class TestHungReset:
    """C3: a hung-looking job is only requeued when its worker shows no life."""

    def test_a_live_workers_job_is_not_run_twice(self, queue: JobQueue) -> None:
        """The double run from #799: reset, then a second worker claims it."""
        alive = _worker(queue, "alive", session_id="A")
        second = _worker(queue, "second", session_id="A")
        job_id = _job(queue, session_id="A")
        _claim(queue, alive, job_id)
        _age_job(queue, job_id)

        assert queue.reset_hung_jobs(timeout_seconds=600) == 0

        assert queue.get_next_job("notebook", worker_id=second) is None
        job = queue.get_job(job_id)
        assert job is not None
        assert (job.status, job.worker_id, job.attempts) == ("processing", alive, 1)

    def test_a_cell_beacon_counts_as_life(self, queue: JobQueue) -> None:
        """A busy notebook worker refreshes the beacon, not ``workers.last_heartbeat``."""
        worker = _worker(queue, "w", heartbeat_age=3000)
        job_id = _job(queue)
        _claim(queue, worker, job_id)
        _age_job(queue, job_id)
        queue._get_conn().execute(
            "INSERT INTO worker_heartbeats (worker_id, job_id, heartbeat_at) "
            "VALUES (?, ?, CURRENT_TIMESTAMP)",
            (worker, job_id),
        )

        assert queue.reset_hung_jobs(timeout_seconds=600) == 0

    def test_a_beacon_counts_even_if_the_worker_row_was_deleted(self, queue: JobQueue) -> None:
        worker = _worker(queue, "w")
        job_id = _job(queue)
        _claim(queue, worker, job_id)
        _age_job(queue, job_id)
        conn = queue._get_conn()
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "INSERT INTO worker_heartbeats (worker_id, job_id, heartbeat_at) "
            "VALUES (?, ?, CURRENT_TIMESTAMP)",
            (worker, job_id),
        )
        conn.execute("DELETE FROM workers WHERE id = ?", (worker,))

        assert queue.reset_hung_jobs(timeout_seconds=600) == 0

    @pytest.mark.parametrize(
        ("status", "heartbeat_age"),
        [("busy", 3000), ("dead", 0), ("hung", 0)],
        ids=["silent", "dead", "hung"],
    )
    def test_a_worker_without_life_gives_its_job_back(
        self, queue: JobQueue, status: str, heartbeat_age: int
    ) -> None:
        worker = _worker(queue, "w")
        job_id = _job(queue)
        _claim(queue, worker, job_id)
        _age_job(queue, job_id)
        queue._get_conn().execute(
            "UPDATE workers SET status = ?, last_heartbeat = datetime('now', ?) WHERE id = ?",
            (status, f"-{heartbeat_age} seconds", worker),
        )

        assert queue.reset_hung_jobs(timeout_seconds=600) == 1

        job = queue.get_job(job_id)
        assert job is not None
        assert (job.status, job.worker_id, job.started_at) == ("pending", None, None)

    def test_a_missing_worker_row_gives_the_job_back(self, queue: JobQueue) -> None:
        worker = _worker(queue, "w")
        job_id = _job(queue)
        _claim(queue, worker, job_id)
        _age_job(queue, job_id)
        conn = queue._get_conn()
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DELETE FROM workers WHERE id = ?", (worker,))

        assert queue.reset_hung_jobs(timeout_seconds=600) == 1

    def test_a_recent_job_is_left_alone(self, queue: JobQueue) -> None:
        worker = _worker(queue, "w", heartbeat_age=3000)
        job_id = _job(queue)
        _claim(queue, worker, job_id)

        assert queue.reset_hung_jobs(timeout_seconds=600) == 0

    def test_a_short_timeout_keeps_the_shared_heartbeat_grace(self, queue: JobQueue) -> None:
        worker = _worker(queue, "w", heartbeat_age=60)
        job_id = _job(queue)
        _claim(queue, worker, job_id)
        _age_job(queue, job_id, seconds=30)

        assert queue.reset_hung_jobs(timeout_seconds=10) == 0

    def test_a_build_leaves_another_sessions_job_alone(self, queue: JobQueue) -> None:
        """Even with a silent worker: a long cell in build A, the reset from build B."""
        worker = _worker(queue, "w", session_id="A", heartbeat_age=3000)
        job_id = _job(queue, session_id="A")
        _claim(queue, worker, job_id)
        _age_job(queue, job_id)

        assert queue.reset_hung_jobs(timeout_seconds=600, session_id="B") == 0

        assert _status(queue, job_id) == "processing"

    def test_a_builds_own_silent_worker_counts_as_alive(self, queue: JobQueue) -> None:
        """The owning pool's monitor vouches for its rows (``worker_liveness``)."""
        worker = _worker(queue, "w", session_id="A", heartbeat_age=3000)
        job_id = _job(queue, session_id="A")
        _claim(queue, worker, job_id)
        _age_job(queue, job_id)

        assert queue.reset_hung_jobs(timeout_seconds=600, session_id="A") == 0

    def test_a_build_recovers_its_own_dead_workers_job(self, queue: JobQueue) -> None:
        worker = _worker(queue, "w", session_id="A")
        job_id = _job(queue, session_id="A")
        _claim(queue, worker, job_id)
        _age_job(queue, job_id)
        queue._get_conn().execute("UPDATE workers SET status='dead' WHERE id=?", (worker,))

        assert queue.reset_hung_jobs(timeout_seconds=600, session_id="A") == 1

    def test_a_build_recovers_an_unstamped_job(self, queue: JobQueue) -> None:
        worker = _worker(queue, "w", status="dead")
        job_id = _job(queue)
        _claim(queue, worker, job_id)
        _age_job(queue, job_id)

        assert queue.reset_hung_jobs(timeout_seconds=600, session_id="B") == 1

    def test_maintenance_callers_see_every_session(self, queue: JobQueue) -> None:
        worker = _worker(queue, "w", session_id="A", heartbeat_age=3000)
        job_id = _job(queue, session_id="A")
        _claim(queue, worker, job_id)
        _age_job(queue, job_id)

        assert queue.reset_hung_jobs(timeout_seconds=600) == 1

    def test_an_exhausted_job_fails_instead_of_waiting_forever(self, queue: JobQueue) -> None:
        """``get_next_job`` never claims it again, so ``pending`` would be a dead end."""
        worker = _worker(queue, "w", status="dead")
        job_id = _job(queue, max_attempts=1)
        _claim(queue, worker, job_id)
        _age_job(queue, job_id)

        assert queue.reset_hung_jobs(timeout_seconds=600) == 0

        job = queue.get_job(job_id)
        assert job is not None
        assert job.status == "failed"
        assert job.error == JobQueue.HUNG_EXHAUSTED_ERROR_MESSAGE
        assert job.completed_at is not None

    def test_an_exhausted_job_of_a_live_worker_keeps_running(self, queue: JobQueue) -> None:
        worker = _worker(queue, "w")
        job_id = _job(queue, max_attempts=1)
        _claim(queue, worker, job_id)
        _age_job(queue, job_id)

        assert queue.reset_hung_jobs(timeout_seconds=600) == 0

        assert _status(queue, job_id) == "processing"

    def test_cleanup_all_passes_the_session_on(self, queue: JobQueue) -> None:
        worker = _worker(queue, "w", session_id="A", heartbeat_age=3000)
        job_id = _job(queue, session_id="A")
        _claim(queue, worker, job_id)
        _age_job(queue, job_id)

        assert queue.cleanup_all(session_id="B")["hung_jobs_reset"] == 0

        assert _status(queue, job_id) == "processing"


class TestForcedInterleaving:
    def test_reset_then_stale_finish_leaves_one_result(self, queue: JobQueue) -> None:
        """The whole schedule: a silent worker loses its job, then finishes anyway."""
        first = _worker(queue, "first", heartbeat_age=3000)
        second = _worker(queue, "second")
        job_id = _job(queue)
        _claim(queue, first, job_id)
        _age_job(queue, job_id)

        assert queue.reset_hung_jobs(timeout_seconds=600) == 1
        _claim(queue, second, job_id)

        assert (
            queue.update_job_status(job_id, "completed", result='{"by":1}', worker_id=first)
            is False
        )
        assert (
            queue.update_job_status(job_id, "completed", result='{"by":2}', worker_id=second)
            is True
        )

        row = (
            queue._get_conn()
            .execute("SELECT status, result, worker_id, attempts FROM jobs WHERE id = ?", (job_id,))
            .fetchone()
        )
        assert tuple(row) == ("completed", '{"by":2}', second, 2)
