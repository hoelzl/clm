"""Minimal JSONL trace writer for the mitmproxy addon (issue #165 P5).

The forensic HTTP-replay trace harness
(``docs/claude/design/http-replay-trace.md``) cross-references three event
streams: ``socket`` (worker-kernel ground truth via an audit hook),
``cassette`` (host-side lifecycle) and — under the in-process vcrpy path —
``vcr`` (wrapped vcrpy internals).

Under the **out-of-process mitmproxy transport** the ``vcr`` stream goes dark:
the kernel no longer imports vcr or patches httpcore, so there are no vcr
internals to wrap. The proxy's own per-flow decisions become the
interception-evidence stream instead. This module is that stream's writer
(``stream="proxy"``).

It is imported by :mod:`clm.infrastructure.http_replay_mitm.addon`, which runs
inside the isolated ``mitmdump`` interpreter (``uv tool install mitmproxy``)
where the ``clm`` package is absent. Like
:mod:`clm.infrastructure.http_replay_mitm.cassette_format` it therefore imports
**only the stdlib** so the addon can load it by bare path, and it emits the
*same* JSONL record shape as
:class:`clm.workers.notebook.http_replay_trace.TraceWriter` so
``scripts/analyze_http_replay_trace.py`` reads every stream uniformly.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Stream name for proxy-side flow events — the interception-evidence stream
# under the mitmproxy transport (replaces the kernel ``vcr`` stream). The
# analyzer keys on this exact string.
PROXY_STREAM = "proxy"


class ProxyTraceLog:
    """Thread-safe JSONL appender for the addon's per-flow proxy events.

    One instance per ``mitmdump`` process writes ``proxy-<pid>.jsonl`` into the
    build's trace invocation directory (the pid suffix matches the
    ``worker-<pid>.jsonl`` convention so the analyzer can glob ``proxy-*``).
    A disabled instance (no trace directory configured) is a cheap no-op so the
    addon can call :meth:`emit` unconditionally on every flow.

    The writer is crash-proof on purpose: forensic tracing must never take down
    the proxy, so :meth:`emit` swallows any write error rather than propagating
    it into mitmproxy's flow handling.
    """

    __slots__ = ("_fh", "_lock", "_path", "_start_mono")

    def __init__(self, path: Path | None) -> None:
        self._lock = threading.Lock()
        self._start_mono = time.monotonic()
        self._path = path
        self._fh = None
        if path is not None:
            # Construction must be as crash-proof as emit(): a file-open failure
            # (Windows AV holding a stale recycled-PID file, a TOCTOU dir removal
            # after from_trace_dir's is_dir() check, a dir already at the path)
            # must degrade to a disabled no-op log, NOT propagate out of the
            # addon's running() — where mitmproxy turns any exception into a hard
            # sys.exit(1) startup abort, i.e. it would crash the very proxy the
            # forensic trace is meant to observe.
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                self._fh = path.open("a", encoding="utf-8", newline="\n")
            except OSError:
                self._fh = None

    @classmethod
    def from_trace_dir(cls, trace_dir: str | os.PathLike[str] | None) -> ProxyTraceLog:
        """Build a log writing ``proxy-<pid>.jsonl`` under ``trace_dir``.

        An empty/``None`` ``trace_dir`` — or one the host has not created yet —
        yields a disabled (no-op) log rather than crashing the proxy.
        """
        if not trace_dir:
            return cls(None)
        directory = Path(trace_dir)
        if not directory.is_dir():
            return cls(None)
        return cls(directory / f"{PROXY_STREAM}-{os.getpid()}.jsonl")

    @property
    def enabled(self) -> bool:
        return self._fh is not None

    @property
    def path(self) -> Path | None:
        return self._path

    def emit(self, event: str, data: dict[str, Any] | None = None) -> None:
        fh = self._fh
        if fh is None:
            return
        try:
            record = {
                "ts_mono": time.monotonic() - self._start_mono,
                "ts_wall": datetime.now(timezone.utc).isoformat(),
                "pid": os.getpid(),
                "tid": threading.get_ident(),
                "stream": PROXY_STREAM,
                "event": event,
                "data": data or {},
            }
            line = json.dumps(record, sort_keys=False) + "\n"
            with self._lock:
                fh.write(line)
                fh.flush()
        except Exception:
            # Forensic tracing must never crash the proxy.
            return

    def close(self) -> None:
        with self._lock:
            if self._fh is not None and not self._fh.closed:
                try:
                    self._fh.close()
                except Exception:
                    pass
