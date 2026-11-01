"""Filesystem utilities: project-root discovery and atomic writes.

The course-domain path vocabulary (output specs, skip/ignore rules, slide
family detection, prog-lang mapping) lives in
:mod:`clm.core.utils.path_utils` (Phase 8 step A6, #802) — this module keeps
only the helpers that actually touch the filesystem or the environment.
"""

import errno
import logging
import os
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


#: Markers that identify a CLM/Python project root, in per-directory precedence
#: order (the FIRST that matches at the nearest ancestor wins). ``pyproject.toml``
#: is checked first because it is the file that actually carries ``[tool.clm]
#: cache_dir`` — so a nested project's own ``pyproject.toml`` correctly wins over
#: an outer one. A bare ``.clm/`` directory is **deliberately NOT a marker**: a
#: *topic* directory has its own ``.clm/`` (voiceover scratch + the committed sync
#: ledger — see :data:`clm.core.utils.path_utils.SKIP_DIRS_FOR_COURSE`), so
#: treating the bare dir as a root marker would stop the ascent at a topic and
#: defeat the walk-up. A topic-local ``.clm/`` has no ``config.toml``, so the
#: ``.clm/config.toml`` marker is safe.
_PROJECT_ROOT_FILE_MARKERS = ("pyproject.toml", ".clm/config.toml", "clm.toml")


def find_project_root(start: Path | None = None) -> Path:
    """Walk up from ``start`` (default: cwd) to the nearest project root.

    Mirrors how ``git`` / ``uv`` / ``ruff`` resolve a project: ascend the
    directory tree until a root marker is found, so a CLM command behaves
    identically no matter which subdirectory it is invoked from. Markers, in
    per-directory precedence: ``pyproject.toml``, ``.clm/config.toml``,
    ``clm.toml``, then a ``.git`` entry. Returns the **resolved** marked
    directory, or — when no marker exists anywhere up to the filesystem root —
    the resolved ``start`` (preserving today's cwd-as-root behavior for
    directories that are genuinely outside any project).

    ``.git`` is matched with :meth:`Path.exists` (not ``is_dir``) because a
    linked git **worktree** records its ``.git`` as a *file*, not a directory;
    using ``is_dir`` would skip worktrees. This finds the project root only —
    distinct from :func:`clm.infrastructure.llm.cache._main_worktree_root`,
    which answers the different question "what root is *shared* across linked
    worktrees" (git-common-dir based) and re-anchors a relative ``cache_dir``.
    """
    base = (start or Path.cwd()).resolve()
    for directory in (base, *base.parents):
        for marker in _PROJECT_ROOT_FILE_MARKERS:
            if (directory / marker).is_file():
                return directory
        # ``.git`` is a directory in a normal checkout, a FILE in a linked
        # worktree — ``.exists()`` covers both.
        if (directory / ".git").exists():
            return directory
    return base


def atomic_write_all(
    writes: list[tuple[Path, str]],
    *,
    max_retries: int = 5,
    base_delay: float = 0.05,
) -> None:
    """Write several ``(path, text)`` outputs as atomically as the FS allows.

    Every text is first written to a sibling ``*.tmp`` file; only after **all**
    temp writes succeed are they ``os.replace``-d into place back-to-back.

    What is guaranteed:

    * A failure during the temp phase (disk full, permission) leaves every
      real target untouched.
    * Each ``os.replace`` is retried with backoff on the transient Windows
      errnos (:data:`_TRANSIENT_WRITE_ERRNOS` — an antivirus / indexer /
      editor handle briefly held on the target), exactly like
      :func:`atomic_write_bytes` (#1099).
    * If a replace still fails, the targets already replaced in this call are
      **rolled back** best-effort: each is restored from the bytes it held
      before the call (read just before the replace phase), or removed if it
      did not exist. The original error is then re-raised.
    * Leftover temps are cleaned up either way.

    What is NOT guaranteed: cross-file atomicity. A crash (power loss, kill)
    mid-replace, or a rollback write that itself fails, can still leave some
    targets new and some old — that needs a journal. A failed rollback is
    logged and attached to the raised exception as a note.

    Shared by the slide rewriters that emit several coupled files in one op —
    ``split`` / ``unify`` (a deck plus its voiceover companion), the paired
    ``voiceover extract`` (two slide halves plus two companions), and the v3
    write surface (``harvest accept`` et al.). With plain per-file writes a
    mid-operation failure could leave a one-sided companion (the very
    orphaning these seams prevent).
    """
    temps: list[tuple[Path, Path]] = []
    try:
        for path, text in writes:
            tmp = path.with_name(path.name + ".tmp")
            # Registered before the write so a half-written temp is cleaned up.
            temps.append((tmp, path))
            tmp.write_text(text, encoding="utf-8", newline="\n")
        # Snapshot the pre-call state of every target so a mid-replace
        # failure can undo the replaces that already landed.
        originals: list[bytes | None] = [
            path.read_bytes() if path.exists() else None for _tmp, path in temps
        ]
        for index, (tmp, path) in enumerate(temps):
            try:
                _replace_with_retry(tmp, path, max_retries=max_retries, base_delay=base_delay)
            except BaseException as exc:
                # BaseException too: a Ctrl-C during the retry backoff must
                # not leave the earlier targets replaced either.
                _roll_back(
                    [(p, originals[i]) for i, (_t, p) in enumerate(temps[:index])],
                    exc,
                )
                raise
    finally:
        for tmp, _ in temps:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove leftover temp file %s", tmp)


