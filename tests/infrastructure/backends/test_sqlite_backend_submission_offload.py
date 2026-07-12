"""Regression tests for the build-progress-stall fix.

The ``clm build`` progress bar advances only from
``SqliteBackend.wait_for_completion``'s poll loop. Before the fix, the
synchronous job-submission body (job-cache probe, worker-availability wait,
payload JSON serialization, jobs-DB INSERT) ran inline on the event loop, so a
submission burst starved that poll loop and the bar froze for a long time while
the workers raced ahead. The fix moves that body onto a single dedicated thread
and gates per-operation concurrency with a semaphore.

These tests lock in the two observable properties of that fix:

* the submission body runs OFF the event-loop thread, and
* a coroutine running concurrently with submission keeps getting scheduled
  (i.e. the loop is not starved).
"""

import asyncio
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from clm.infrastructure.backends.sqlite_backend import (
    SUBMISSION_CONCURRENCY,
    SqliteBackend,
)
from clm.infrastructure.database.job_queue import JobQueue
from clm.infrastructure.database.schema import init_database
from clm.infrastructure.messaging.base_classes import Payload
from clm.infrastructure.operation import Operation


class _MockOperation(Operation):
    """Minimal notebook-service operation."""

    @property
    def service_name(self) -> str:
        return "notebook-processor"

    async def execute(self, backend, *args, **kwargs):  # pragma: no cover - unused
        pass


class _MockPayload(Payload):
    correlation_id: str = "cid"
    input_file: str = "in.py"
    input_file_name: str = "in.py"
    output_file: str = "out.ipynb"
    data: str = "content"


@pytest.fixture
def backend(tmp_path: Path):
    db_path = tmp_path / "jobs.db"
    init_database(db_path)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    be = SqliteBackend(
        db_path=db_path,
        workspace_path=workspace,
        skip_worker_check=True,  # no workers in a unit test
    )
    yield be
    be.active_jobs.clear()  # avoid the 5s shutdown wait for unprocessed jobs


@pytest.mark.asyncio
async def test_submission_runs_off_the_event_loop_thread(backend):
    """The synchronous submission tail runs on the dedicated submit thread."""
    main_thread = threading.current_thread()
    seen: dict[str, threading.Thread] = {}
    real = SqliteBackend._submit_job_blocking

    def spy(self, payload, job_type, force_execution=False):
        seen["thread"] = threading.current_thread()
        return real(self, payload, job_type, force_execution)

    try:
        with patch.object(SqliteBackend, "_submit_job_blocking", spy):
            await backend.execute_operation(_MockOperation(), _MockPayload())

        assert seen["thread"] is not main_thread
        assert seen["thread"].name.startswith("clm-submit")
        # The job was still enqueued and tracked for the poll loop.
        assert len(backend.active_jobs) == 1
        job_queue = JobQueue(backend.db_path)
        try:
            assert job_queue.get_next_job("notebook") is not None
        finally:
            job_queue.close()
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_submission_does_not_starve_the_event_loop(backend):
    """A concurrent coroutine keeps getting scheduled while jobs are submitted.

    With the submission body offloaded, a slow ``add_job`` no longer blocks the
    loop, so a 5 ms heartbeat ticks many times across ~0.4 s of submission. On
    the pre-fix inline path the heartbeat would be starved to a few ticks.
    """
    real_add_job = JobQueue.add_job

    def slow_add_job(self, *args, **kwargs):
        time.sleep(0.02)  # simulate a slow synchronous submission step
        return real_add_job(self, *args, **kwargs)

    ticks = 0
    stop = asyncio.Event()

    async def heartbeat():
        nonlocal ticks
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0.005)

    try:
        with patch.object(JobQueue, "add_job", slow_add_job):
            hb = asyncio.ensure_future(heartbeat())
            await asyncio.gather(
                *(
                    backend.execute_operation(
                        _MockOperation(),
                        _MockPayload(input_file=f"in{i}.py", output_file=f"out{i}.ipynb"),
                    )
                    for i in range(20)
                )
            )
            stop.set()
            await hb

        # 20 jobs * 20 ms serialized on the single submit thread ~= 0.4 s; a
        # non-starved 5 ms heartbeat ticks dozens of times in that window.
        assert ticks > 10, f"event loop appears starved during submission (ticks={ticks})"
        assert len(backend.active_jobs) == 20
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_abandoned_shielded_submission_exception_is_retrieved(backend, caplog):
    """When the caller is cancelled and the shielded submission task then
    raises, the done-callback must retrieve the exception (debug-logging it)
    so asyncio never emits "exception was never retrieved" at teardown
    (#617/#636 follow-up, Finding 5.3)."""
    import logging

    release = threading.Event()

    def failing_submit(self, payload, job_type, force_execution=False):
        release.wait(5)
        raise RuntimeError("submit exploded after the caller was cancelled")

    try:
        with patch.object(SqliteBackend, "_submit_job_blocking", failing_submit):
            with caplog.at_level(
                logging.DEBUG, logger="clm.infrastructure.backends.sqlite_backend"
            ):
                op_task = asyncio.ensure_future(
                    backend.execute_operation(_MockOperation(), _MockPayload())
                )
                await asyncio.sleep(0.05)  # let the submission start and block
                op_task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await op_task

                # Now let the abandoned shielded task raise, and wait for the
                # done-callback to retrieve and log the exception.
                release.set()
                for _ in range(100):
                    if any(
                        "Shielded job submission raised" in rec.message for rec in caplog.records
                    ):
                        break
                    await asyncio.sleep(0.02)

        assert any("Shielded job submission raised" in rec.message for rec in caplog.records), (
            "the abandoned shielded task's exception was never retrieved"
        )
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_submission_exception_still_propagates_to_caller(backend):
    """Retrieving the exception in the done-callback must not swallow it on
    the normal (non-cancelled) path — the caller still sees the raise."""

    def failing_submit(self, payload, job_type, force_execution=False):
        raise RuntimeError("submit exploded")

    try:
        with patch.object(SqliteBackend, "_submit_job_blocking", failing_submit):
            with pytest.raises(RuntimeError, match="submit exploded"):
                await backend.execute_operation(_MockOperation(), _MockPayload())
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_submission_semaphore_gate_is_installed(backend):
    """execute_operation is gated by a bounded concurrency semaphore."""
    await backend.execute_operation(_MockOperation(), _MockPayload())
    try:
        sem = backend._submission_semaphore
        assert isinstance(sem, asyncio.Semaphore)
        # All permits released after the single call completes.
        assert sem._value == SUBMISSION_CONCURRENCY
    finally:
        await backend.shutdown()
