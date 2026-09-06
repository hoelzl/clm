"""Bounded retry for transient SQLite lock contention.

SQLite serialises writers with a single lock per database file. Every CLM
connection sets ``busy_timeout`` (see :mod:`journal_mode`), so a writer already
waits up to 30 s for the lock before ``sqlite3.OperationalError: database is
locked`` is raised — but the busy handler is not fair, and with a dozen or
more worker processes plus the build's own threads all writing to the same
cache/jobs files, a single writer can be starved past that window (issue
#917). A write that gives up at that point is not "slow", it is *lost*: a
dropped result-cache row re-executes a notebook on the next build, and a
dropped terminal job-status write leaves a job in ``processing`` until the
stall detector fires.

:func:`retry_on_busy` wraps such a write in a short, bounded retry with
backoff. Only transient lock errors are retried; every other exception
propagates immediately. Each attempt still pays the connection's own
``busy_timeout``, so the total wait is dominated by that, not by the delays
here — the delays just space out the retries so a starved writer gets new
chances at the lock rather than one long one.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable, Iterable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Backoff between attempts, in seconds. ``len(delays) + 1`` attempts in total.
# Tests shrink this via monkeypatch. Kept short on purpose: each attempt can
# already block for the connection's full ``busy_timeout`` (30 s locally), so
# the worst-case wall clock is ~(attempts x busy_timeout). Four attempts keep
# a fully starved write under ~2 minutes — inside the 120 s heartbeat grace
# another build's stale-row cleanup applies to a worker that is stuck in
# this retry — while still giving a starved writer several fresh chances.
DEFAULT_BUSY_RETRY_DELAYS: tuple[float, ...] = (0.25, 0.5, 1.0)

_TRANSIENT_LOCK_MARKERS = (
    "database is locked",
    "database table is locked",
    "database schema is locked",
    "database is busy",
)


def is_transient_lock_error(exc: BaseException) -> bool:
    """True for the SQLite errors that mean "another writer holds the lock".

    These are the only errors worth retrying: the statement was never
    executed, and repeating it later is safe. Constraint violations, schema
    errors, and I/O errors are not transient and must propagate.
    """
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    message = str(exc).lower()
    return any(marker in message for marker in _TRANSIENT_LOCK_MARKERS)


def retry_on_busy(
    operation: Callable[[], T],
    *,
    label: str,
    delays: Iterable[float] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    on_retry: Callable[[], None] | None = None,
) -> T:
    """Run *operation*, retrying transient lock errors with backoff.

    Args:
        operation: Zero-argument callable performing the write. It must be
            safe to repeat — i.e. either atomic (a single statement, or an
            explicit transaction that is rolled back on failure) or
            idempotent.
        label: Short description for log lines (``"job 42 -> completed"``).
        delays: Backoff schedule; defaults to :data:`DEFAULT_BUSY_RETRY_DELAYS`
            (read at call time so tests can shrink it).
        sleep: Injection point for tests.
        on_retry: Called before each backoff sleep — e.g. a worker refreshing
            its heartbeat so a long retry never looks like a dead process.
            Its own exceptions are logged and ignored.

    Returns:
        Whatever *operation* returns.

    Raises:
        sqlite3.OperationalError: The lock error from the final attempt, once
            the schedule is exhausted. Non-lock exceptions propagate from the
            attempt that raised them.
    """
    schedule = list(DEFAULT_BUSY_RETRY_DELAYS if delays is None else delays)
    attempt = 0
    while True:
        try:
            return operation()
        except sqlite3.OperationalError as exc:
            if not is_transient_lock_error(exc) or attempt >= len(schedule):
                if is_transient_lock_error(exc):
                    logger.warning(
                        f"{label}: still locked after {attempt + 1} attempt(s); giving up: {exc}"
                    )
                raise
            delay = schedule[attempt]
            attempt += 1
            logger.info(
                f"{label}: database locked (attempt {attempt}/{len(schedule) + 1}); "
                f"retrying in {delay:.2f}s"
            )
            if on_retry is not None:
                try:
                    on_retry()
                except Exception:  # pragma: no cover - best-effort hook
                    logger.debug(f"{label}: on_retry hook failed", exc_info=True)
            sleep(delay)
