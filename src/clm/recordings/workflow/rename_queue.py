"""Defer-and-retry queue for renames blocked by an in-flight upload.

When the recording rename pipeline (cascade-to-multi-part, take demote,
supersede, archive) wants to move a file that an Auphonic upload is
currently holding open, :func:`safe_move` raises
:class:`FileLockedError` rather than leaving a duplicate behind. The
caller wraps the move in :meth:`PendingRenameQueue.try_or_defer` so
that on contention the move is parked here and re-attempted later —
either when the queue is drained explicitly (e.g. after a job reaches
a terminal state) or on the next periodic tick.

The queue is intentionally:

* In-process — survives the lifetime of one ``clm recordings serve``
  run. A future iteration could persist to ``<root>/.clm/pending-renames.json``
  if we discover renames stranded across server restarts; today the
  cascade is idempotent enough that the next recording's pipeline will
  re-do it.
* Idempotent — re-enqueueing the same ``(src, dst)`` pair is a no-op,
  so subscribers can safely call drain on every job event.
* Best-effort — drain failures stay queued for the next attempt; only
  non-:class:`FileLockedError` exceptions evict the entry (they would
  fail every time, so retrying wastes work).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from .safe_move import FileLockedError, safe_move


@dataclass(frozen=True)
class PendingRename:
    """One deferred rename: ``src`` should eventually replace ``dst``.

    *reason* is a short human-readable note that ends up in the log when
    the rename is enqueued and again when it drains, so a future post-
    incident reader can correlate "why was this rename queued" with the
    surrounding events.
    """

    src: Path
    dst: Path
    reason: str


class PendingRenameQueue:
    """Thread-safe queue of renames deferred by :func:`safe_move` lock contention."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: list[PendingRename] = []

    def __len__(self) -> int:
        with self._lock:
            return len(self._pending)

    def snapshot(self) -> list[PendingRename]:
        """Return a copy of the queue at this instant — for diagnostics."""
        with self._lock:
            return list(self._pending)

    def try_or_defer(
        self,
        src: Path,
        dst: Path,
        *,
        reason: str,
        on_success: Callable[[Path, Path], None] | None = None,
    ) -> bool:
        """Attempt :func:`safe_move`; on lock contention, enqueue for later.

        Returns ``True`` when the move succeeded immediately, ``False``
        when it was deferred. Other ``OSError`` flavours propagate
        unchanged — they wouldn't be fixed by retrying.

        The optional *on_success* callback fires after a successful
        move (either immediately or later via :meth:`drain`) — used by
        the rename pipeline to mirror the move into ``state.json`` /
        the in-flight job index.
        """
        try:
            safe_move(src, dst)
        except FileLockedError as exc:
            self._enqueue(PendingRename(src=src, dst=dst, reason=reason))
            logger.warning(
                "Rename deferred ({}): {} → {} ({})",
                reason,
                src.name,
                dst,
                exc.last_error,
            )
            return False
        if on_success is not None:
            try:
                on_success(src, dst)
            except Exception as cb_exc:
                logger.warning("on_success callback raised for {} → {}: {}", src, dst, cb_exc)
        return True

    def drain(
        self,
        *,
        predicate: Callable[[PendingRename], bool] | None = None,
        on_success: Callable[[Path, Path], None] | None = None,
    ) -> tuple[list[PendingRename], list[tuple[PendingRename, BaseException]]]:
        """Try every queued rename whose *predicate* returns ``True``.

        When *predicate* is ``None``, every entry is attempted. Entries
        that succeed are removed from the queue. Entries that raise
        :class:`FileLockedError` stay queued for the next call. Entries
        that raise any other exception are removed *and* returned in
        the failures list — these are unrecoverable (missing source,
        permission denied for non-lock reasons, etc.) and should
        surface to the operator.

        Returns ``(succeeded, failed)`` where each list holds the entries
        that were processed in this call. Already-vanished sources are
        silently dropped (treated as success — somebody else moved the
        file, and re-running the move would just raise ``FileNotFoundError``).
        """
        with self._lock:
            candidates = [e for e in self._pending if predicate is None or predicate(e)]
            for e in candidates:
                self._pending.remove(e)

        succeeded: list[PendingRename] = []
        failed: list[tuple[PendingRename, BaseException]] = []
        re_queue: list[PendingRename] = []
        for entry in candidates:
            if not entry.src.exists():
                logger.info(
                    "Pending rename source vanished, dropping ({}): {} → {}",
                    entry.reason,
                    entry.src,
                    entry.dst,
                )
                continue
            try:
                safe_move(entry.src, entry.dst)
            except FileLockedError as exc:
                logger.debug(
                    "Pending rename still locked ({}): {} → {}: {}",
                    entry.reason,
                    entry.src,
                    entry.dst,
                    exc.last_error,
                )
                re_queue.append(entry)
                continue
            except OSError as exc:
                logger.error(
                    "Pending rename failed permanently ({}): {} → {}: {}",
                    entry.reason,
                    entry.src,
                    entry.dst,
                    exc,
                )
                failed.append((entry, exc))
                continue
            logger.info(
                "Pending rename drained ({}): {} → {}",
                entry.reason,
                entry.src.name,
                entry.dst,
            )
            succeeded.append(entry)
            if on_success is not None:
                try:
                    on_success(entry.src, entry.dst)
                except Exception as cb_exc:
                    logger.warning(
                        "on_success callback raised for drained {} → {}: {}",
                        entry.src,
                        entry.dst,
                        cb_exc,
                    )

        if re_queue:
            with self._lock:
                self._pending.extend(re_queue)

        return succeeded, failed

    def _enqueue(self, entry: PendingRename) -> None:
        """Add *entry* unless an identical (src, dst) is already queued."""
        with self._lock:
            for existing in self._pending:
                if existing.src == entry.src and existing.dst == entry.dst:
                    return
            self._pending.append(entry)
