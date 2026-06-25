"""Per-cell worker activity beacons.

This module provides :class:`WorkerHeartbeatStore`, a tiny SQLite helper that
notebook workers use to publish "what cell am I executing right now?" into the
shared ``clm_jobs.db`` so that ``clm monitor`` and ``clm status`` can show
per-cell progress for busy workers.

Design notes
------------

- The table is in the jobs DB (``clm_jobs.db``), not the cache DB. Heartbeats
  are operational telemetry tied to live workers, not cached results.
- Writes are best-effort: heartbeat failures are logged and swallowed so that
  cell execution is never blocked. A single failing write also short-circuits
  further writes for the *same cell* on the same store instance, to keep a
  pathological DB-lock condition from spraying log lines.
- The store opens its own SQLite connection per thread (using a
  ``threading.local`` cache) and runs in autocommit mode, mirroring the
  pattern used by :class:`clm.infrastructure.database.job_queue.JobQueue`.
- Schema lives in :mod:`clm.infrastructure.database.schema` (table
  ``worker_heartbeats``, added in DB version 8).
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

logger = logging.getLogger(__name__)


def _utc_now_naive() -> datetime:
    """Current UTC time as a naive datetime.

    The collector computes elapsed seconds via SQLite's ``julianday('now')``,
    which is always UTC. Heartbeat timestamps must therefore also be UTC, or
    elapsed values come out negative by the local timezone offset (e.g. on
    a CEST host, ``cell_elapsed`` reads as roughly -7200s). We strip the
    tzinfo so the stored string format stays identical to what SQLite's
    own ``CURRENT_TIMESTAMP`` produces.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _format_timestamp(value: datetime) -> str:
    """Render a datetime in the same format SQLite ``CURRENT_TIMESTAMP`` uses.

    Python 3.12 deprecated the default datetime sqlite3 adapter without a
    drop-in replacement. We format the timestamp inline rather than
    registering a process-wide adapter, so this module stays self-contained
    and doesn't change sqlite3 behaviour for the rest of CLM. Callers must
    pass UTC-naive datetimes (see :func:`_utc_now_naive`) so the resulting
    string matches what ``CURRENT_TIMESTAMP`` would have produced.
    """
    return value.isoformat(sep=" ", timespec="seconds")


# Maximum length of the captured output excerpt (characters). The intent is
# "one human-readable line on a monitor row", not "a transcript" — the full
# output is still in the executed notebook.
MAX_EXCERPT_LENGTH = 120

# If a single UPSERT takes longer than this (seconds), the store logs a
# warning and disables further writes for this store instance so a slow disk
# or a held lock can't degrade cell execution. The threshold is intentionally
# generous: real per-cell cost is tens of microseconds, but on Windows under
# parallel test load or in the presence of WAL checkpoints, an occasional
# spike to 10-20ms is common. We only want to fire on genuine pathologies
# (lock held by a dead writer, filesystem stall, etc.).
#
# Overridable via ``CLM_HEARTBEAT_SLOW_WRITE_THRESHOLD_SECONDS`` so the test
# suite can relax it in one place (``tests/conftest.py`` sets it session-wide,
# and subprocess workers inherit it through the environment) instead of every
# heartbeat test re-patching this constant. The production default stays 50ms —
# do NOT raise it; that defeats the real-disk safety mechanism.
SLOW_WRITE_THRESHOLD_SECONDS = float(
    os.environ.get("CLM_HEARTBEAT_SLOW_WRITE_THRESHOLD_SECONDS", "0.050")
)  # 50 ms in production

