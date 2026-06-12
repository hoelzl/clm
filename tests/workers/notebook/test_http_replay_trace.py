"""Tests for the HTTP-replay forensic trace harness.

Covers:

* The host-side helpers (``redact_body``, ``TraceWriter``,
  ``make_invocation_dir``, ``write_manifest``, env-var sensing).
* The bootstrap template's trace block (formats, parses, includes the
  expected wrappers).
* The analysis script's ability to digest a synthetic trace bundle.
"""

from __future__ import annotations

import ast
import io
import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from clm.workers.notebook import http_replay_trace as trace_mod


@pytest.fixture(autouse=True)
def _reset_trace_state(monkeypatch):
    monkeypatch.delenv("CLM_HTTP_REPLAY_TRACE", raising=False)
    monkeypatch.delenv("CLM_HTTP_REPLAY_TRACE_DIR", raising=False)
    monkeypatch.delenv("CLM_HTTP_REPLAY_TRACE_VERBOSE", raising=False)
    monkeypatch.delenv("CLM_HTTP_REPLAY_TRACE_MAX_BODY_BYTES", raising=False)
    trace_mod.close_all_writers()
    trace_mod.set_invocation_dir(None)
    yield
    trace_mod.close_all_writers()
    trace_mod.set_invocation_dir(None)


class TestEnvSensing:
    def test_is_enabled_default_off(self):
        assert trace_mod.is_enabled() is False

    def test_is_enabled_truthy_values(self, monkeypatch):
        for v in ("1", "true", "yes", "on", "TRUE", "Yes"):
            monkeypatch.setenv("CLM_HTTP_REPLAY_TRACE", v)
            assert trace_mod.is_enabled() is True, f"expected {v!r} to enable"

    def test_is_enabled_falsy_values(self, monkeypatch):
        for v in ("", "0", "false", "no", "off"):
            monkeypatch.setenv("CLM_HTTP_REPLAY_TRACE", v)
            assert trace_mod.is_enabled() is False, f"expected {v!r} to leave off"

    def test_max_body_bytes_default(self):
        assert trace_mod.max_body_bytes() == 2048

    def test_max_body_bytes_override(self, monkeypatch):
        monkeypatch.setenv("CLM_HTTP_REPLAY_TRACE_MAX_BODY_BYTES", "4096")
        assert trace_mod.max_body_bytes() == 4096

    def test_max_body_bytes_bad_value_falls_back(self, monkeypatch):
        monkeypatch.setenv("CLM_HTTP_REPLAY_TRACE_MAX_BODY_BYTES", "garbage")
        assert trace_mod.max_body_bytes() == 2048

    def test_max_body_bytes_negative_falls_back(self, monkeypatch):
        monkeypatch.setenv("CLM_HTTP_REPLAY_TRACE_MAX_BODY_BYTES", "-1")
        assert trace_mod.max_body_bytes() == 2048


class TestRedactBody:
    def test_short_body_no_tail(self):
        out = trace_mod.redact_body(b"hello")
        assert out["length"] == 5
        assert out["head"] == "b'hello'"
        assert "tail" not in out
        assert out["sha256"]  # non-empty

    def test_none_body(self):
        out = trace_mod.redact_body(None)
        assert out["length"] == 0
        assert out["head"] == "b''"
        assert out["sha256"] == ""

    def test_crlf_preserved_in_repr(self):
        out = trace_mod.redact_body(b"line1\r\nline2")
        # repr() must surface the CR/LF escape sequence so analysis can spot it
        assert "\\r\\n" in out["head"], f"expected \\\\r\\\\n in {out['head']!r}"

    def test_long_body_head_tail_split(self):
        body = b"A" * 100 + b"B" * 100
        out = trace_mod.redact_body(body, max_per_side=20)
        assert out["length"] == 200
        assert "tail" in out
        assert out["truncated"] == 200 - 40
        assert out["head"].count("A") == 20
        assert out["tail"].count("B") == 20

    def test_unicode_body_safe(self):
        out = trace_mod.redact_body("hëllo wörld")
        assert out["length"] > 0
        assert "head" in out

    def test_sha_stable_across_calls(self):
        a = trace_mod.redact_body(b"identical")
        b = trace_mod.redact_body(b"identical")
        assert a["sha256"] == b["sha256"]

    def test_sha_differs_for_different_bodies(self):
        a = trace_mod.redact_body(b"one")
        b = trace_mod.redact_body(b"two")
        assert a["sha256"] != b["sha256"]