def _replace_with_retry(src: Path, dst: Path, *, max_retries: int, base_delay: float) -> None:
    """``os.replace(src, dst)``, retrying transient Windows errnos with backoff.

    Always makes at least one attempt, so ``max_retries <= 0`` can never
    silently skip the replace.
    """
    max_retries = max(1, max_retries)
    for attempt in range(max_retries):
        try:
            os.replace(src, dst)
            return
        except OSError as exc:
            if exc.errno not in _TRANSIENT_WRITE_ERRNOS or attempt + 1 >= max_retries:
                raise
            logger.warning(
                "Transient OSError replacing %s (errno=%s, attempt %d/%d): %s — retrying",
                dst,
                exc.errno,
                attempt + 1,
                max_retries,
                exc,
            )
            time.sleep(base_delay * (2**attempt))


def _roll_back(replaced: list[tuple[Path, bytes | None]], cause: BaseException) -> None:
    """Best-effort restore of already-replaced targets after a failed replace.

    Never raises: a restore failure is logged and noted on ``cause`` so the
    caller's original error stays the one that propagates.
    """
    for path, original in reversed(replaced):
        try:
            if original is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write_bytes(path, original)
        except OSError as restore_exc:
            logger.error("Rollback of %s failed after a write error: %s", path, restore_exc)
            cause.add_note(f"rollback of {path} failed: {restore_exc}")


# Errnos that, on Windows, are routinely produced when antivirus, the
# search indexer, or a cloud-sync agent (Defender, OneDrive, Dropbox) is
# briefly holding a handle on a file in the destination directory while
# CLM is rapid-writing many results. EINVAL in particular shows up when
# CreateFileW races with such a handle on an O_TRUNC open.
_TRANSIENT_WRITE_ERRNOS = frozenset({errno.EACCES, errno.EBUSY, errno.EINVAL, errno.EPERM})


def atomic_write_bytes(
    path: Path,
    data: bytes,
    *,
    max_retries: int = 5,
    base_delay: float = 0.05,
) -> None:
    """Write ``data`` to ``path`` atomically and resiliently.

    The bytes are first written to a unique sibling temp file, then
    ``os.replace``-d into place. The destination is therefore never opened
    with ``O_TRUNC`` in place — this avoids most CreateFileW races with
    Windows antivirus / search-indexer / cloud-sync handles, which manifest
    as ``OSError [Errno 22] Invalid argument`` on otherwise-valid paths.

    Transient ``OSError``s during either the temp write or the rename are
    retried with exponential backoff so a short scan window doesn't fail
    the build. Non-transient errors (e.g. ``ENOSPC``) propagate immediately.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    last_err: OSError | None = None
    for attempt in range(max_retries):
        # Fresh temp name per attempt — if a previous attempt left a stale
        # temp behind that itself can't be unlinked, we don't keep retrying
        # against it.
        tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_bytes(data)
            os.replace(tmp, path)
            return
        except OSError as exc:
            last_err = exc
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            if exc.errno not in _TRANSIENT_WRITE_ERRNOS:
                raise
            if attempt + 1 < max_retries:
                logger.warning(
                    "Transient OSError writing %s (errno=%s, attempt %d/%d): %s — retrying",
                    path,
                    exc.errno,
                    attempt + 1,
                    max_retries,
                    exc,
                )
                time.sleep(base_delay * (2**attempt))

    assert last_err is not None
    raise last_err
