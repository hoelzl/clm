"""Shared process-reaping helpers.

This module owns the terminate-then-kill-survivors sequence used whenever
CLM needs to reliably kill a process (and optionally its descendants) on
Windows and Unix alike.

It has two callers:

1. :mod:`clm.workers.notebook.notebook_processor` — via
   :func:`reap_kernel_descendants`, which snapshots a kernel's child
   processes *before* running ``shutdown_kernel`` and then reaps any that
   outlived the kernel (Fix 2).
2. :mod:`clm.cli.commands.workers` — via :func:`scan_worker_processes`
   and :func:`reap_process_tree`, which scans for surviving
   ``python -m clm.workers.*`` processes from dead pool sessions and
   kills the whole tree (Fix 5).

Why a separate module?
----------------------

Before Fix 5, the terminate/kill logic lived inline inside
``reap_kernel_descendants`` in ``notebook_processor.py``. Fix 5 needs the
same sequence to kill full worker process trees discovered by a
cross-run psutil scan — a different caller, same primitive. Factoring
the sequence out avoids duplication and centralises the (slightly
fiddly) Windows-vs-Unix terminate/kill semantics in one place.

The helper is pure psutil: no CLM-specific state, no I/O other than
logging. That makes it easy to unit-test with mocked ``psutil.Process``
objects.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import psutil  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


# Prefixes of worker module names as passed to ``python -m``.  Any process
# whose cmdline looks like ``<python> -m clm.workers.<something>`` is a
# candidate for :func:`scan_worker_processes`.  Kept as a tuple so callers
# can extend it easily (e.g., future worker types) without a string join.
WORKER_MODULE_PREFIXES: tuple[str, ...] = (
    "clm.workers.notebook",
    "clm.workers.plantuml",
    "clm.workers.drawio",
)

# Timeout (seconds) each termination wave waits before escalating.
_TERMINATE_WAIT = 2
_KILL_WAIT = 2


def terminate_then_kill_procs(
    procs: list[psutil.Process],
    log_prefix: str = "",
) -> int:
    """Terminate a list of processes, wait, then force-kill survivors.

    This is the low-level primitive used by both the kernel-descendant
    reap and the worker-process-tree reap. It takes whatever processes
    the caller hands it and:

    1. Filters out anything already dead.
    2. Calls :meth:`psutil.Process.terminate` on each (``SIGTERM`` on
       Unix, ``TerminateProcess`` on Windows).
    3. Waits up to :data:`_TERMINATE_WAIT` seconds via
       :func:`psutil.wait_procs`.
    4. Force-kills any survivors via :meth:`psutil.Process.kill`.
    5. Waits once more so the caller knows the OS has had a chance to
       clean up.

    The terminate/kill loops tolerate ``psutil.NoSuchProcess`` (the
    process died between the liveness check and the call) and
    ``psutil.AccessDenied`` (which on Windows usually means the process
    is owned by a different session) without raising — an unreapable
    process is logged at DEBUG and skipped.

    A WARNING is emitted when anything has to be force-killed, because
    that indicates a process refused a graceful terminate and is the
    diagnostic signal operators need when chasing runaway workers.

    Args:
        procs: Candidate processes. May contain already-dead entries;
            the helper filters them out.
        log_prefix: Optional prefix for log lines (e.g., a correlation
            ID or worker ID) so multiple concurrent reaps are
            distinguishable in log output.

    Returns:
        The number of processes that were alive when the helper started
        — i.e., the number the caller should report as "reaped".
    """
    live = [p for p in procs if _is_running(p)]
    if not live:
        return 0

    prefix = f"{log_prefix}: " if log_prefix else ""

    for proc in live:
        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied as exc:
            logger.debug(f"{prefix}Access denied terminating pid={proc.pid}: {exc}")

    _gone, alive = psutil.wait_procs(live, timeout=_TERMINATE_WAIT)

    if alive:
        logger.warning(
            f"{prefix}{len(alive)} process(es) survived terminate; force-killing "
            f"(pids={[p.pid for p in alive]})"
        )
        for proc in alive:
            try:
                proc.kill()
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied as exc:
                logger.debug(f"{prefix}Access denied killing pid={proc.pid}: {exc}")
        # Best-effort final wait; if anything is *still* alive after this
        # the OS is in a bad state and there is nothing more this helper
        # can usefully do.
        psutil.wait_procs(alive, timeout=_KILL_WAIT)

    return len(live)


def _is_running(proc: psutil.Process) -> bool:
    """Return True if ``proc`` is still alive, tolerating psutil errors.

    ``psutil.Process.is_running`` can raise ``NoSuchProcess`` if the pid
    has been reused. We treat any exception here as "not running" so
    downstream terminate/kill calls are skipped rather than crashing.
    """
    try:
        # psutil is untyped so ``is_running`` is Any; coerce to bool for
        # the explicit return annotation.
        return bool(proc.is_running())
    except psutil.Error:
        return False


def reap_process_tree(pid: int, log_prefix: str = "") -> int:
    """Kill a process and every descendant it still has.

    Looks up ``pid`` via psutil, snapshots its recursive children
    (grandchildren included), and hands the full ``[parent] +
    descendants`` list to :func:`terminate_then_kill_procs`.

    This is the Fix 5 primitive for killing a surviving worker process
    that was found by a psutil scan of the user's machine. The snapshot
    must be taken before we terminate the parent, because once the
    parent dies psutil cannot walk its tree any more.

    Args:
        pid: The root pid of the tree to reap. Typically a surviving
            CLM worker process found by :func:`scan_worker_processes`.
        log_prefix: Optional prefix for log lines.

    Returns:
        The number of processes (root + descendants) that were alive
        and got a terminate signal. ``0`` means the pid was already
        dead by the time the helper ran.
    """
    try:
        root = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return 0

    try:
        descendants = root.children(recursive=True)
    except psutil.NoSuchProcess:
        descendants = []

    # Build the full list root-first. Reaping the children before the
    # root avoids a race where the root respawns children during its
    # own terminate window — there is no such respawn for a dying CLM
    # worker in practice, but keeping the root at the front matches the
    # kernel-descendant reap shape and keeps the semantics simple.
    all_procs: list[psutil.Process] = [root, *descendants]
    return terminate_then_kill_procs(all_procs, log_prefix=log_prefix)


@dataclass(frozen=True)
class DiscoveredWorkerProcess:
    """A surviving ``python -m clm.workers.*`` process found by scanning.

    ``DB_PATH`` and ``worker_id`` come from the worker's environment
    (see ``DirectWorkerExecutor.start_worker``) and are best-effort:
    psutil cannot always read a process's environment on Windows, in
    which case these fields are ``None``. Callers must be prepared for
    that and decide whether to skip, warn, or reap anyway.

    Attributes:
        pid: The worker process pid.
        worker_module: The module that was run (e.g., ``clm.workers.notebook``).
        cmdline: The cmdline as psutil reported it, for logging.
        db_path: Absolute DB path the worker was started with, or
            ``None`` if the environ could not be read.
        worker_id: The CLM-assigned worker id (``WORKER_ID`` env var),
            or ``None`` if unreadable.
        cwd: Working directory when the process started, or ``None``
            if unreadable.
    """

    pid: int
    worker_module: str
    cmdline: list[str]
    db_path: Path | None
    worker_id: str | None
    cwd: Path | None

    @property
    def worker_type(self) -> str:
        """Short type label derived from ``worker_module`` — ``notebook``, etc."""
        # clm.workers.notebook -> notebook
        return self.worker_module.rsplit(".", 1)[-1]


def scan_worker_processes() -> list[DiscoveredWorkerProcess]:
    """Find every surviving ``python -m clm.workers.*`` process.

    Iterates :func:`psutil.process_iter` (fetching only ``pid`` +
    ``cmdline``, which is cheap on both Linux and Windows) and matches
    the cmdline shape produced by
    ``DirectWorkerExecutor.start_worker``: the first argument is
    ``-m`` and the second starts with one of :data:`WORKER_MODULE_PREFIXES`.

    For each match, the helper then best-effort reads the process's
    environment and working directory via :meth:`psutil.Process.environ`
    and :meth:`psutil.Process.cwd`. These are the signals the CLI needs
    to decide whether a given surviving worker belongs to the operator's
    current worktree (``DB_PATH`` match) or a different one. On Windows
    these calls can raise ``psutil.AccessDenied`` — the helper catches
    that and reports the process with ``db_path=None`` so the CLI can
    surface it as "unknown provenance" rather than silently skipping
    it.

    Returns:
        A list of :class:`DiscoveredWorkerProcess`. Empty if no
        surviving workers were found. The order is whatever
        ``process_iter`` happens to yield and should not be relied on
        by callers.
    """
    found: list[DiscoveredWorkerProcess] = []

    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            info = proc.info
        except psutil.NoSuchProcess:
            continue
        cmdline = info.get("cmdline") or []
        worker_module = _match_worker_module(cmdline)
        if worker_module is None:
            continue

        db_path, worker_id = _read_worker_env(proc)
        cwd = _read_worker_cwd(proc)

        found.append(
            DiscoveredWorkerProcess(
                pid=info["pid"],
                worker_module=worker_module,
                cmdline=list(cmdline),
                db_path=db_path,
                worker_id=worker_id,
                cwd=cwd,
            )
        )

    return found


def _match_worker_module(cmdline: list[str]) -> str | None:
    """Return the ``clm.workers.*`` module if ``cmdline`` looks like a worker.

    Expected shape: ``[<python>, '-m', 'clm.workers.<type>', ...]``.
    Anything else returns ``None``. We match on the module-name prefix
    rather than a strict ``==`` so future worker submodules (e.g.,
    ``clm.workers.notebook.__main__``) still light up.
    """
    if len(cmdline) < 3:
        return None
    if cmdline[1] != "-m":
        return None
    module = cmdline[2]
    for prefix in WORKER_MODULE_PREFIXES:
        if module == prefix or module.startswith(prefix + "."):
            return prefix
    return None


def _read_worker_env(proc: psutil.Process) -> tuple[Path | None, str | None]:
    """Best-effort read of ``DB_PATH`` and ``WORKER_ID`` env vars.

    Both are set by ``DirectWorkerExecutor.start_worker``. On Windows
    the call can fail with ``AccessDenied`` (e.g., for a process owned
    by another session) — in that case we return ``(None, None)`` and
    let the CLI surface it as unknown provenance.
    """
    try:
        environ = proc.environ()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None, None
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(f"Failed to read environ for pid={proc.pid}: {exc}")
        return None, None

    raw_db = environ.get("DB_PATH")
    db_path = Path(raw_db) if raw_db else None
    # Normalise empty-string env var to None so callers only need to
    # check for a single "no info" sentinel.
    worker_id = environ.get("WORKER_ID") or None
    return db_path, worker_id


def _read_worker_cwd(proc: psutil.Process) -> Path | None:
    """Best-effort read of the process's working directory.

    Used purely for display — helps operators tell apart workers
    launched from different worktrees.
    """
    try:
        raw = proc.cwd()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(f"Failed to read cwd for pid={proc.pid}: {exc}")
        return None
    return Path(raw) if raw else None
