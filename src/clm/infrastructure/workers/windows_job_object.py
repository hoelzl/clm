"""Windows JobObject helpers for reliable worker process tree cleanup.

Background
----------

On Windows, ``subprocess.Popen.terminate()`` calls ``TerminateProcess``,
which kills only the target pid — not its descendants. CLM's notebook
workers spawn Jupyter kernel subprocesses (which in turn may spawn their
own children for cells that use ``multiprocessing`` or ``subprocess``).
When a worker is terminated mid-job, the kernel and anything it launched
become orphans. Over hundreds of build iterations this accumulates into
gigabytes of leaked RAM.

This module wraps the Windows JobObject API to give CLM a Windows-native
process-tree cleanup mechanism equivalent to Unix process groups. A
JobObject configured with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` will
terminate every process in the job the instant its handle is closed.
Children of processes in the job are automatically in the job by default,
so assigning the top-level worker pid is enough to cover the whole tree.

Public API
----------

``WorkerJobObject`` is the only public symbol. It is a cross-platform
facade — every method is a no-op on non-Windows platforms, so callers do
not need to special-case their code.

Typical use inside ``DirectWorkerExecutor``::

    self._job = WorkerJobObject()

    def start_worker(...):
        process = subprocess.Popen(...)
        self._job.assign(process)

    def cleanup(...):
        for worker_id in list(self.processes):
            self.stop_worker(worker_id)     # graceful path first
        self._job.close()                   # kernel-level safety net
"""

from __future__ import annotations

import logging
import subprocess
import sys

logger = logging.getLogger(__name__)


class WorkerJobObject:
    """Cross-platform facade around a Windows kill-on-close JobObject.

    On Windows, instantiating this class creates a new unnamed JobObject
    configured with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``. Every
    subprocess passed to :meth:`assign` is placed into the job. Calling
    :meth:`close` (or letting the object be garbage-collected) closes the
    job handle, which instructs Windows to terminate every process still
    in the job.

    On non-Windows platforms, every method is a no-op. Unix already has
    process groups and ``killpg`` for the same purpose — the Unix branches
    in ``DirectWorkerExecutor.stop_worker`` handle that path.
    """

    def __init__(self) -> None:
        self._handle: int | None = None
        self._closed: bool = False

        if sys.platform != "win32":
            return

        try:
            self._handle = _create_kill_on_close_job()
            logger.debug(
                "Created Windows JobObject (handle=0x%x) with KILL_ON_JOB_CLOSE",
                self._handle,
            )
        except OSError as e:
            # Never fail worker startup just because the JobObject could
            # not be created. Fall back to the pre-fix behavior (leaky but
            # functional) and log a warning so operators can investigate.
            logger.warning(
                "Failed to create Windows JobObject (%s). "
                "Worker tree cleanup will not be reliable on this run.",
                e,
            )
            self._handle = None

    def assign(self, process: subprocess.Popen) -> None:
        """Place ``process`` into the JobObject.

        No-op on non-Windows, or if the JobObject could not be created, or
        if the JobObject has already been closed. Should be called
        immediately after ``subprocess.Popen()`` returns, before the child
        has had a chance to spawn its own children.
        """
        if self._handle is None or self._closed:
            return
        if process.pid is None:
            return

        try:
            _assign_process_to_job(self._handle, process.pid)
            logger.debug(
                "Assigned pid %d to JobObject (handle=0x%x)",
                process.pid,
                self._handle,
            )
        except OSError as e:
            logger.warning(
                "Failed to assign pid %d to JobObject: %s. "
                "This worker's descendants may leak on termination.",
                process.pid,
                e,
            )

    def close(self) -> None:
        """Close the job handle, terminating all processes in the job.

        Idempotent: safe to call multiple times. No-op on non-Windows or if
        the JobObject could not be created.
        """
        if self._closed:
            return
        self._closed = True

        if self._handle is None:
            return

        handle = self._handle
        self._handle = None
        try:
            _close_handle(handle)
            logger.debug("Closed Windows JobObject (handle=0x%x)", handle)
        except OSError as e:
            logger.warning("Failed to close Windows JobObject: %s", e)

    def __del__(self) -> None:
        # Belt-and-suspenders: make sure the job is closed even if the
        # owning executor forgot. Under normal operation this is a no-op
        # because ``close()`` has already been called explicitly.
        try:
            self.close()
        except Exception:
            # ``__del__`` must never raise.
            pass


# ---------------------------------------------------------------------------
# Windows implementation
# ---------------------------------------------------------------------------
#
# Only defined when we are actually on Windows, so importing this module is
# cheap and free of ctypes overhead on other platforms.


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: when the last handle to the job
    # is closed, Windows terminates every process associated with the job.
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

    # JOBOBJECTINFOCLASS value for JobObjectExtendedLimitInformation.
    _JobObjectExtendedLimitInformation = 9

    # Access rights needed by AssignProcessToJobObject on the target process.
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_SET_QUOTA = 0x0100

    _ULONG_PTR = ctypes.c_size_t  # pointer-sized unsigned integer

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", _ULONG_PTR),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]

    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]

    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]

    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]

    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    def _raise_win_error(op: str) -> None:
        err = ctypes.get_last_error()
        raise OSError(err, f"{op} failed (WinError {err})")

    def _create_kill_on_close_job() -> int:
        """Create an unnamed JobObject with KILL_ON_JOB_CLOSE set.

        Returns the raw HANDLE as a Python int.
        """
        handle = _kernel32.CreateJobObjectW(None, None)
        if not handle:
            _raise_win_error("CreateJobObjectW")

        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = _kernel32.SetInformationJobObject(
            handle,
            _JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            err = ctypes.get_last_error()
            _kernel32.CloseHandle(handle)
            raise OSError(err, f"SetInformationJobObject failed (WinError {err})")

        return int(handle)

    def _assign_process_to_job(job_handle: int, pid: int) -> None:
        """Open ``pid`` and assign it to ``job_handle``.

        Uses ``OpenProcess`` with the minimum access rights required by
        ``AssignProcessToJobObject`` (PROCESS_SET_QUOTA | PROCESS_TERMINATE).
        """
        proc_handle = _kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        if not proc_handle:
            _raise_win_error(f"OpenProcess(pid={pid})")
        try:
            ok = _kernel32.AssignProcessToJobObject(job_handle, proc_handle)
            if not ok:
                _raise_win_error(f"AssignProcessToJobObject(pid={pid})")
        finally:
            _kernel32.CloseHandle(proc_handle)

    def _close_handle(handle: int) -> None:
        ok = _kernel32.CloseHandle(handle)
        if not ok:
            _raise_win_error("CloseHandle")

else:
    # Non-Windows stubs. These are never invoked in practice because
    # WorkerJobObject.__init__ short-circuits on non-Windows platforms,
    # but defining them keeps type-checkers happy and surfaces clear
    # errors if they are ever called by mistake.

    def _create_kill_on_close_job() -> int:  # pragma: no cover
        raise OSError("Windows JobObjects are only available on Windows")

    def _assign_process_to_job(job_handle: int, pid: int) -> None:  # pragma: no cover
        raise OSError("Windows JobObjects are only available on Windows")

    def _close_handle(handle: int) -> None:  # pragma: no cover
        raise OSError("Windows JobObjects are only available on Windows")