# ANSI escape sequences we strip from stream output before truncating.
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from ``text``."""
    return _ANSI_ESCAPE_RE.sub("", text)


def _last_meaningful_line(text: str) -> str:
    """Return the last non-empty line from ``text``.

    Streams arrive in chunks that may end in a trailing newline, contain
    multiple lines, or both. The monitor only has room for one line, so we
    keep the most recent non-blank one.
    """
    if not text:
        return ""
    # Normalize line separators (kernels sometimes send '\r' for progress bars).
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line for line in normalized.split("\n") if line.strip()]
    if not lines:
        return ""
    return lines[-1]


def truncate_excerpt(text: str, max_length: int = MAX_EXCERPT_LENGTH) -> str:
    """Strip ANSI, keep the last meaningful line, and truncate.

    Public so callers (and tests) can produce the same excerpt the live
    write path would produce, without having to construct a store.
    """
    cleaned = _strip_ansi(text)
    last = _last_meaningful_line(cleaned)
    if len(last) > max_length:
        # Reserve 3 chars for the ellipsis indicator.
        return last[: max_length - 3] + "..."
    return last


class WorkerHeartbeatStore:
    """Best-effort writer for the ``worker_heartbeats`` table.

    The store is owned by the worker process. Construct it once per worker
    and reuse it across jobs — it caches a SQLite connection per thread.
    """

    def __init__(self, db_path: Path, worker_id: int):
        """Initialize a heartbeat store.

        Args:
            db_path: Path to the jobs DB (``clm_jobs.db``).
            worker_id: Integer ``workers.id`` for the owning worker.
        """
        self.db_path = db_path
        self.worker_id = worker_id
        self._local = threading.local()
        # Set to True after a write exceeds SLOW_WRITE_THRESHOLD_SECONDS or
        # raises; future writes become no-ops until ``reset_disabled`` is
        # called (typically at the start of a new cell or job).
        self._disabled = False
        # The very first write on a fresh sqlite3 connection is slow because
        # SQLite has to open the DB file, parse WAL state, and prime its
        # page cache — easily 50+ms on Windows. That latency is paid once
        # per process and isn't representative of steady-state per-cell
        # write cost, so the slow-write check skips it.
        self._connection_primed = False

    # -- connection management --------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        """Return a thread-local autocommit connection."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            # Reuse the same WAL-mode DB the JobQueue uses. ``init_database``
            # will have been called by the worker pool startup; we don't
            # re-initialize here to avoid surprising mutations.
            conn = sqlite3.connect(
                str(self.db_path),
                timeout=30.0,
                isolation_level=None,
            )
            self._local.conn = conn
        return cast(sqlite3.Connection, conn)

    def close(self) -> None:
        """Close the thread-local connection if open."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None

    # -- public write API -------------------------------------------------

    def reset_disabled(self) -> None:
        """Re-enable writes after they were disabled by a slow/failing write."""
        self._disabled = False

    def begin_cell(
        self,
        job_id: int | None,
        cell_index: int,
        total_cells: int | None,
    ) -> None:
        """Stamp the start of a new cell.

        Clears the per-cell-disabled state so a slow previous cell does not
        permanently silence the store.
        """
        # New cell → reset the throttle so we get fresh visibility.
        self.reset_disabled()
        now = _utc_now_naive()
        self._upsert(
            job_id=job_id,
            current_cell_index=cell_index,
            total_cells=total_cells,
            current_cell_started_at=now,
            last_output_excerpt=None,
            last_output_at=None,
            heartbeat_at=now,
        )

    def record_output(self, raw_text: str) -> None:
        """Record the last stream output for the currently executing cell.

        ``raw_text`` is the most recent stdout/stderr chunk. The store only
        keeps the last non-empty line, with ANSI stripped and truncated.
        """
        if self._disabled:
            return
        excerpt = truncate_excerpt(raw_text)
        if not excerpt:
            return
        now = _utc_now_naive()
        # Partial update — keep cell index/total/started_at intact.
        self._update_output(excerpt, now)

    def finish_job(self) -> None:
        """Clear the per-job fields when a job ends.

        We keep the worker row (so the entry is observable as "no current
        job") and null out cell-specific fields. The worker shutdown path
        should call :meth:`clear` instead to remove the row entirely.
        """
        if self._disabled:
            # Even if disabled, attempt one clear write — it's the only way
            # to make the monitor stop showing stale info.
            self.reset_disabled()
        now = _utc_now_naive()
        self._upsert(
            job_id=None,
            current_cell_index=None,
            total_cells=None,
            current_cell_started_at=None,
            last_output_excerpt=None,
            last_output_at=None,
            heartbeat_at=now,
        )

    def clear(self) -> None:
        """Delete the worker's heartbeat row entirely.

        Called on worker shutdown so the monitor doesn't render stale
        entries for dead workers.
        """
        try:
            conn = self._get_conn()
            conn.execute(
                "DELETE FROM worker_heartbeats WHERE worker_id = ?",
                (self.worker_id,),
            )
        except Exception as exc:
            logger.debug(
                "Worker %s: failed to clear heartbeat row: %s",
                self.worker_id,
                exc,
            )

    # -- internal --------------------------------------------------------

    def _upsert(
        self,
        *,
        job_id: int | None,
        current_cell_index: int | None,
        total_cells: int | None,
        current_cell_started_at: datetime | None,
        last_output_excerpt: str | None,
        last_output_at: datetime | None,
        heartbeat_at: datetime,
    ) -> None:
        """Full-row UPSERT. Best effort: failures are logged + swallowed."""
        if self._disabled:
            return
        start = time.perf_counter()
        try:
            conn = self._get_conn()
            conn.execute(
                """
                INSERT INTO worker_heartbeats (
                    worker_id, job_id,
                    current_cell_index, total_cells,
                    current_cell_started_at,
                    last_output_excerpt, last_output_at,
                    heartbeat_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    job_id = excluded.job_id,
                    current_cell_index = excluded.current_cell_index,
                    total_cells = excluded.total_cells,
                    current_cell_started_at = excluded.current_cell_started_at,
                    last_output_excerpt = excluded.last_output_excerpt,
                    last_output_at = excluded.last_output_at,
                    heartbeat_at = excluded.heartbeat_at
                """,
                (
                    self.worker_id,
                    job_id,
                    current_cell_index,
                    total_cells,
                    _format_timestamp(current_cell_started_at) if current_cell_started_at else None,
                    last_output_excerpt,
                    _format_timestamp(last_output_at) if last_output_at else None,
                    _format_timestamp(heartbeat_at),
                ),
            )
        except Exception as exc:
            logger.warning(
                "Worker %s heartbeat upsert failed: %s; disabling further writes until next cell.",
                self.worker_id,
                exc,
            )
            self._disabled = True
            return

        self._check_slow_write(start)

    def _update_output(self, excerpt: str, now: datetime) -> None:
        """Partial UPDATE that only touches output fields and heartbeat_at."""
        start = time.perf_counter()
        try:
            conn = self._get_conn()
            now_str = _format_timestamp(now)
            conn.execute(
                """
                UPDATE worker_heartbeats
                SET last_output_excerpt = ?,
                    last_output_at = ?,
                    heartbeat_at = ?
                WHERE worker_id = ?
                """,
                (excerpt, now_str, now_str, self.worker_id),
            )
        except Exception as exc:
            logger.warning(
                "Worker %s heartbeat output update failed: %s; disabling "
                "further writes until next cell.",
                self.worker_id,
                exc,
            )
            self._disabled = True
            return

        self._check_slow_write(start)

    def _check_slow_write(self, start_perf_counter: float) -> None:
        elapsed = time.perf_counter() - start_perf_counter
        # The first write per connection pays a one-time priming cost
        # (file open, WAL header parse, page cache warm-up). Skip the
        # slow-write check exactly once so a cold open doesn't permanently
        # silence the store. Subsequent writes on the same connection do
        # get checked, because that latency *is* representative of disk
        # contention / lock pressure we want to detect.
        if not self._connection_primed:
            self._connection_primed = True
            return
        if elapsed > SLOW_WRITE_THRESHOLD_SECONDS:
            logger.warning(
                "Worker %s heartbeat write took %.1fms (>%dms); disabling "
                "further writes for this cell.",
                self.worker_id,
                elapsed * 1000.0,
                int(SLOW_WRITE_THRESHOLD_SECONDS * 1000),
            )
            self._disabled = True
