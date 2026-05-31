"""Unit tests for ``MitmproxyManager`` plumbing that needs no real mitmdump.

The stdout reader thread (issue #165 P3) is the important piece here: a
multi-hour build must never deadlock because mitmdump filled the OS pipe
buffer while the manager wasn't reading it. We exercise the reader against a
plain Python subprocess that emits far more output than a pipe buffer holds.
"""

from __future__ import annotations

import subprocess
import sys
import time

from clm.infrastructure.http_replay_mitm.proxy_manager import MitmproxyManager


def _manager() -> MitmproxyManager:
    # cassette_path is irrelevant for the reader-thread mechanics.
    return MitmproxyManager(cassette_path="unused.yaml")


def test_reader_thread_drains_large_output_without_deadlock(tmp_path) -> None:
    """A subprocess that prints far more than a pipe buffer (~64 KiB) must run
    to completion — proving the reader thread drains continuously instead of
    letting the child block on a full pipe."""
    # ~50k lines * ~16 bytes ≈ 800 KiB, an order of magnitude past the pipe buffer.
    code = "import sys\nfor i in range(50000):\n    print('mitm-log-line', i)\n"
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    mgr = _manager()
    mgr._process = proc
    mgr._start_reader()

    # Without draining, the child would block on write and never exit. Give it
    # a generous-but-bounded window; a hang here is the deadlock we prevent.
    proc.wait(timeout=30.0)
    assert proc.returncode == 0

    output = mgr._drain_output()
    # Only the tail is retained (bounded ring buffer), so assert on the end.
    assert "mitm-log-line 49999" in output
    # And the buffer is bounded — it must not have grown unbounded.
    assert len(mgr._output) <= 1000


def test_reader_thread_joined_and_output_available_after_exit(tmp_path) -> None:
    """After the child exits, ``_drain_output`` returns its captured tail and
    the reader thread is joinable (no per-build thread leak)."""
    code = "print('hello from child')\n"
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    mgr = _manager()
    mgr._process = proc
    mgr._start_reader()
    proc.wait(timeout=10.0)

    # Reader thread should finish on EOF shortly after the process exits.
    deadline = time.monotonic() + 5.0
    while mgr._reader_thread is not None and mgr._reader_thread.is_alive():
        if time.monotonic() > deadline:
            break
        time.sleep(0.02)

    assert "hello from child" in mgr._drain_output()
