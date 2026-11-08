"""Cancelled and attempts-exhausted jobs end the wait, and cancels stay in their build (#799 C9).

Before this, both cases left ``wait_for_completion`` polling until the stall
detector aborted the build (1200 s by default), and watch mode's cancel could
cancel another build's jobs for the same file.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from clm.infrastructure.database.job_queue import JobQueue
from clm.infrastructure.database.schema import init_database
from tests.infrastructure.backends.test_sqlite_backend_resilience import (
    _backend,
    _MockOp,
    _MockPayload,
    _StubReporter,
)


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    path = tmp_path / "jobs.db"
    init_database(path)
    return path


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Path:
    path = tmp_path / "workspace"
    path.mkdir()
    return path


def _sql(db: Path, statement: str, params: tuple = ()) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(statement, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


class TestWaitForCompletion:
    @pytest.mark.asyncio
    async def test_a_job_cancelled_from_outside_ends_the_wait_as_a_failure(
        self, temp_db: Path, temp_workspace: Path
    ) -> None:
        reporter = _StubReporter()
        backend = _backend(temp_db, temp_workspace, build_reporter=reporter)
        try:
            await backend.execute_operation(_MockOp(), _MockPayload())
            job_id = next(iter(backend.active_jobs))
            # What ``clm jobs cancel`` does from another terminal
            JobQueue(temp_db).cancel_pending_jobs()

            result = await asyncio.wait_for(backend.wait_for_completion(), timeout=30)

            assert result is False
            assert backend.active_jobs == {}
            [error] = reporter.errors
            assert error.category == "job_cancelled"
            assert error.job_id == job_id
            assert error.details["output_file"] == "out.ipynb"
            assert ("in.py", "notebook", job_id, False) in reporter.completed
        finally:
            backend.active_jobs.clear()
            await backend.shutdown()

    @pytest.mark.asyncio
    async def test_a_dead_workers_exhausted_job_fails_instead_of_waiting(
        self, temp_db: Path, temp_workspace: Path
    ) -> None:
        """Review finding F5 on #1201: the dead-worker sweep requeued it into a dead end."""
        backend = _backend(temp_db, temp_workspace)
        try:
            await backend.execute_operation(_MockOp(), _MockPayload())
            job_id = next(iter(backend.active_jobs))
            [(worker_id,)] = _sql(
                temp_db,
                "INSERT INTO workers (worker_type, container_id, status) "
                "VALUES ('notebook', 'dead', 'dead') RETURNING id",
            )
            _sql(
                temp_db,
                "UPDATE jobs SET status = 'processing', worker_id = ?, attempts = max_attempts, "
                "started_at = CURRENT_TIMESTAMP WHERE id = ?",
                (worker_id, job_id),
            )

            assert backend._cleanup_dead_worker_jobs() == 0

            [(status, error)] = _sql(
                temp_db, "SELECT status, error FROM jobs WHERE id = ?", (job_id,)
            )
            assert (status, error) == ("failed", JobQueue.DEAD_WORKER_EXHAUSTED_ERROR_MESSAGE)
            assert await asyncio.wait_for(backend.wait_for_completion(), timeout=30) is False
        finally:
            backend.active_jobs.clear()
            await backend.shutdown()


class TestRequeueJobs:
    def test_requeues_jobs_with_attempts_left_and_fails_the_rest(self, temp_db: Path) -> None:
        queue = JobQueue(temp_db)
        try:
            ids = [queue.add_job("notebook", f"{n}.py", f"{n}.html", "h", {}) for n in ("a", "b")]
            queue._get_conn().execute(
                "UPDATE jobs SET status = 'processing', worker_id = 1, attempts = 1, "
                "started_at = CURRENT_TIMESTAMP, max_attempts = CASE id WHEN ? THEN 1 ELSE 3 END",
                (ids[1],),
            )

            assert queue.requeue_jobs(ids, exhausted_error="gone") == (1, 1)

            rows = (
                queue._get_conn()
                .execute("SELECT status, worker_id, started_at, error FROM jobs ORDER BY id")
                .fetchall()
            )
            assert [tuple(r) for r in rows] == [
                ("pending", None, None, None),
                ("failed", 1, rows[1][2], "gone"),
            ]
        finally:
            queue.close()


class TestCancelScope:
    def _job(self, queue: JobQueue, session_id: str | None, status: str = "pending") -> int:
        job_id = queue.add_job("notebook", "deck.py", "deck.html", "h", {}, session_id=session_id)
        queue._get_conn().execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))
        return job_id

    def test_a_build_cancels_only_its_own_and_unstamped_jobs(self, temp_db: Path) -> None:
        queue = JobQueue(temp_db)
        try:
            own = self._job(queue, "A")
            running = self._job(queue, "A", status="processing")
            unstamped = self._job(queue, None)
            other = self._job(queue, "B")
            done = self._job(queue, "A", status="completed")

            cancelled = queue.cancel_jobs_for_file("deck.py", "watch_mode", session_id="A")

            assert cancelled == [own, running, unstamped]
            statuses = dict(queue._get_conn().execute("SELECT id, status FROM jobs").fetchall())
            assert statuses[other] == "pending"
            assert statuses[done] == "completed"
        finally:
            queue.close()

    def test_without_a_session_every_build_is_affected(self, temp_db: Path) -> None:
        queue = JobQueue(temp_db)
        try:
            ids = [self._job(queue, "A"), self._job(queue, "B")]

            assert queue.cancel_jobs_for_file("deck.py") == ids
        finally:
            queue.close()

    @pytest.mark.asyncio
    async def test_watch_mode_passes_its_session(self, temp_db: Path, temp_workspace: Path) -> None:
        backend = _backend(temp_db, temp_workspace)
        backend.worker_session_id = "A"
        try:
            queue = JobQueue(temp_db)
            try:
                other = self._job(queue, "B")
                other_input = queue.get_job(other)
                assert other_input is not None
                count = await backend.cancel_jobs_for_file(Path(other_input.input_file))
            finally:
                queue.close()

            assert count == 0
            [(status,)] = _sql(temp_db, "SELECT status FROM jobs WHERE id = ?", (other,))
            assert status == "pending"
        finally:
            await backend.shutdown()
