"""Forensic trace harness for HTTP-replay debugging.

The harness emits independent telemetry streams to per-process JSONL
files so the analysis script can cross-reference them:

* ``socket`` events from a ``sys.addaudithook`` in the worker kernel
  (ground truth — installed by the tag bootstrap's socket-trace block,
  every connect should target the replay proxy).
* ``proxy`` events written by the mitmproxy addon (per-flow interception
  decisions: served / miss / forward / ignored / passthrough).
* ``cassette`` events from host-side lifecycle calls (merge decisions,
  dedup outcomes, completion-marker writes, orphan sweeps).

This module owns the host side. (Older trace bundles also contain a
``vcr`` stream from the removed in-kernel vcrpy transport; the analyzer
still reads those.)

Activate with ``CLM_HTTP_REPLAY_TRACE=1``; trace files land under
``$CLM_HTTP_REPLAY_TRACE_DIR`` (default ``./clm-http-replay-traces``) in a
per-invocation subdirectory containing ``manifest.json``, ``host.jsonl``,
and ``worker-<pid>.jsonl`` per worker. Design doc:
``docs/claude/design/http-replay-trace.md``.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ENABLED_ENV = "CLM_HTTP_REPLAY_TRACE"
_DIR_ENV = "CLM_HTTP_REPLAY_TRACE_DIR"
_INVOCATION_ENV = "CLM_HTTP_REPLAY_TRACE_INVOCATION_DIR"
_VERBOSE_ENV = "CLM_HTTP_REPLAY_TRACE_VERBOSE"
_MAX_BODY_BYTES_ENV = "CLM_HTTP_REPLAY_TRACE_MAX_BODY_BYTES"

_DEFAULT_DIR_NAME = "clm-http-replay-traces"
_DEFAULT_MAX_BODY_BYTES = 2048

_MANIFEST_SCHEMA = 1


def is_enabled() -> bool:
    return _truthy(os.environ.get(_ENABLED_ENV, ""))


def is_verbose() -> bool:
    return _truthy(os.environ.get(_VERBOSE_ENV, ""))


def max_body_bytes() -> int:
    raw = os.environ.get(_MAX_BODY_BYTES_ENV, "").strip()
    if not raw:
        return _DEFAULT_MAX_BODY_BYTES
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_MAX_BODY_BYTES
    return value if value >= 0 else _DEFAULT_MAX_BODY_BYTES


def _truthy(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def resolve_trace_root() -> Path:
    override = os.environ.get(_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path.cwd() / _DEFAULT_DIR_NAME


def make_invocation_dir(
    *,
    root: Path | None = None,
    timestamp: datetime | None = None,
) -> Path:
    """Create a fresh per-invocation trace directory under ``root``.

    The directory name encodes both a UTC timestamp (sortable) and a
    short random suffix so two builds started in the same second still
    land in distinct directories.
    """
    root = root or resolve_trace_root()
    ts = (timestamp or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H-%M-%S")
    suffix = uuid.uuid4().hex[:8]
    invocation_dir = root / f"{ts}_{suffix}"
    invocation_dir.mkdir(parents=True, exist_ok=True)
    return invocation_dir


def write_manifest(
    invocation_dir: Path,
    *,
    http_replay_mode: str | None,
    command_argv: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    payload: dict[str, Any] = {
        "schema": _MANIFEST_SCHEMA,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "host_pid": os.getpid(),
        "http_replay_mode": http_replay_mode,
        "verbose": is_verbose(),
        "max_body_bytes": max_body_bytes(),
        "argv": command_argv if command_argv is not None else list(sys.argv),
    }
    if extra:
        payload.update(extra)
    target = invocation_dir / "manifest.json"
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return target


@dataclass
class RedactedBody:
    length: int
    sha256: str
    head: str
    tail: str | None
    truncated: int

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "length": self.length,
            "sha256": self.sha256,
            "head": self.head,
        }
        if self.tail is not None:
            out["tail"] = self.tail
            out["truncated"] = self.truncated
        return out


def redact_body(body: bytes | str | None, max_per_side: int | None = None) -> dict[str, Any]:
    """Redact ``body`` into a forensic-friendly dict.

    The output preserves enough detail to surface whitespace-level
    matcher mismatches (CR/LF differences are a known cause): ``head``
    and ``tail`` use ``repr()`` so escape sequences are visible. The
    SHA-256 hex (first 16 chars) is the stable fingerprint that
    matchers can cluster on; ``length`` is the total byte count.
    """
    if max_per_side is None:
        max_per_side = max_body_bytes()
    raw = _coerce_to_bytes(body)
    length = len(raw)
    sha = hashlib.sha256(raw).hexdigest()[:16] if length > 0 else ""
    if length <= 2 * max_per_side:
        head = repr(raw)
        tail = None
        truncated = 0
    else:
        head = repr(raw[:max_per_side])
        tail = repr(raw[-max_per_side:])
        truncated = length - 2 * max_per_side
    return RedactedBody(
        length=length,
        sha256=sha,
        head=head,
        tail=tail,
        truncated=truncated,
    ).to_dict()


def _coerce_to_bytes(body: bytes | str | None) -> bytes:
    if body is None:
        return b""
    if isinstance(body, bytes):
        return body
    if isinstance(body, bytearray):
        return bytes(body)
    if isinstance(body, str):
        return body.encode("utf-8", errors="replace")
    read = getattr(body, "read", None)
    if callable(read):
        try:
            data = read()
        except Exception:
            return str(body).encode("utf-8", errors="replace")
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
        return str(data).encode("utf-8", errors="replace")
    return str(body).encode("utf-8", errors="replace")


class TraceWriter:
    """Thread-safe JSONL appender for one trace stream.

    Holds an open append-mode file handle for the lifetime of the
    process so emit() does not pay file-open overhead per event. Writes
    are serialized through a ``threading.Lock`` because a single host
    process emits cassette-lifecycle events from multiple threads
    (concurrent course-file processing).
    """

    def __init__(self, path: Path, *, stream: str) -> None:
        self._path = path
        self._stream = stream
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", encoding="utf-8", newline="\n")
        self._start_mono = time.monotonic()

    @property
    def path(self) -> Path:
        return self._path

    def emit(self, event: str, data: dict[str, Any] | None = None) -> None:
        record = {
            "ts_mono": time.monotonic() - self._start_mono,
            "ts_wall": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "tid": threading.get_ident(),
            "stream": self._stream,
            "event": event,
            "data": data or {},
        }
        line = json.dumps(record, sort_keys=False) + "\n"
        with self._lock:
            self._fh.write(line)
            self._fh.flush()

    def close(self) -> None:
        with self._lock:
            if not self._fh.closed:
                self._fh.close()


class _NullWriter:
    """Stand-in writer used when tracing is disabled. All emits are no-ops."""

    def emit(self, event: str, data: dict[str, Any] | None = None) -> None:
        pass

    def close(self) -> None:
        pass


_writers_lock = threading.Lock()
_writers: dict[str, TraceWriter] = {}
_invocation_dir: Path | None = None


def set_invocation_dir(path: Path | None) -> None:
    """Pin the current process's invocation directory.

    The host process calls this once after creating the trace directory
    via :func:`make_invocation_dir` so subsequent :func:`get_writer`
    calls land in the right place. ``None`` disables tracing for the
    process.
    """
    global _invocation_dir
    with _writers_lock:
        _invocation_dir = path


def get_invocation_dir() -> Path | None:
    """Return the pinned invocation directory, falling back to env var.

    The host process pins via :func:`set_invocation_dir` after creating
    the directory. Direct worker subprocesses inherit env from the host;
    Docker workers receive the var through the explicit allowlist. Either
    way, worker code that imports this module fresh discovers the
    invocation dir via ``CLM_HTTP_REPLAY_TRACE_INVOCATION_DIR``.
    """
    if _invocation_dir is not None:
        return _invocation_dir
    env_value = os.environ.get(_INVOCATION_ENV, "").strip()
    if env_value:
        path = Path(env_value)
        if path.is_dir():
            return path
    return None


def get_writer(stream: str) -> TraceWriter | _NullWriter:
    """Return the writer for ``stream`` (e.g. ``"host"``), or a null writer.

    The file name is ``<stream>.jsonl`` for host-pinned streams and
    ``<stream>-<pid>.jsonl`` when this process is a worker subprocess
    (i.e., the invocation dir was discovered from the env rather than
    pinned in-process). The pid suffix prevents concurrent worker
    processes from racing on the same file.
    """
    if not is_enabled():
        return _NullWriter()
    invocation_dir = get_invocation_dir()
    if invocation_dir is None:
        return _NullWriter()
    with _writers_lock:
        existing = _writers.get(stream)
        if existing is not None:
            return existing
        if _invocation_dir is None:
            file_name = f"{stream}-{os.getpid()}.jsonl"
        else:
            file_name = f"{stream}.jsonl"
        path = invocation_dir / file_name
        writer = TraceWriter(path, stream=stream)
        _writers[stream] = writer
        return writer


def close_all_writers() -> None:
    with _writers_lock:
        for writer in _writers.values():
            try:
                writer.close()
            except Exception:
                pass
        _writers.clear()
