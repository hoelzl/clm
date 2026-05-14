"""Lock-aware file move that never silently leaves duplicates.

``shutil.move`` falls back to ``copy2 + os.unlink`` when ``os.rename``
fails. On Windows this fallback bites whenever the source file is open
elsewhere (typically: an in-flight Auphonic upload holding a read lock):
the copy succeeds, the unlink fails with ``PermissionError``, ``shutil.move``
re-raises — and a duplicate is left at the destination. The recordings
workflow then has two physical files of the same content (we have a real
incident's worth of forensic evidence in ``D:\\CLM\\Recordings/takes`` to
prove this).

:func:`safe_move` swaps that footgun for ``os.replace`` (atomic, no
fallback) plus a bounded retry loop for transient locks (antivirus
scanners, the watcher's own stability check, Auphonic's first few hundred
ms of upload init). When retries exhaust, it raises
:class:`FileLockedError` so callers can decide between "fail loudly" and
"defer the rename until the lock holder finishes" — never produces a
duplicate.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from loguru import logger


class FileLockedError(OSError):
    """Raised by :func:`safe_move` when *src* stays locked through every retry.

    Callers that want a "best-effort, defer on contention" semantic catch
    this and enqueue the rename for later (see
    :class:`~clm.recordings.workflow.rename_queue.PendingRenameQueue`).
    Callers that want hard failure let it propagate — the recordings
    rename pipeline already classifies any exception from a rename step
    as a session-level error.
    """

    def __init__(self, src: Path, dst: Path, attempts: int, last_error: BaseException) -> None:
        self.src = src
        self.dst = dst
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"File {src} stayed locked through {attempts} attempt(s); last error: {last_error}"
        )


def safe_move(
    src: Path,
    dst: Path,
    *,
    retries: int = 3,
    retry_interval: float = 0.5,
) -> Path:
    """Move *src* to *dst* atomically, retrying on transient lock errors.

    Uses :func:`os.replace` so the operation is either fully done or not
    done — no copy-and-leak fallback. Creates *dst*'s parent directory
    if absent. Retries up to *retries* additional times with
    *retry_interval* seconds between attempts when the OS reports the
    file is in use, then raises :class:`FileLockedError`.

    The retry budget is kept short on purpose: the goal is to absorb
    sub-second transients (AV scanners, watcher polling, Auphonic upload
    handshake), not to wait out a multi-minute upload. If the caller
    knows the lock is held by a long-running operation, it should catch
    :class:`FileLockedError` and defer.

    Raises:
        FileLockedError: If every attempt failed with
            :class:`PermissionError` (Windows ``WinError 32``) or the
            equivalent ``OSError`` flavours.
        OSError: For non-lock failures (missing parent dir on a
            read-only filesystem, cross-device move, etc.) — surfaced
            on the first attempt without retry, since retrying won't
            help.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    last_exc: BaseException | None = None
    attempts = retries + 1
    for attempt in range(1, attempts + 1):
        try:
            os.replace(str(src), str(dst))
            if attempt > 1:
                logger.info(
                    "safe_move succeeded on attempt {} after lock cleared: {} → {}",
                    attempt,
                    src,
                    dst,
                )
            return dst
        except PermissionError as exc:
            last_exc = exc
            logger.debug(
                "safe_move attempt {}/{} failed (locked): {} → {}: {}",
                attempt,
                attempts,
                src,
                dst,
                exc,
            )
            if attempt < attempts:
                time.sleep(retry_interval)
                continue
        except OSError:
            raise

    assert last_exc is not None
    raise FileLockedError(src, dst, attempts, last_exc)
