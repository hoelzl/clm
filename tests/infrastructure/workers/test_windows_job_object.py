"""Tests for ``windows_job_object.WorkerJobObject``.

The critical behavior we are guarding: closing the job handle kills the
entire process tree of every assigned worker, including grandchildren.
This is what the Fix 1 changes in ``DirectWorkerExecutor`` rely on to
prevent Jupyter kernel orphans on Windows.

These tests spawn tiny helper Python processes — NOT real clm workers —
so they are fast and self-contained. The Windows-only tests additionally
require psutil for process-tree inspection.
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from clm.infrastructure.workers.windows_job_object import WorkerJobObject

# ---------------------------------------------------------------------------
# Cross-platform smoke tests
# ---------------------------------------------------------------------------


def test_noop_on_non_windows_does_not_crash():
    """On any platform, constructing and using WorkerJobObject is safe."""
    job = WorkerJobObject()
    # assign with no process should be tolerated (we pass a finished one)
    finished = subprocess.Popen(
        [sys.executable, "-c", "pass"],
    )
    finished.wait(timeout=10)
    job.assign(finished)
    job.close()
    job.close()  # idempotent


def test_close_is_idempotent():
    """Multiple close() calls must not raise."""
    job = WorkerJobObject()
    job.close()
    job.close()
    job.close()


# ---------------------------------------------------------------------------
# Windows-only integration tests
# ---------------------------------------------------------------------------


windows_only = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows-only JobObject behavior",
)


try:
    import psutil  # type: ignore[import-untyped]

    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False


requires_psutil = pytest.mark.skipif(
    not HAS_PSUTIL,
    reason="psutil is required for process-tree verification",
)


def _wait_for_exit(pid: int, timeout: float = 5.0) -> bool:
    """Return True once ``pid`` is no longer running (or never was)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not psutil.pid_exists(pid):
            return True
        try:
            proc = psutil.Process(pid)
            if proc.status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.05)
    return not psutil.pid_exists(pid)


def _spawn_sleeping_child(duration: float = 60.0) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({duration})"],
    )


def _spawn_grandparent_that_spawns_grandchild() -> subprocess.Popen:
    """Spawn a process that spawns its own child and prints the grandchild pid.

    The outer process sleeps for 60 s. The inner process sleeps for 60 s.
    Both should die when the JobObject is closed.
    """
    helper = (
        "import subprocess, sys, time;"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
        "print(child.pid, flush=True);"
        "time.sleep(60)"
    )
    return subprocess.Popen(
        [sys.executable, "-c", helper],
        stdout=subprocess.PIPE,
        text=True,
    )


@windows_only
def test_creates_job_handle_on_windows():
    job = WorkerJobObject()
    try:
        assert job._handle is not None
        assert isinstance(job._handle, int)
        assert job._handle > 0
    finally:
        job.close()


@windows_only
@requires_psutil
def test_closing_job_kills_assigned_child():
    """The decisive single-process test: assigned child dies on close."""
    job = WorkerJobObject()
    child = _spawn_sleeping_child()
    try:
        assert job._handle is not None, "Windows should create a real job"
        job.assign(child)
        assert psutil.pid_exists(child.pid)

        job.close()

        assert _wait_for_exit(child.pid, timeout=5.0), (
            f"Child pid {child.pid} survived JobObject close — "
            f"Windows process-tree cleanup is not working"
        )
    finally:
        if child.poll() is None:
            child.kill()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


@windows_only
@requires_psutil
def test_closing_job_kills_grandchildren():
    """The kernel-leak regression test.

    This simulates the CLM process tree:
        WorkerJobObject owner   (stands in for CLM)
          └── direct child      (stands in for clm.workers.notebook)
                └── grandchild  (stands in for the Jupyter kernel)

    Closing the job must kill BOTH the child and the grandchild.
    Before Fix 1, the grandchild would survive on Windows and become an
    orphaned ~80 MB process — that is the machine-wrecking leak.
    """
    job = WorkerJobObject()
    parent = _spawn_grandparent_that_spawns_grandchild()
    grandchild_pid: int | None = None
    try:
        assert job._handle is not None, "Windows should create a real job"
        job.assign(parent)

        # Read the grandchild pid printed by the helper
        assert parent.stdout is not None
        line = parent.stdout.readline().strip()
        assert line, "Helper did not print a grandchild pid"
        grandchild_pid = int(line)
        assert grandchild_pid > 0

        # Sanity: both processes must be alive before we test the kill
        assert psutil.pid_exists(parent.pid)
        assert psutil.pid_exists(grandchild_pid)

        job.close()

        assert _wait_for_exit(parent.pid, timeout=5.0), (
            f"Parent pid {parent.pid} survived JobObject close"
        )
        assert _wait_for_exit(grandchild_pid, timeout=5.0), (
            f"Grandchild pid {grandchild_pid} survived JobObject close — "
            f"this is the kernel-leak regression Fix 1 is supposed to "
            f"prevent"
        )
    finally:
        # Defensive: if the test failed for any reason, make sure we
        # never leave the test's own orphans behind.
        if parent.poll() is None:
            parent.kill()
            try:
                parent.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        if grandchild_pid is not None and psutil.pid_exists(grandchild_pid):
            try:
                psutil.Process(grandchild_pid).kill()
            except psutil.NoSuchProcess:
                pass


@windows_only
@requires_psutil
def test_assign_after_close_is_safe():
    """Assigning a process after close() must not raise and must not track it."""
    job = WorkerJobObject()
    job.close()

    child = _spawn_sleeping_child(duration=2)
    try:
        job.assign(child)  # no-op — the job is already closed
    finally:
        child.kill()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
