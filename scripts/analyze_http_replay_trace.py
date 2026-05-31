"""Forensic analysis for HTTP-replay trace bundles.

Reads ``manifest.json`` plus all ``*.jsonl`` files under a trace directory
produced by ``CLM_HTTP_REPLAY_TRACE=1 clm build`` and cross-references the
three event streams (socket / vcr / cassette).

The headline numbers:

* **Bypassed** — outbound connects to a non-loopback host with no
  near-by vcr event on the same worker. These are the bugs we are hunting.
* **Race candidate** — a bypassed connect with ``vcr.force_reset.enter``
  open on a *different* thread of the same worker at the moment of
  ``socket.connect``. Classic issue-129 fingerprint.
* **Cassette health** — hits (``cassette.play``), appends, and
  cannot-play decisions, plus cassette-lifecycle outcomes (folded vs
  discarded vs concurrent-skip).

Run:

    uv run python scripts/analyze_http_replay_trace.py <trace_dir>
    uv run python scripts/analyze_http_replay_trace.py <trace_dir> --json
    uv run python scripts/analyze_http_replay_trace.py <trace_dir> --no-bypass-details
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Window for "did vcr see this socket connect?" — anything within this
# many seconds before the connect counts as related. Generous enough to
# tolerate scheduling jitter, tight enough to avoid false positives.
_MATCH_WINDOW_SECONDS = 0.5


@dataclass
class Event:
    ts_mono: float
    ts_wall: str
    pid: int
    tid: int
    stream: str
    event: str
    data: dict[str, Any]
    source_file: str

    @classmethod
    def from_line(cls, line: str, source_file: str) -> Event | None:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return None
        try:
            return cls(
                ts_mono=float(obj["ts_mono"]),
                ts_wall=str(obj.get("ts_wall", "")),
                pid=int(obj["pid"]),
                tid=int(obj["tid"]),
                stream=str(obj["stream"]),
                event=str(obj["event"]),
                data=dict(obj.get("data", {})),
                source_file=source_file,
            )
        except (KeyError, TypeError, ValueError):
            return None


@dataclass
class ForceResetWindow:
    tid: int
    enter_ts_mono: float
    exit_ts_mono: float | None


@dataclass
class WorkerSummary:
    pid: int
    events: list[Event] = field(default_factory=list)
    socket_connects: list[Event] = field(default_factory=list)
    remote_connects: list[Event] = field(default_factory=list)
    loopback_connects: list[Event] = field(default_factory=list)
    # mitmproxy transport only: remote connects whose port is the proxy's —
    # the expected path (the kernel reaches the out-of-process proxy), NOT a
    # bypass. Empty under the in-kernel vcrpy path (the proxy is loopback there
    # or there is no proxy at all).
    to_proxy_connects: list[Event] = field(default_factory=list)
    cassette_hits: int = 0
    cassette_appends: int = 0
    cassette_append_errors: int = 0
    can_play_true: int = 0
    can_play_false: int = 0
    force_reset_windows_per_tid: dict[int, list[ForceResetWindow]] = field(default_factory=dict)
    bypassed: list[dict[str, Any]] = field(default_factory=list)
    race_candidates: list[dict[str, Any]] = field(default_factory=list)
    bootstrap_complete: dict[str, Any] | None = None
    vcr_events_by_tid: dict[int, list[Event]] = field(default_factory=dict)


@dataclass
class HostSummary:
    events: list[Event] = field(default_factory=list)
    seeds: int = 0
    merge_starts: int = 0
    merges_with_folds: int = 0
    folded_total: int = 0
    deduped_total: int = 0
    discarded_orphans: int = 0
    skipped_concurrent: int = 0
    lock_timeouts: int = 0
    completion_markers: int = 0


@dataclass
class ProxySummary:
    """mitmproxy-transport interception evidence (the ``proxy`` stream).

    Replaces the in-kernel ``vcr`` stream under the out-of-process transport:
    the kernel no longer imports vcr, so the proxy's own per-flow decisions are
    what tell us each request was intercepted rather than escaping upstream.
    """

    events: list[Event] = field(default_factory=list)
    proxy_ports: set[int] = field(default_factory=set)
    proxy_ready: list[dict[str, Any]] = field(default_factory=list)
    flows_total: int = 0
    served: int = 0
    miss: int = 0
    ignored: int = 0
    forward: int = 0
    passthrough: int = 0
    recorded: int = 0


@dataclass
class AnalysisResult:
    manifest: dict[str, Any]
    workers: dict[int, WorkerSummary]
    host: HostSummary
    trace_dir: Path
    transport: str = "vcrpy"
    proxy: ProxySummary = field(default_factory=ProxySummary)


def is_loopback(host: str) -> bool:
    """True if ``host`` is a loopback peer (kernel↔Jupyter, or the Direct proxy).

    Parsed-address matching rather than string prefixes, so it correctly covers
    the whole ``127.0.0.0/8`` block, ``::1`` *and* its expanded
    ``0:0:0:0:0:0:0:1`` form, and the IPv4-mapped ``::ffff:127.0.0.1`` that a
    dual-stack kernel's ``socket.connect`` audit event may report verbatim — a
    prefix list missed those and over-reported them as escapes (issue #165 P5).
    """
    if not isinstance(host, str) or not host:
        return False
    if host == "localhost":
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        # A real upstream hostname (e.g. api.openai.com) — not loopback.
        return False
    if addr.is_loopback:
        return True
    mapped = getattr(addr, "ipv4_mapped", None)
    return mapped is not None and mapped.is_loopback


def load_events(jsonl_path: Path) -> Iterable[Event]:
    if not jsonl_path.is_file():
        return
    with jsonl_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            event = Event.from_line(line, jsonl_path.name)
            if event is not None:
                yield event


def analyze(trace_dir: Path) -> AnalysisResult:
    manifest_path = trace_dir / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    )

    transport = str(manifest.get("transport", "vcrpy"))
    host = HostSummary()
    proxy = ProxySummary()
    workers: dict[int, WorkerSummary] = {}

    # Proxy stream (mitmproxy transport, issue #165 P5): per-flow interception
    # decisions written by the addon. This is the evidence the (now-dark) vcr
    # stream used to provide.
    proxy_paths = [trace_dir / "proxy.jsonl", *sorted(trace_dir.glob("proxy-*.jsonl"))]
    for proxy_path in proxy_paths:
        for event in load_events(proxy_path):
            if event.stream != "proxy":
                continue
            proxy.events.append(event)
            if event.event == "proxy.ready":
                proxy.proxy_ready.append(event.data)
                port = event.data.get("listen_port")
                if isinstance(port, int):
                    proxy.proxy_ports.add(port)
            elif event.event == "proxy.request":
                proxy.flows_total += 1
                action = str(event.data.get("action", ""))
                if action == "served":
                    proxy.served += 1
                elif action == "miss":
                    proxy.miss += 1
                elif action == "ignored":
                    proxy.ignored += 1
                elif action == "forward":
                    proxy.forward += 1
                elif action == "passthrough":
                    proxy.passthrough += 1
            elif event.event == "proxy.response" and event.data.get("recorded"):
                proxy.recorded += 1

    host_paths = [trace_dir / "host.jsonl", *sorted(trace_dir.glob("host-*.jsonl"))]
    for host_path in host_paths:
        for event in load_events(host_path):
            host.events.append(event)
            if not event.event.startswith("cassette."):
                continue
            if event.event == "cassette.seed":
                host.seeds += 1
            elif event.event == "cassette.merge.start":
                host.merge_starts += 1
            elif event.event == "cassette.merge.end":
                folded = int(event.data.get("folded", 0) or 0)
                if folded:
                    host.merges_with_folds += 1
                    host.folded_total += folded
            elif event.event == "cassette.merge.decision":
                decision = event.data.get("decision", "")
                if decision == "folded":
                    host.deduped_total += int(event.data.get("interactions_deduped", 0) or 0)
                elif decision == "discarded_orphan":
                    host.discarded_orphans += 1
                elif decision == "skipped_concurrent":
                    host.skipped_concurrent += 1
            elif event.event == "cassette.merge.lock_timeout":
                host.lock_timeouts += 1
            elif event.event == "cassette.completion_marker.write":
                host.completion_markers += 1

    for worker_path in sorted(trace_dir.glob("worker-*.jsonl")):
        for event in load_events(worker_path):
            ws = workers.setdefault(event.pid, WorkerSummary(pid=event.pid))
            ws.events.append(event)

            if event.stream == "socket" and event.event == "connect":
                ws.socket_connects.append(event)
                host_name = str(event.data.get("host", ""))
                if is_loopback(host_name):
                    ws.loopback_connects.append(event)
                else:
                    ws.remote_connects.append(event)
            elif event.stream == "vcr":
                ws.vcr_events_by_tid.setdefault(event.tid, []).append(event)
                if event.event == "force_reset.enter":
                    ws.force_reset_windows_per_tid.setdefault(event.tid, []).append(
                        ForceResetWindow(
                            tid=event.tid,
                            enter_ts_mono=event.ts_mono,
                            exit_ts_mono=None,
                        )
                    )
                elif event.event == "force_reset.exit":
                    windows = ws.force_reset_windows_per_tid.get(event.tid, [])
                    for w in reversed(windows):
                        if w.exit_ts_mono is None:
                            w.exit_ts_mono = event.ts_mono
                            break
                elif event.event == "cassette.play":
                    ws.cassette_hits += 1
                elif event.event == "cassette.append":
                    ws.cassette_appends += 1
                elif event.event == "cassette.append.error":
                    ws.cassette_append_errors += 1
                elif event.event == "cassette.can_play":
                    if event.data.get("result"):
                        ws.can_play_true += 1
                    else:
                        ws.can_play_false += 1
                elif event.event == "bootstrap.complete":
                    ws.bootstrap_complete = dict(event.data)

    for ws in workers.values():
        if transport == "mitmproxy":
            _classify_bypasses_transport(ws, proxy.proxy_ports)
        else:
            _classify_bypasses(ws)

    return AnalysisResult(
        manifest=manifest,
        workers=workers,
        host=host,
        trace_dir=trace_dir,
        transport=transport,
        proxy=proxy,
    )


def _classify_bypasses(ws: WorkerSummary) -> None:
    """Decide which remote connects had no near-by vcr event."""
    for sc in ws.remote_connects:
        same_tid_events = ws.vcr_events_by_tid.get(sc.tid, [])
        nearby = [
            ev
            for ev in same_tid_events
            if sc.ts_mono - _MATCH_WINDOW_SECONDS <= ev.ts_mono <= sc.ts_mono + 0.05
        ]
        if nearby:
            continue
        open_windows: list[ForceResetWindow] = []
        for tid, windows in ws.force_reset_windows_per_tid.items():
            if tid == sc.tid:
                continue
            for w in windows:
                exit_ts = w.exit_ts_mono if w.exit_ts_mono is not None else sc.ts_mono + 1.0
                if w.enter_ts_mono <= sc.ts_mono <= exit_ts:
                    open_windows.append(w)
        record = {
            "ts_mono": sc.ts_mono,
            "ts_wall": sc.ts_wall,
            "tid": sc.tid,
            "host": sc.data.get("host"),
            "port": sc.data.get("port"),
            "open_force_reset_other_tids": [
                {"tid": w.tid, "enter_ts_mono": w.enter_ts_mono, "exit_ts_mono": w.exit_ts_mono}
                for w in open_windows
            ],
        }
        if open_windows:
            ws.race_candidates.append(record)
        else:
            ws.bypassed.append(record)


def _classify_bypasses_transport(ws: WorkerSummary, proxy_ports: set[int]) -> None:
    """Bypass model for the out-of-process mitmproxy transport (issue #165 P5).

    Under the transport the kernel's *intended* target IS the proxy, so the
    vcr-stream cross-reference is meaningless (there is no vcr stream) and the
    bypass rule inverts. We classify every kernel connect by port:

    * port == the proxy's → a connect *to the proxy* (the expected path). This
      is loopback for a Direct build (proxy on 127.0.0.1) and the bridge-gateway
      IP for a Docker build — the port match recognizes both.
    * other loopback (e.g. kernel↔Jupyter) → ignored, neither to-proxy nor escape.
    * any other remote host → a genuine **escape**: the kernel reached a real
      upstream without going through the proxy.

    ``force_reset`` races cannot occur (nothing patches the kernel), so there
    are no race candidates.
    """
    for sc in ws.socket_connects:
        port = sc.data.get("port")
        host = str(sc.data.get("host", ""))
        if isinstance(port, int) and port in proxy_ports:
            ws.to_proxy_connects.append(sc)
            continue
        if is_loopback(host):
            continue  # kernel↔Jupyter (or other) loopback — not the proxy, not an escape
        ws.bypassed.append(
            {
                "ts_mono": sc.ts_mono,
                "ts_wall": sc.ts_wall,
                "tid": sc.tid,
                "host": sc.data.get("host"),
                "port": port,
                "open_force_reset_other_tids": [],
            }
        )


def format_text(result: AnalysisResult, *, show_bypass_details: bool = True) -> str:
    out: list[str] = []
    out.append(f"Trace directory: {result.trace_dir}")
    out.append(f"Transport:       {result.transport}")
    if result.manifest:
        out.append(f"Started at:      {result.manifest.get('started_at', '?')}")
        out.append(f"Replay mode:     {result.manifest.get('http_replay_mode', '?')}")
        out.append(f"Host PID:        {result.manifest.get('host_pid', '?')}")
        out.append(f"Verbose:         {result.manifest.get('verbose', False)}")
        out.append(f"Max body bytes:  {result.manifest.get('max_body_bytes', '?')}")
    out.append("")

    out.append(f"Workers seen:    {len(result.workers)}")
    total_connects = sum(len(w.socket_connects) for w in result.workers.values())
    total_loopback = sum(len(w.loopback_connects) for w in result.workers.values())
    total_remote = sum(len(w.remote_connects) for w in result.workers.values())
    total_bypassed = sum(len(w.bypassed) for w in result.workers.values())
    total_race = sum(len(w.race_candidates) for w in result.workers.values())
    total_to_proxy = sum(len(w.to_proxy_connects) for w in result.workers.values())

    if result.transport == "mitmproxy":
        out.append(
            f"Socket connects: {total_connects} ({total_loopback} loopback, {total_remote} remote)"
        )
        out.append(f"  To proxy (expected): {total_to_proxy}")
        out.append(f"  Bypassed (escaped the proxy): {total_bypassed}")
        out.append("")

        out.append("Proxy (interception evidence):")
        out.append(f"  Listen port(s):            {sorted(result.proxy.proxy_ports) or '?'}")
        out.append(f"  Flows total:               {result.proxy.flows_total}")
        out.append(f"  Served (cassette hit):     {result.proxy.served}")
        out.append(f"  Strict-miss (404):         {result.proxy.miss}")
        out.append(f"  Forwarded upstream:        {result.proxy.forward}")
        out.append(f"  Ignored (not recorded):    {result.proxy.ignored}")
        out.append(f"  Passthrough (untagged):    {result.proxy.passthrough}")
        out.append(f"  Responses recorded:        {result.proxy.recorded}")
        # Connection-bound health: the issue #143 leak signature was ~1 leaked
        # pooled connection per request (connects ≈ flows). Healthy pooling to
        # the out-of-process proxy keeps connects ≪ flows. Only meaningful with
        # a reasonable sample — at a handful of flows the per-connect noise
        # (TLS tunnel setup, DNS) swamps the ratio, so we gate on flows.
        flows = result.proxy.flows_total
        if flows >= 10 and total_to_proxy:
            ratio = total_to_proxy / flows
            verdict = "healthy (pooled)" if ratio <= 0.5 else "INVESTIGATE (≈1:1 → leak?)"
            out.append(
                f"  Conn-bound: {total_to_proxy} proxy connects / {flows} flows "
                f"= {ratio:.2f} → {verdict}"
            )
        elif flows:
            out.append(
                f"  Conn-bound: {total_to_proxy} proxy connects / {flows} flows "
                f"(too few flows for a ratio verdict)"
            )
        out.append("")
    else:
        out.append(
            f"Socket connects: {total_connects} ({total_loopback} loopback, {total_remote} remote)"
        )
        matched = total_remote - total_bypassed - total_race
        out.append(f"  Matched a vcr event: {matched}")
        out.append(f"  Bypassed (no vcr event near connect): {total_bypassed}")
        out.append(f"  Race candidates (bypass + force_reset open on other TID): {total_race}")
        out.append("")

        total_hits = sum(w.cassette_hits for w in result.workers.values())
        total_appends = sum(w.cassette_appends for w in result.workers.values())
        total_append_errs = sum(w.cassette_append_errors for w in result.workers.values())
        total_can_play_t = sum(w.can_play_true for w in result.workers.values())
        total_can_play_f = sum(w.can_play_false for w in result.workers.values())
        out.append("Cassette (worker-side):")
        out.append(f"  Hits (play_response):      {total_hits}")
        out.append(f"  Appends:                   {total_appends}")
        out.append(f"  Append errors:             {total_append_errs}")
        out.append(f"  can_play True / False:     {total_can_play_t} / {total_can_play_f}")
        out.append("")

    out.append("Cassette (host-side lifecycle):")
    out.append(f"  Seeds:                     {result.host.seeds}")
    out.append(f"  Merge starts:              {result.host.merge_starts}")
    out.append(f"  Merges that folded:        {result.host.merges_with_folds}")
    out.append(f"  Interactions folded:       {result.host.folded_total}")
    out.append(f"  Interactions deduped:      {result.host.deduped_total}")
    out.append(f"  Orphans discarded:         {result.host.discarded_orphans}")
    out.append(f"  Skipped concurrent:        {result.host.skipped_concurrent}")
    out.append(f"  Lock timeouts:             {result.host.lock_timeouts}")
    out.append(f"  Completion markers:        {result.host.completion_markers}")
    out.append("")

    if show_bypass_details and (total_bypassed > 0 or total_race > 0):
        out.append("Bypass forensics:")
        for pid, ws in sorted(result.workers.items()):
            for rec in ws.race_candidates:
                others = ", ".join(
                    f"TID {w['tid']} enter={w['enter_ts_mono']:.6f} exit={w['exit_ts_mono']}"
                    for w in rec["open_force_reset_other_tids"]
                )
                out.append(
                    f"  [worker pid={pid} tid={rec['tid']} t={rec['ts_mono']:.6f}] "
                    f"connect to {rec['host']}:{rec['port']}  "
                    f"RACE — force_reset open on: {others}"
                )
            bypass_reason = (
                "escaped the proxy (port is not the proxy's)"
                if result.transport == "mitmproxy"
                else "no near-by vcr event"
            )
            for rec in ws.bypassed:
                out.append(
                    f"  [worker pid={pid} tid={rec['tid']} t={rec['ts_mono']:.6f}] "
                    f"connect to {rec['host']}:{rec['port']}  "
                    f"BYPASS — {bypass_reason}"
                )
        out.append("")

    return "\n".join(out)


def format_json(result: AnalysisResult) -> str:
    payload = {
        "trace_dir": str(result.trace_dir),
        "transport": result.transport,
        "manifest": result.manifest,
        "proxy": {
            "proxy_ports": sorted(result.proxy.proxy_ports),
            "flows_total": result.proxy.flows_total,
            "served": result.proxy.served,
            "miss": result.proxy.miss,
            "ignored": result.proxy.ignored,
            "forward": result.proxy.forward,
            "passthrough": result.proxy.passthrough,
            "recorded": result.proxy.recorded,
        },
        "host": {
            "seeds": result.host.seeds,
            "merge_starts": result.host.merge_starts,
            "merges_with_folds": result.host.merges_with_folds,
            "folded_total": result.host.folded_total,
            "deduped_total": result.host.deduped_total,
            "discarded_orphans": result.host.discarded_orphans,
            "skipped_concurrent": result.host.skipped_concurrent,
            "lock_timeouts": result.host.lock_timeouts,
            "completion_markers": result.host.completion_markers,
        },
        "workers": {
            str(pid): {
                "events_total": len(ws.events),
                "socket_connects": len(ws.socket_connects),
                "loopback_connects": len(ws.loopback_connects),
                "remote_connects": len(ws.remote_connects),
                "to_proxy_connects": len(ws.to_proxy_connects),
                "cassette_hits": ws.cassette_hits,
                "cassette_appends": ws.cassette_appends,
                "cassette_append_errors": ws.cassette_append_errors,
                "can_play_true": ws.can_play_true,
                "can_play_false": ws.can_play_false,
                "bypassed": ws.bypassed,
                "race_candidates": ws.race_candidates,
                "bootstrap_complete": ws.bootstrap_complete,
            }
            for pid, ws in sorted(result.workers.items())
        },
    }
    return json.dumps(payload, indent=2, sort_keys=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace_dir", type=Path, help="Per-invocation trace directory")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of the text report",
    )
    parser.add_argument(
        "--no-bypass-details",
        action="store_true",
        help="Suppress the per-event bypass forensics list (text format only)",
    )
    args = parser.parse_args(argv)

    trace_dir: Path = args.trace_dir
    if not trace_dir.is_dir():
        print(f"error: {trace_dir} is not a directory", file=sys.stderr)
        return 2

    result = analyze(trace_dir)
    if args.json:
        print(format_json(result))
    else:
        print(format_text(result, show_bypass_details=not args.no_bypass_details))
    return 0


if __name__ == "__main__":
    sys.exit(main())
