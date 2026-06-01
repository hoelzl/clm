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

import pytest

from clm.infrastructure.http_replay_mitm import proxy_manager
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


class TestClientHostAndProxyUrl:
    """Bind/loopback host separation for Docker reachability (issue #165 P4).

    When the proxy binds a wildcard address so containers can reach it via
    ``host.docker.internal``, same-host clients (Direct workers, the readiness
    poll) must still connect via loopback — ``connect("0.0.0.0")`` is invalid
    on Windows.
    """

    def test_loopback_bind_is_unchanged(self) -> None:
        mgr = MitmproxyManager(cassette_path="unused.yaml", listen_host="127.0.0.1")
        mgr.listen_port = 54321
        assert mgr._client_host() == "127.0.0.1"
        assert mgr.proxy_url == "http://127.0.0.1:54321"

    def test_wildcard_bind_connects_via_loopback(self) -> None:
        mgr = MitmproxyManager(cassette_path="unused.yaml", listen_host="0.0.0.0")
        mgr.listen_port = 54321
        # Bind address stays the wildcard (what mitmdump --listen-host gets)...
        assert mgr.listen_host == "0.0.0.0"
        # ...but clients/poll use loopback so connect() is valid on Windows.
        assert mgr._client_host() == "127.0.0.1"
        assert mgr.proxy_url == "http://127.0.0.1:54321"

    def test_empty_host_is_treated_as_wildcard(self) -> None:
        # An empty listen_host binds all IPv4 interfaces (INADDR_ANY), so
        # clients/poll must still use loopback.
        mgr = MitmproxyManager(cassette_path="unused.yaml", listen_host="")
        mgr.listen_port = 7
        assert mgr._client_host() == "127.0.0.1"

    def test_concrete_host_is_used_verbatim(self) -> None:
        mgr = MitmproxyManager(cassette_path="unused.yaml", listen_host="192.168.1.5")
        mgr.listen_port = 8080
        assert mgr._client_host() == "192.168.1.5"
        assert mgr.proxy_url == "http://192.168.1.5:8080"


class TestStartCommandTraceDir:
    """The forensic trace dir (issue #165 P5) reaches the addon via a
    ``--set clm_trace_dir=`` option only when configured."""

    class _FakeProc:
        stdout = None

        def poll(self):
            return None

    def _captured_cmd(self, monkeypatch, **kwargs) -> list[str]:
        captured: dict[str, list[str]] = {}

        def _fake_popen(cmd, **_kw):
            captured["cmd"] = cmd
            return self._FakeProc()

        monkeypatch.setattr(proxy_manager, "_locate_mitmdump", lambda: "mitmdump")
        monkeypatch.setattr(proxy_manager, "_pick_free_port", lambda host: 12345)
        monkeypatch.setattr(proxy_manager.subprocess, "Popen", _fake_popen)
        monkeypatch.setattr(MitmproxyManager, "_wait_for_ready", lambda self: None)
        mgr = MitmproxyManager(cassette_path="unused.yaml", **kwargs)
        mgr.start()
        return captured["cmd"]

    def test_trace_dir_adds_clm_trace_dir_set(self, monkeypatch, tmp_path) -> None:
        cmd = self._captured_cmd(monkeypatch, trace_dir=tmp_path)
        assert any(str(part) == f"clm_trace_dir={tmp_path}" for part in cmd)

    def test_no_trace_dir_omits_clm_trace_dir_set(self, monkeypatch) -> None:
        cmd = self._captured_cmd(monkeypatch)
        assert not any("clm_trace_dir=" in str(part) for part in cmd)


def _raise_oserror(*_args, **_kwargs):
    """Stand-in for ``socket.create_connection`` that always refuses."""
    raise OSError("connection refused")


class _ReadyFakeProc:
    """Minimal ``Popen`` stand-in for ``_wait_for_ready`` diagnostics.

    ``rc=None`` models an alive-but-not-listening process (the overload case);
    a non-None ``rc`` models a process that has already exited.
    """

    def __init__(self, rc: int | None = None) -> None:
        self._rc = rc
        self.returncode = rc

    def poll(self) -> int | None:
        return self._rc


class TestStartupTimeoutResolution:
    """``_startup_timeout_seconds`` honours ``CLM_MITM_STARTUP_TIMEOUT`` (issue #184).

    The readiness budget is generous by default because a loaded host can bind
    the port well past the old 10s, but it stays overridable for hosts that
    need even more headroom. A typo must not silently disable the wait.
    """

    def test_default_when_unset(self, monkeypatch) -> None:
        monkeypatch.delenv(proxy_manager._STARTUP_TIMEOUT_ENV, raising=False)
        assert (
            proxy_manager._startup_timeout_seconds()
            == proxy_manager._DEFAULT_STARTUP_TIMEOUT_SECONDS
        )

    def test_valid_override_is_used(self, monkeypatch) -> None:
        monkeypatch.setenv(proxy_manager._STARTUP_TIMEOUT_ENV, "45")
        assert proxy_manager._startup_timeout_seconds() == 45.0

    def test_non_numeric_falls_back_to_default(self, monkeypatch) -> None:
        monkeypatch.setenv(proxy_manager._STARTUP_TIMEOUT_ENV, "soon")
        assert (
            proxy_manager._startup_timeout_seconds()
            == proxy_manager._DEFAULT_STARTUP_TIMEOUT_SECONDS
        )

    def test_non_positive_falls_back_to_default(self, monkeypatch) -> None:
        monkeypatch.setenv(proxy_manager._STARTUP_TIMEOUT_ENV, "0")
        assert (
            proxy_manager._startup_timeout_seconds()
            == proxy_manager._DEFAULT_STARTUP_TIMEOUT_SECONDS
        )


class TestWaitForReadyDiagnostics:
    """The readiness timeout message distinguishes overload from a crash (issue #184)."""

    def test_overloaded_process_still_starting_points_to_env_knob(self, monkeypatch) -> None:
        # Tiny budget + a port that never accepts exercises the timeout path
        # without waiting the real default.
        monkeypatch.setenv(proxy_manager._STARTUP_TIMEOUT_ENV, "0.2")
        monkeypatch.setattr(proxy_manager.socket, "create_connection", _raise_oserror)
        mgr = _manager()
        mgr._process = _ReadyFakeProc(rc=None)  # alive, never binds
        mgr.listen_port = 65000

        with pytest.raises(proxy_manager.MitmproxyError) as excinfo:
            mgr._wait_for_ready()
        msg = str(excinfo.value)
        assert "overloaded" in msg.lower()
        assert proxy_manager._STARTUP_TIMEOUT_ENV in msg
        assert "0.2s" in msg  # reports the resolved budget, not the default

    def test_exited_process_reports_crash_not_overload(self, monkeypatch) -> None:
        monkeypatch.setattr(proxy_manager.socket, "create_connection", _raise_oserror)
        mgr = _manager()
        mgr._process = _ReadyFakeProc(rc=7)  # already exited
        mgr.listen_port = 65000

        with pytest.raises(proxy_manager.MitmproxyError) as excinfo:
            mgr._wait_for_ready()
        msg = str(excinfo.value)
        assert "exited during startup" in msg
        assert "rc=7" in msg
        assert "overloaded" not in msg.lower()
