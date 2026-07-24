"""Journal-mode policy for CLM's SQLite databases.

WAL is the right journal mode for a local database: readers and writers no
longer block each other, which is what makes concurrent workers viable. But
SQLite's WAL index lives in a memory-mapped ``-shm`` file, and that mapping is
**not coherent across machines** on a network share. SQLite documents WAL over
a network filesystem as unsupported, and the consequences are not subtle: two
machines can each believe they claimed the same pending job, and interleaved
checkpoints can corrupt the database file outright.

CLM supports sharing a jobs database across machines (the ``execution_mode``
job tags and session-ownership columns exist for exactly that), so this is a
real configuration, not a hypothetical one.

**Every** connection to a CLM database must configure itself through
:func:`configure_connection`. ``journal_mode`` is a persistent property of the
database *file*, so a single connection setting WAL silently undoes the safe
mode another connection chose — which is why this policy lives in one place
instead of at each ``sqlite3.connect`` call site.

This is an interim measure (decision D6 in
``docs/claude/handovers/adversarial-review-remediation-handover.md``). The
long-term fix (D5) is to stop opening CLM databases over a network share at
all, routing cross-machine access through the worker API so that exactly one
machine owns the file.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Local databases: WAL plus a relaxed synchronous setting. At worst the last
# committed transaction is lost on power failure — never corruption — which is
# the right trade for a queue and caches that can be rebuilt.
_LOCAL_BUSY_TIMEOUT_MS = 30_000

# Network-hosted databases: rollback journaling, full fsync, and a longer lock
# timeout because a share adds latency to every lock acquisition.
_NETWORK_BUSY_TIMEOUT_MS = 60_000

# POSIX filesystem types that are network-backed. Checked against the mount
# table; anything not listed is treated as local.
_NETWORK_FSTYPES = frozenset(
    {
        "afs",
        "cifs",
        "coda",
        "fuse.sshfs",
        "fuse.davfs",
        "ftpfs",
        "glusterfs",
        "lustre",
        "ncpfs",
        "nfs",
        "nfs4",
        "smb2",
        "smbfs",
    }
)

_DRIVE_REMOTE = 4  # winbase.h DRIVE_REMOTE


class NetworkJournalModeError(RuntimeError):
    """Raised when a network-hosted database cannot be moved off WAL mode.

    Almost always means another connection — typically on another machine —
    still holds the database in WAL mode. Continuing would reintroduce exactly
    the corruption risk this module exists to prevent, so we refuse instead.
    """


def _is_unc(path: Path) -> bool:
    """True for a UNC path such as ``\\\\server\\share\\clm_jobs.db``."""
    text = str(path)
    return text.startswith("\\\\") or text.startswith("//")


def _windows_drive_is_remote(path: Path) -> bool:
    """True when *path* sits on a mapped network drive (``Z:`` and friends)."""
    drive = path.drive
    if not drive or not drive.endswith(":"):
        return False
    import ctypes

    # windll exists only on Windows; the unused-ignore companion keeps this
    # clean under mypy on both Windows (where the attribute resolves) and
    # POSIX (where it does not).
    get_drive_type = ctypes.windll.kernel32.GetDriveTypeW  # type: ignore[attr-defined, unused-ignore]
    get_drive_type.argtypes = [ctypes.c_wchar_p]
    return bool(get_drive_type(f"{drive}\\") == _DRIVE_REMOTE)


def _posix_mount_is_network(path: Path) -> bool:
    """True when *path* falls under a network-backed mount point."""
    try:
        mounts = Path("/proc/mounts").read_text(encoding="utf-8")
    except OSError:
        return False

    best_len = -1
    best_fstype = ""
    for line in mounts.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        mount_point, fstype = fields[1], fields[2]
        # Longest matching mount point wins, exactly as the kernel resolves it.
        if str(path) == mount_point or str(path).startswith(mount_point.rstrip("/") + "/"):
            if len(mount_point) > best_len:
                best_len, best_fstype = len(mount_point), fstype
    return best_fstype in _NETWORK_FSTYPES


def is_network_path(path: Path | str) -> bool:
    """Return True when *path* lives on a network share.

    Detection is best-effort and fails *open* (returns False): an undetected
    share keeps today's behaviour rather than breaking a working setup, and the
    consequence of a false negative is the pre-existing risk, not a new one. A
    false positive merely costs some performance.
    """
    try:
        candidate = Path(path).expanduser()
        # resolve() so a symlink or relative path is judged by where it lands.
        # strict=False: the database file usually does not exist yet.
        resolved = candidate.resolve()
        if _is_unc(resolved) or _is_unc(candidate):
            return True
        if sys.platform == "win32":
            return _windows_drive_is_remote(resolved)
        return _posix_mount_is_network(resolved)
    except Exception:  # pragma: no cover — detection must never break a build
        logger.debug("Network-path detection failed for %s; assuming local", path, exc_info=True)
        return False


def configure_connection(
    conn: sqlite3.Connection,
    db_path: Path | str,
    *,
    synchronous: str | None = None,
) -> str:
    """Apply CLM's journal/durability pragmas to *conn* and return the mode.

    Args:
        conn: An open connection to the database at *db_path*.
        db_path: Path the connection was opened against. Used to decide whether
            the database is network-hosted.
        synchronous: Override for the ``synchronous`` pragma. Callers on a hot
            write path may relax this for a *local* database; it is ignored for
            network-hosted databases, which always use FULL.

    Returns:
        The journal mode now in effect, upper-cased (``"WAL"`` or ``"DELETE"``).

    Raises:
        NetworkJournalModeError: The database is network-hosted and could not be
            switched out of WAL mode.
    """
    network = is_network_path(db_path)
    desired = "DELETE" if network else "WAL"

    row = conn.execute(f"PRAGMA journal_mode={desired}").fetchone()
    actual = str(row[0]).upper() if row else ""

    if network and actual == "WAL":
        raise NetworkJournalModeError(
            f"{db_path} is on a network share and is still in WAL mode. WAL is "
            "unsafe over a network filesystem: the shared-memory index is not "
            "coherent between machines, so job claims can race and the database "
            "can be corrupted.\n\n"
            "The switch to DELETE journaling failed, which normally means "
            "another connection — often on another machine — currently has the "
            "database open. Close every other CLM process using it and retry, "
            "or point CLM_JOBS_DB_PATH at a local database."
        )

    if network:
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute(f"PRAGMA busy_timeout={_NETWORK_BUSY_TIMEOUT_MS}")
        logger.info(
            "%s is network-hosted: using %s journaling with synchronous=FULL. "
            "This is slower than WAL but safe over a share.",
            db_path,
            actual or desired,
        )
    else:
        conn.execute(f"PRAGMA synchronous={synchronous or 'NORMAL'}")
        conn.execute("PRAGMA wal_autocheckpoint=1000")
        conn.execute(f"PRAGMA busy_timeout={_LOCAL_BUSY_TIMEOUT_MS}")

    return actual or desired
