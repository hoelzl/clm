"""No worker job is submitted once a stage has aborted (issue #1270).

``SqliteBackend.execute_operation`` hands the synchronous submission tail
(job-cache probe, worker-availability wait, INSERT) to a single submit thread
and awaits it through ``asyncio.shield`` so a cancelled caller can never
strand an INSERTed row (#617). The flip side: when one operation of a stage
raises, the stage ``TaskGroup`` cancels its siblings, but every submission
already queued on the submit thread still ran to completion and INSERTed its
job *after* the build had aborted. #1090's log shows four such jobs
(``Job #10601``-``#10604``) submitted five seconds after the abort, never
counted and never processed.

The fix: ``Backend.discard_pending_submissions()``, which
``Course.process_stage_for_target`` calls when the stage fails. Submissions
started before the call are dropped before they reach the jobs DB;
submissions started after it proceed normally (watch mode reuses the
backend).
"""

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from attrs import frozen

from clm.core.course import Course
from clm.core.messaging.base_classes import Payload
from clm.core.operation import Operation
from clm.infrastructure.backends.sqlite_backend import SqliteBackend
from clm.infrastructure.database.schema import init_database


class _MockPayload(Payload):
    correlation_id: str = "cid"
    input_file: str = "in.py"
    input_file_name: str = "in.py"
    output_file: str = "out.ipynb"
    data: str = "content"


@frozen
class _SubmittingOperation(Operation):
    """An operation that submits one notebook job, like ProcessNotebookOperation."""

    name: str

    @property
    def service_name(self) -> str:
        return "notebook-processor"

    async def execute(self, backend, *args, **kwargs):
        payload = _MockPayload(input_file=f"{self.name}.py", output_file=f"{self.name}.ipynb")
        await backend.execute_operation(self, payload)


@frozen
class _FailingOperation(Operation):
    """An operation whose input vanished: it raises before submitting."""

    before_raise: asyncio.Event

    @property
    def service_name(self) -> str:
        return "notebook-processor"

    async def execute(self, backend, *args, **kwargs):
        # Raise only once the sibling's submission is queued on the submit
        # thread, so the test exercises the "already in flight" case.
        await self.before_raise.wait()
        raise FileNotFoundError(2, "No such file or directory", "temperature_utils.py")


class _File:
    def __init__(self, op: Operation):
        self.op = op
        self.path = Path("f.py")

    async def get_processing_operation(self, *args, **kwargs):
        return self.op


def _count_jobs(backend: SqliteBackend) -> int:
    assert backend.job_queue is not None
    conn = backend.job_queue._get_conn()
    return conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]


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
        enable_progress_tracking=False,
    )
    yield be
    be.active_jobs.clear()  # avoid the shutdown wait for unprocessed jobs


async def _wait_until(predicate, timeout: float = 10.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not reached within timeout")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_queued_submission_is_dropped_when_the_stage_aborts(backend):
    """Regression test for #1270 item 1: a submission queued on the submit
    thread when a sibling operation fails must not INSERT a job."""
    executor = backend._ensure_submit_executor()
    # Occupy the single submit thread so the sibling's submission queues
    # behind it — the state #1090's late jobs were in when the abort hit.
    release = threading.Event()
    blocker_started = threading.Event()

    def _block() -> None:
        blocker_started.set()
        release.wait(30)

    blocker = executor.submit(_block)
    assert blocker_started.wait(10)

    queued = asyncio.Event()
    real_ensure = SqliteBackend._ensure_submit_executor

    def _ensure_and_signal(self):
        # Called by execute_operation right before it queues on the thread.
        queued.set()
        return real_ensure(self)

    course = SimpleNamespace(
        files=[_File(_SubmittingOperation("late")), _File(_FailingOperation(queued))],
        course_root=backend.workspace_path,
    )
    target = SimpleNamespace(name="t", output_root=Path("out"))

    try:
        with (
            patch.object(SqliteBackend, "_ensure_submit_executor", _ensure_and_signal),
            pytest.raises(BaseException) as excinfo,
        ):
            await Course.process_stage_for_target(course, 1, backend, target)  # type: ignore[arg-type]
        assert excinfo.group_contains(FileNotFoundError, depth=None)

        # The stage has aborted. Now let the submit thread run the queued
        # submission and wait for it to drain.
        release.set()
        blocker.result(timeout=10)
        await asyncio.get_running_loop().run_in_executor(
            None, executor.submit(lambda: None).result, 10
        )

        assert _count_jobs(backend) == 0, (
            "a job was submitted after the stage aborted (#1270): the queued "
            "submission must be discarded, not INSERTed"
        )
        assert backend.active_jobs == {}
    finally:
        release.set()
        await backend.shutdown()


@pytest.mark.asyncio
async def test_submissions_after_the_discard_still_run(backend):
    """The discard is scoped to submissions already in flight: a backend that
    is reused afterwards (watch mode) still submits normally."""
    try:
        backend.discard_pending_submissions()
        await backend.execute_operation(_SubmittingOperation("next"), _MockPayload())
        assert _count_jobs(backend) == 1
        assert len(backend.active_jobs) == 1
    finally:
        await backend.shutdown()


@pytest.mark.asyncio
async def test_discard_drops_a_submission_waiting_for_workers(backend):
    """A submission already running on the submit thread — e.g. blocked in the
    worker-availability wait, which can sleep for seconds — is dropped before
    its INSERT when the discard arrives meanwhile."""
    in_wait = threading.Event()
    release = threading.Event()

    def _slow_availability(job_type: str) -> int:
        in_wait.set()
        release.wait(30)
        return 1

    backend.skip_worker_check = False
    try:
        with patch.object(
            SqliteBackend, "_get_available_workers", lambda self, jt: _slow_availability(jt)
        ):
            task = asyncio.ensure_future(
                backend.execute_operation(_SubmittingOperation("slow"), _MockPayload())
            )
            await _wait_until(in_wait.is_set)
            task.cancel()
            backend.discard_pending_submissions()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        executor = backend._ensure_submit_executor()
        await asyncio.get_running_loop().run_in_executor(
            None, executor.submit(lambda: None).result, 10
        )

        assert _count_jobs(backend) == 0
        assert backend.active_jobs == {}
    finally:
        release.set()
        await backend.shutdown()


@pytest.mark.asyncio
async def test_a_caller_that_was_not_cancelled_sees_the_discard(backend):
    """A discarded submission raises SubmissionDiscardedError instead of
    returning as if the job had run, so a caller that was not cancelled never
    records an output that was never produced."""
    from clm.core.backend import SubmissionDiscardedError

    in_wait = threading.Event()
    release = threading.Event()

    def _slow_availability(job_type: str) -> int:
        in_wait.set()
        release.wait(30)
        return 1

    backend.skip_worker_check = False
    try:
        with patch.object(
            SqliteBackend, "_get_available_workers", lambda self, jt: _slow_availability(jt)
        ):
            task = asyncio.ensure_future(
                backend.execute_operation(_SubmittingOperation("kept"), _MockPayload())
            )
            await _wait_until(in_wait.is_set)
            backend.discard_pending_submissions()
            release.set()
            with pytest.raises(SubmissionDiscardedError):
                await task
        assert _count_jobs(backend) == 0
    finally:
        release.set()
        await backend.shutdown()
