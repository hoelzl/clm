"""A job must not run twice, and a stale worker must not overwrite it (#799 C3 + C5).

The interleavings are forced by hand against a real SQLite queue: each test
drives two workers through a schedule that used to end in a double run or in a
stale write.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from clm.infrastructure.api.job_queue_adapter import ApiJobQueue
from clm.infrastructure.api.server import WorkerApiServer
from clm.infrastructure.database.job_queue import JobQueue
from clm.infrastructure.database.schema import init_database
from clm.infrastructure.database.worker_heartbeats import touch_job_beacon
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


def _beacon(queue: JobQueue, worker_id: int, job_id: int, *, age: int = 0) -> None:
    """The beacon a worker refreshes while it holds *job_id*."""
    conn = queue._get_conn()
    touch_job_beacon(conn, worker_id, job_id)
    conn.execute(
        "UPDATE worker_heartbeats SET heartbeat_at = datetime('now', ?) WHERE worker_id = ?",
        (f"-{age} seconds", worker_id),
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

    @pytest.mark.parametrize("status", ["completed", "failed"])
    def test_a_retried_write_that_already_landed_is_acknowledged(
        self, queue: JobQueue, status: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The API client retries on a timeout even when the server applied the write."""
        worker = _worker(queue, "w")
        job_id = _job(queue)
        _claim(queue, worker, job_id)
        assert queue.update_job_status(job_id, status, error="e", worker_id=worker) is True

        with caplog.at_level("WARNING"):
            assert queue.update_job_status(job_id, status, error="e", worker_id=worker) is True

        assert "rejected" not in caplog.text

    def test_the_same_status_from_another_worker_is_still_rejected(self, queue: JobQueue) -> None:
        first = _worker(queue, "first")
        second = _worker(queue, "second")
        job_id = _job(queue)
        _claim(queue, second, job_id)
        assert queue.update_job_status(job_id, "completed", worker_id=second) is True

        assert queue.update_job_status(job_id, "completed", worker_id=first) is False

    def test_finishing_a_cancelled_job_is_not_a_warning(
        self, queue: JobQueue, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Routine in watch mode: the file changed while its job ran."""
        worker = _worker(queue, "w")
        job_id = _job(queue)
        _claim(queue, worker, job_id)
        queue.cancel_jobs_for_file("deck.py")

        with caplog.at_level("WARNING"):
            assert queue.update_job_status(job_id, "completed", worker_id=worker) is False

        assert "rejected" not in caplog.text

    def test_a_stale_holders_write_is_a_warning(
        self, queue: JobQueue, caplog: pytest.LogCaptureFixture
    ) -> None:
        first = _worker(queue, "first")
        second = _worker(queue, "second")
        job_id = _job(queue)
        _claim(queue, second, job_id)

        with caplog.at_level("WARNING"):
            queue.update_job_status(job_id, "completed", worker_id=first)

        assert f"rejected 'completed' from worker {first}" in caplog.text

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

    def test_a_docker_workers_mid_job_heartbeat_refreshes_its_beacon(
        self, client: TestClient, queue: JobQueue
    ) -> None:
        """Only the beacon: the monitor reads ``last_heartbeat`` to spot a wedged container."""
        worker = _worker(queue, "w", heartbeat_age=3000)
        job_id = _job(queue)
        _claim(queue, worker, job_id)
        _age_job(queue, job_id)

        response = client.post(
            "/api/worker/heartbeat", json={"worker_id": worker, "job_id": job_id}
        )

        assert response.status_code == 200
        assert queue.reset_hung_jobs(timeout_seconds=600) == 0
        stale = (
            queue._get_conn()
            .execute(
                "SELECT last_heartbeat < datetime('now', '-2000 seconds') FROM workers WHERE id = ?",
                (worker,),
            )
            .fetchone()[0]
        )
        assert stale

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

    def test_a_worker_sends_its_id_and_logs_when_rejected(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        worker = MagicMock(spec=Worker)
        worker.worker_id = 7
        worker.job_queue = MagicMock()
        worker.job_queue.update_job_status.return_value = False

        with caplog.at_level("INFO", logger="clm.infrastructure.workers.worker_base"):
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
        alive = _worker(queue, "alive", session_id="A", heartbeat_age=3000)
        second = _worker(queue, "second", session_id="A")
        job_id = _job(queue, session_id="A")
        _claim(queue, alive, job_id)
        _age_job(queue, job_id)
        _beacon(queue, alive, job_id)

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
        [("busy", 3000), ("dead", 0), ("hung", 0), ("idle", 0)],
        ids=["silent", "dead", "hung", "gave-up"],
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
        worker = _worker(queue, "w", heartbeat_age=3000)
        job_id = _job(queue)
        _claim(queue, worker, job_id)
        _age_job(queue, job_id, seconds=30)
        _beacon(queue, worker, job_id, age=60)

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
        _beacon(queue, worker, job_id)

        assert queue.reset_hung_jobs(timeout_seconds=600) == 0

        assert _status(queue, job_id) == "processing"

    def test_a_fresh_idle_heartbeat_does_not_vouch_for_an_abandoned_job(
        self, queue: JobQueue
    ) -> None:
        """Review finding: the terminal write failed and the worker went back to polling."""
        worker = _worker(queue, "w", status="idle")
        job_id = _job(queue)
        _claim(queue, worker, job_id)
        _age_job(queue, job_id)

        assert queue.reset_hung_jobs(timeout_seconds=600) == 1

    def test_a_beacon_for_another_job_does_not_vouch(self, queue: JobQueue) -> None:
        worker = _worker(queue, "w", heartbeat_age=3000)
        abandoned = _job(queue)
        _claim(queue, worker, abandoned)
        _age_job(queue, abandoned)
        current = _job(queue)
        queue._get_conn().execute(
            "UPDATE jobs SET status = 'processing', worker_id = ? WHERE id = ?",
            (worker, current),
        )
        _beacon(queue, worker, current)

        assert queue.reset_hung_jobs(timeout_seconds=600) == 1

        assert _status(queue, abandoned) == "pending"
        assert _status(queue, current) == "processing"

    def test_a_stale_beacon_does_not_vouch(self, queue: JobQueue) -> None:
        worker = _worker(queue, "w", heartbeat_age=3000)
        job_id = _job(queue)
        _claim(queue, worker, job_id)
        _age_job(queue, job_id)
        _beacon(queue, worker, job_id, age=700)

        assert queue.reset_hung_jobs(timeout_seconds=600) == 1

    def test_prune_leaves_another_builds_silent_cell_alone(self, queue: JobQueue) -> None:
        """Review finding: ``clm db prune`` has no session, so only the beacon can vouch."""
        worker = _worker(queue, "w", session_id="A", heartbeat_age=3000)
        job_id = _job(queue, session_id="A")
        _claim(queue, worker, job_id)
        _age_job(queue, job_id, seconds=900)
        _beacon(queue, worker, job_id, age=20)

        assert queue.cleanup_all()["hung_jobs_reset"] == 0

        assert _status(queue, job_id) == "processing"

    def test_recovery_reports_the_jobs_it_failed(self, queue: JobQueue) -> None:
        requeued = _job(queue)
        exhausted = _job(queue, max_attempts=1)
        dead = _worker(queue, "dead", status="dead")
        for job_id in (requeued, exhausted):
            queue._get_conn().execute(
                "UPDATE jobs SET status = 'processing', worker_id = ?, attempts = 1 WHERE id = ?",
                (dead, job_id),
            )
            _age_job(queue, job_id)

        result = queue.cleanup_all()

        assert (result["hung_jobs_reset"], result["hung_jobs_failed"]) == (1, 1)

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


class _GatedWorker(Worker):
    """Holds its job until the test releases it."""

    JOB_BEACON_INTERVAL = 0.05

    def __init__(self, worker_id: int, db_path: Path) -> None:
        super().__init__(worker_id, "notebook", db_path, poll_interval=0.05)
        self.started = threading.Event()
        self.release = threading.Event()

    def process_job(self, job) -> None:  # noqa: ANN001 - matches the base signature
        self.started.set()
        assert self.release.wait(timeout=15)


class TestWorkerBeacon:
    """The worker keeps its job's beacon fresh for as long as it holds the job."""

    def _beacon_row(self, queue: JobQueue, worker_id: int):
        return (
            queue._get_conn()
            .execute(
                "SELECT job_id, heartbeat_at FROM worker_heartbeats WHERE worker_id = ?",
                (worker_id,),
            )
            .fetchone()
        )

    def test_a_silent_job_keeps_its_beacon_and_is_not_requeued(
        self, queue: JobQueue, db_path: Path
    ) -> None:
        """Regression for the review of #1201: nothing else vouches mid-job outside the session."""
        worker_id = _worker(queue, "w", status="idle")
        job_id = _job(queue)
        worker = _GatedWorker(worker_id, db_path)
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()
        try:
            assert worker.started.wait(timeout=15)
            # The job looks long-running, and every heartbeat looks old.
            conn = queue._get_conn()
            _age_job(queue, job_id)
            conn.execute(
                "UPDATE workers SET last_heartbeat = datetime('now', '-3000 seconds') WHERE id = ?",
                (worker_id,),
            )
            conn.execute(
                "UPDATE worker_heartbeats SET heartbeat_at = datetime('now', '-3000 seconds')"
            )

            # The background refresh brings the beacon back within its interval.
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                row = self._beacon_row(queue, worker_id)
                fresh = conn.execute(
                    "SELECT heartbeat_at > datetime('now', '-60 seconds') "
                    "FROM worker_heartbeats WHERE worker_id = ?",
                    (worker_id,),
                ).fetchone()
                if fresh is not None and fresh[0]:
                    break
                time.sleep(0.05)
            else:
                pytest.fail("the worker never refreshed its job's beacon")
            assert row is not None and row[0] == job_id

            assert queue.reset_hung_jobs(timeout_seconds=600) == 0
            assert _status(queue, job_id) == "processing"
        finally:
            worker.release.set()
            worker.stop()
            thread.join(timeout=15)

        assert _status(queue, job_id) == "completed"
        assert self._beacon_row(queue, worker_id) is None
