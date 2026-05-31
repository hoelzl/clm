"""Unit tests for the addon's proxy-flow trace writer (issue #165 P5).

``ProxyTraceLog`` is pure stdlib so the addon can load it by bare path inside
the isolated mitmdump interpreter. These tests pin its on-disk schema (which
must match the host-side ``TraceWriter``), its disabled-by-default behavior,
and its crash-proofness — a forensic logger must never take down the proxy.
"""

from __future__ import annotations

import json
import os

from clm.infrastructure.http_replay_mitm.trace_log import PROXY_STREAM, ProxyTraceLog


def _read_lines(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


class TestDisabled:
    def test_none_path_is_no_op(self, tmp_path) -> None:
        log = ProxyTraceLog(None)
        assert log.enabled is False
        # Emitting must not create any file or raise.
        log.emit("proxy.ready", {"listen_port": 1})
        log.close()
        assert list(tmp_path.iterdir()) == []

    def test_from_trace_dir_empty_is_disabled(self) -> None:
        assert ProxyTraceLog.from_trace_dir("").enabled is False
        assert ProxyTraceLog.from_trace_dir(None).enabled is False

    def test_from_trace_dir_missing_dir_is_disabled(self, tmp_path) -> None:
        # The host creates the invocation dir before starting the proxy; a
        # missing dir disables rather than crashing the proxy.
        missing = tmp_path / "does-not-exist"
        assert ProxyTraceLog.from_trace_dir(missing).enabled is False

    def test_construction_open_failure_degrades_to_disabled(self, tmp_path) -> None:
        # Construction must be as crash-proof as emit(): if open() fails AFTER
        # from_trace_dir's is_dir() check passes (here a directory sits at the
        # exact log-file path → open("a") raises), the log must degrade to a
        # disabled no-op, never propagate — else it crashes proxy startup
        # (mitmproxy turns an exception in running() into sys.exit(1)).
        blocker = tmp_path / f"{PROXY_STREAM}-{os.getpid()}.jsonl"
        blocker.mkdir()  # a directory exactly where the log file would be opened
        log = ProxyTraceLog.from_trace_dir(tmp_path)  # must NOT raise
        assert log.enabled is False
        log.emit("proxy.ready", {"x": 1})  # still a safe no-op
        log.close()


class TestEnabled:
    def test_from_trace_dir_writes_pid_suffixed_file(self, tmp_path) -> None:
        log = ProxyTraceLog.from_trace_dir(tmp_path)
        assert log.enabled is True
        assert log.path is not None
        assert log.path.name == f"{PROXY_STREAM}-{os.getpid()}.jsonl"
        log.close()

    def test_emit_writes_host_compatible_schema(self, tmp_path) -> None:
        log = ProxyTraceLog.from_trace_dir(tmp_path)
        log.emit("proxy.ready", {"listen_port": 8080, "mode": "replay"})
        log.emit(
            "proxy.request",
            {"method": "POST", "host": "api.openai.com", "port": 443, "action": "served"},
        )
        log.close()

        records = _read_lines(log.path)
        assert len(records) == 2
        for rec in records:
            # Same field set as clm.workers.notebook.http_replay_trace.TraceWriter.
            assert set(rec) == {"ts_mono", "ts_wall", "pid", "tid", "stream", "event", "data"}
            assert rec["stream"] == PROXY_STREAM
            assert rec["pid"] == os.getpid()
            assert isinstance(rec["ts_mono"], (int, float))
        assert records[0]["event"] == "proxy.ready"
        assert records[0]["data"]["listen_port"] == 8080
        assert records[1]["data"]["action"] == "served"

    def test_emit_appends_across_instances(self, tmp_path) -> None:
        # Two logs for the same pid append (the addon makes one per process,
        # but appending is the safe semantics if reopened).
        ProxyTraceLog.from_trace_dir(tmp_path).emit("proxy.request", {"action": "miss"})
        log2 = ProxyTraceLog.from_trace_dir(tmp_path)
        log2.emit("proxy.request", {"action": "forward"})
        log2.close()
        records = _read_lines(log2.path)
        assert [r["data"]["action"] for r in records] == ["miss", "forward"]

    def test_emit_is_crash_proof(self, tmp_path) -> None:
        # A non-JSON-serializable payload must be swallowed, not raised.
        log = ProxyTraceLog.from_trace_dir(tmp_path)
        log.emit("proxy.request", {"bad": object()})  # must not raise
        log.emit("proxy.request", {"action": "served"})  # later good events still write
        log.close()
        records = _read_lines(log.path)
        assert len(records) == 1
        assert records[0]["data"]["action"] == "served"