class TestInvocationDir:
    def test_make_invocation_dir_under_root(self, tmp_path):
        ts = datetime(2026, 5, 26, 16, 39, 10, tzinfo=timezone.utc)
        out = trace_mod.make_invocation_dir(root=tmp_path, timestamp=ts)
        assert out.parent == tmp_path
        assert out.name.startswith("2026-05-26T16-39-10_")
        assert out.is_dir()

    def test_make_invocation_dir_distinct_per_call(self, tmp_path):
        ts = datetime(2026, 5, 26, 16, 39, 10, tzinfo=timezone.utc)
        a = trace_mod.make_invocation_dir(root=tmp_path, timestamp=ts)
        b = trace_mod.make_invocation_dir(root=tmp_path, timestamp=ts)
        assert a != b  # uuid suffix breaks the tie

    def test_resolve_trace_root_default_is_cwd(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        assert trace_mod.resolve_trace_root() == tmp_path / "clm-http-replay-traces"

    def test_resolve_trace_root_override(self, monkeypatch, tmp_path):
        target = tmp_path / "custom"
        monkeypatch.setenv("CLM_HTTP_REPLAY_TRACE_DIR", str(target))
        assert trace_mod.resolve_trace_root() == target.resolve()


class TestManifest:
    def test_write_manifest_round_trip(self, tmp_path):
        invocation = trace_mod.make_invocation_dir(root=tmp_path)
        out = trace_mod.write_manifest(
            invocation,
            http_replay_mode="replay",
            command_argv=["clm", "build", "spec.xml"],
        )
        assert out.is_file()
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["http_replay_mode"] == "replay"
        assert payload["argv"] == ["clm", "build", "spec.xml"]
        assert payload["schema"] == 1
        assert "started_at" in payload

    def test_write_manifest_includes_extra(self, tmp_path):
        invocation = trace_mod.make_invocation_dir(root=tmp_path)
        out = trace_mod.write_manifest(
            invocation,
            http_replay_mode=None,
            extra={"course": "AZAV-ML"},
        )
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["course"] == "AZAV-ML"


class TestTraceWriter:
    def test_emit_writes_one_jsonl_line(self, tmp_path):
        path = tmp_path / "out.jsonl"
        writer = trace_mod.TraceWriter(path, stream="vcr")
        writer.emit("test.event", {"k": "v"})
        writer.close()
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["stream"] == "vcr"
        assert record["event"] == "test.event"
        assert record["data"] == {"k": "v"}
        assert record["pid"] == os.getpid()
        assert record["tid"] == threading.get_ident()

    def test_emit_thread_safe(self, tmp_path):
        path = tmp_path / "concurrent.jsonl"
        writer = trace_mod.TraceWriter(path, stream="socket")
        n_threads = 8
        per_thread = 50

        def worker(label: int) -> None:
            for i in range(per_thread):
                writer.emit("connect", {"i": i, "label": label})

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        writer.close()

        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == n_threads * per_thread
        for line in lines:
            json.loads(line)  # every line must parse — no torn writes

    def test_get_writer_no_op_when_disabled(self, tmp_path):
        # Default fixture state: no invocation dir, env not set.
        w = trace_mod.get_writer("host")
        w.emit("anything", {"x": 1})  # must not raise
        assert isinstance(w, trace_mod._NullWriter)

    def test_get_writer_writes_when_enabled(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLM_HTTP_REPLAY_TRACE", "1")
        invocation = trace_mod.make_invocation_dir(root=tmp_path)
        trace_mod.set_invocation_dir(invocation)
        w = trace_mod.get_writer("host")
        w.emit("seed", {"a": 1})
        trace_mod.close_all_writers()
        target = invocation / "host.jsonl"
        record = json.loads(target.read_text(encoding="utf-8").splitlines()[0])
        assert record["event"] == "seed"
        assert record["data"]["a"] == 1


class TestAnalysisScript:
    """The analyze_http_replay_trace script must digest a synthetic bundle."""

    def _build_synthetic_bundle(self, tmp_path: Path) -> Path:
        invocation = tmp_path / "trace"
        invocation.mkdir()
        (invocation / "manifest.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "started_at": "2026-05-26T16:39:10+00:00",
                    "host_pid": 99,
                    "http_replay_mode": "replay",
                    "verbose": False,
                    "max_body_bytes": 2048,
                    "argv": ["clm", "build"],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        def _event(stream, event, *, pid, tid, ts_mono, data=None):
            return (
                json.dumps(
                    {
                        "ts_mono": ts_mono,
                        "ts_wall": "2026-05-26T16:39:10+00:00",
                        "pid": pid,
                        "tid": tid,
                        "stream": stream,
                        "event": event,
                        "data": data or {},
                    }
                )
                + "\n"
            )

        # host: one seed, one merge that folds one entry
        (invocation / "host.jsonl").write_text(
            _event(
                "cassette",
                "cassette.seed",
                pid=99,
                tid=1,
                ts_mono=0.0,
                data={"canonical": "/c", "staging": "/s", "seeded_bytes": 0},
            )
            + _event(
                "cassette",
                "cassette.merge.start",
                pid=99,
                tid=1,
                ts_mono=1.0,
                data={"canonical": "/c", "n_staging_files": 1},
            )
            + _event(
                "cassette",
                "cassette.merge.decision",
                pid=99,
                tid=1,
                ts_mono=1.1,
                data={
                    "staging": "/s",
                    "decision": "folded",
                    "interactions_loaded": 1,
                    "interactions_folded": 1,
                    "interactions_deduped": 0,
                },
            )
            + _event(
                "cassette",
                "cassette.merge.end",
                pid=99,
                tid=1,
                ts_mono=1.2,
                data={"canonical": "/c", "folded": 1, "final_interaction_count": 1},
            ),
            encoding="utf-8",
        )

        # worker 1234: classic race — force_reset opens on TID B,
        # remote socket.connect fires on TID A in the window.
        lines = []
        lines.append(_event("vcr", "force_reset.enter", pid=1234, tid=2, ts_mono=10.0))
        lines.append(
            _event(
                "socket",
                "connect",
                pid=1234,
                tid=1,
                ts_mono=10.001,
                data={"host": "api.openai.com", "port": 443},
            )
        )
        lines.append(_event("vcr", "force_reset.exit", pid=1234, tid=2, ts_mono=10.01))
        # And a clean cassette hit on TID 1 a moment later — must NOT count as bypass
        lines.append(
            _event(
                "vcr",
                "cassette.can_play",
                pid=1234,
                tid=1,
                ts_mono=10.1,
                data={"host": "api.openai.com", "result": True},
            )
        )
        lines.append(
            _event(
                "vcr",
                "cassette.play",
                pid=1234,
                tid=1,
                ts_mono=10.11,
                data={"host": "api.openai.com"},
            )
        )
        # A loopback connect (kernel ↔ jupyter) must not be counted as remote
        lines.append(
            _event(
                "socket",
                "connect",
                pid=1234,
                tid=1,
                ts_mono=10.2,
                data={"host": "127.0.0.1", "port": 50000},
            )
        )
        # A pure bypass: remote connect with NO vcr event nearby
        lines.append(
            _event(
                "socket",
                "connect",
                pid=1234,
                tid=1,
                ts_mono=20.0,
                data={"host": "smith.langchain.com", "port": 443},
            )
        )
        (invocation / "worker-1234.jsonl").write_text("".join(lines), encoding="utf-8")
        return invocation

    def test_analyze_classifies_race_and_bypass(self, tmp_path):
        sys.path.insert(0, str(Path("scripts").resolve()))
        try:
            import analyze_http_replay_trace as ana
        finally:
            sys.path.pop(0)

        bundle = self._build_synthetic_bundle(tmp_path)
        result = ana.analyze(bundle)

        assert result.manifest["http_replay_mode"] == "replay"
        assert result.host.seeds == 1
        assert result.host.folded_total == 1

        assert 1234 in result.workers
        ws = result.workers[1234]
        assert len(ws.loopback_connects) == 1
        assert len(ws.remote_connects) == 2
        # The connect at t=10.001 is a race candidate (force_reset open on TID 2)
        assert len(ws.race_candidates) == 1
        assert ws.race_candidates[0]["host"] == "api.openai.com"
        # The connect at t=20.0 is a pure bypass (no vcr event near it)
        assert len(ws.bypassed) == 1
        assert ws.bypassed[0]["host"] == "smith.langchain.com"

    def test_analyze_text_report_smoketest(self, tmp_path):
        sys.path.insert(0, str(Path("scripts").resolve()))
        try:
            import analyze_http_replay_trace as ana
        finally:
            sys.path.pop(0)

        bundle = self._build_synthetic_bundle(tmp_path)
        result = ana.analyze(bundle)
        report = ana.format_text(result)
        assert "Bypassed" in report
        assert "Race candidates" in report
        assert "api.openai.com" in report or "smith.langchain.com" in report

    def test_analyze_cli_runs(self, tmp_path):
        bundle = self._build_synthetic_bundle(tmp_path)
        proc = subprocess.run(
            [sys.executable, "scripts/analyze_http_replay_trace.py", str(bundle), "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert "workers" in payload
        assert "host" in payload


class TestIsLoopback:
    """is_loopback must recognize all loopback forms a dual-stack kernel's
    socket.connect audit event can report verbatim (issue #165 P5), so they are
    not over-reported as escapes."""

    def _ana(self):
        sys.path.insert(0, str(Path("scripts").resolve()))
        try:
            import analyze_http_replay_trace as ana
        finally:
            sys.path.pop(0)
        return ana

    def test_loopback_forms_recognized(self):
        ana = self._ana()
        for h in [
            "127.0.0.1",
            "127.0.0.5",  # whole 127.0.0.0/8
            "::1",
            "0:0:0:0:0:0:0:1",  # expanded IPv6 loopback
            "::ffff:127.0.0.1",  # IPv4-mapped loopback
            "localhost",
        ]:
            assert ana.is_loopback(h) is True, h

    def test_non_loopback_not_recognized(self):
        ana = self._ana()
        for h in ["10.0.0.2", "api.openai.com", "smith.langchain.com", "0.0.0.0", ""]:
            assert ana.is_loopback(h) is False, h


class TestTransportModeAnalysis:
    """Under transport=mitmproxy the analyzer inverts the bypass rule and reads
    the ``proxy`` stream as interception evidence (issue #165 P5).

    The kernel's intended remote target IS the proxy, so a remote connect on the
    proxy port is expected (to-proxy), and a remote connect to any other port is
    a genuine escape. The dark ``vcr`` stream is not consulted.
    """

    PROXY_PORT = 12345

    def _build_bundle(self, tmp_path: Path) -> Path:
        invocation = tmp_path / "trace"
        invocation.mkdir()
        (invocation / "manifest.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "started_at": "2026-05-31T10:00:00+00:00",
                    "host_pid": 99,
                    "http_replay_mode": "new-episodes",
                    "transport": "mitmproxy",
                    "verbose": False,
                    "max_body_bytes": 2048,
                    "argv": ["clm", "build"],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        def _event(stream, event, *, pid, tid, ts_mono, data=None):
            return (
                json.dumps(
                    {
                        "ts_mono": ts_mono,
                        "ts_wall": "2026-05-31T10:00:00+00:00",
                        "pid": pid,
                        "tid": tid,
                        "stream": stream,
                        "event": event,
                        "data": data or {},
                    }
                )
                + "\n"
            )

        # Proxy stream: the addon's per-flow decisions.
        proxy_lines = [
            _event(
                "proxy",
                "proxy.ready",
                pid=5000,
                tid=1,
                ts_mono=0.0,
                data={
                    "listen_host": "0.0.0.0",
                    "listen_port": self.PROXY_PORT,
                    "mode": "new-episodes",
                },
            ),
            _event(
                "proxy",
                "proxy.request",
                pid=5000,
                tid=1,
                ts_mono=1.0,
                data={"method": "POST", "host": "api.openai.com", "port": 443, "action": "served"},
            ),
            _event(
                "proxy",
                "proxy.request",
                pid=5000,
                tid=1,
                ts_mono=1.1,
                data={"method": "POST", "host": "api.openai.com", "port": 443, "action": "forward"},
            ),
            _event(
                "proxy",
                "proxy.response",
                pid=5000,
                tid=1,
                ts_mono=1.2,
                data={"host": "api.openai.com", "port": 443, "status": 200, "recorded": True},
            ),
            _event(
                "proxy",
                "proxy.request",
                pid=5000,
                tid=1,
                ts_mono=1.3,
                data={
                    "method": "POST",
                    "host": "smith.langchain.com",
                    "port": 443,
                    "action": "ignored",
                },
            ),
        ]
        (invocation / "proxy-5000.jsonl").write_text("".join(proxy_lines), encoding="utf-8")

        # Worker stream: a loopback Jupyter connect (NOT the proxy port), a
        # Direct-style loopback connect TO the proxy port, a Docker-style
        # gateway-IP connect to the proxy port, and a genuine escape on :443.
        worker_lines = [
            _event(
                "socket",
                "connect",
                pid=4242,
                tid=1,
                ts_mono=0.4,
                data={"host": "127.0.0.1", "port": 50000},
            ),
            _event(
                "socket",
                "connect",
                pid=4242,
                tid=1,
                ts_mono=0.5,
                data={"host": "127.0.0.1", "port": self.PROXY_PORT},
            ),
            _event(
                "socket",
                "connect",
                pid=4242,
                tid=1,
                ts_mono=0.6,
                data={"host": "10.0.0.2", "port": self.PROXY_PORT},
            ),
            _event(
                "socket",
                "connect",
                pid=4242,
                tid=1,
                ts_mono=0.7,
                data={"host": "api.openai.com", "port": 443},
            ),
        ]
        (invocation / "worker-4242.jsonl").write_text("".join(worker_lines), encoding="utf-8")
        return invocation

    def _ana(self):
        sys.path.insert(0, str(Path("scripts").resolve()))
        try:
            import analyze_http_replay_trace as ana
        finally:
            sys.path.pop(0)
        return ana

    def test_proxy_stream_and_bypass_inversion(self, tmp_path):
        ana = self._ana()
        result = ana.analyze(self._build_bundle(tmp_path))

        assert result.transport == "mitmproxy"
        assert result.proxy.proxy_ports == {self.PROXY_PORT}
        assert result.proxy.flows_total == 3
        assert result.proxy.served == 1
        assert result.proxy.forward == 1
        assert result.proxy.ignored == 1
        assert result.proxy.recorded == 1

        ws = result.workers[4242]
        assert len(ws.loopback_connects) == 2  # jupyter + the Direct loopback-to-proxy
        assert len(ws.remote_connects) == 2  # the gateway-IP one + the escape
        # Both proxy-port connects (loopback Direct + remote Docker gateway) are
        # to-proxy, NOT bypasses.
        assert len(ws.to_proxy_connects) == 2
        assert {c.data["host"] for c in ws.to_proxy_connects} == {"127.0.0.1", "10.0.0.2"}
        # Only the :443 connect that is NOT on the proxy port is the genuine escape.
        assert len(ws.bypassed) == 1
        assert ws.bypassed[0]["host"] == "api.openai.com"
        # No vcr stream → no race candidates under the transport.
        assert ws.race_candidates == []

    def test_text_report_transport_sections(self, tmp_path):
        ana = self._ana()
        result = ana.analyze(self._build_bundle(tmp_path))
        report = ana.format_text(result)
        assert "Transport:       mitmproxy" in report
        assert "Proxy (interception evidence):" in report
        assert "To proxy (expected): 2" in report
        assert "Bypassed (escaped the proxy): 1" in report
        assert "BYPASS — escaped the proxy" in report

    def test_json_report_has_proxy_block(self, tmp_path):
        ana = self._ana()
        result = ana.analyze(self._build_bundle(tmp_path))
        payload = json.loads(ana.format_json(result))
        assert payload["transport"] == "mitmproxy"
        assert payload["proxy"]["proxy_ports"] == [self.PROXY_PORT]
        assert payload["proxy"]["flows_total"] == 3
        assert payload["workers"]["4242"]["to_proxy_connects"] == 2
        assert payload["workers"]["4242"]["remote_connects"] == 2
