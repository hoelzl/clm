"""Tests for the shared process-reaping helpers (Fix 5).

These tests cover the new :mod:`clm.infrastructure.workers.process_reaper`
module:

- :func:`terminate_then_kill_procs` — the low-level terminate/wait/kill
  sequence shared between the kernel-descendant reap (Fix 2) and the
  worker-tree reap (Fix 5).
- :func:`reap_process_tree` — looks up a pid, snapshots descendants,
  delegates to the low-level helper.
- :func:`scan_worker_processes` — ``psutil.process_iter`` scanner that
  finds surviving ``python -m clm.workers.*`` processes.

All tests mock ``psutil`` rather than spawning real processes. The
helper is pure psutil with no I/O other than logging, so mocks exercise
every branch deterministically.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import psutil  # type: ignore[import-untyped]
import pytest

from clm.infrastructure.workers.process_reaper import (
    DiscoveredWorkerProcess,
    reap_process_tree,
    scan_worker_processes,
    terminate_then_kill_procs,
)

# ---------------------------------------------------------------------------
# Fake psutil.Process helpers
# ---------------------------------------------------------------------------


def _fake_proc(
    pid: int = 100,
    *,
    is_running: bool = True,
    terminate_raises: Exception | None = None,
    kill_raises: Exception | None = None,
) -> MagicMock:
    """Build a MagicMock that looks like a ``psutil.Process``.

    The mock answers the handful of methods the reaper actually calls:
    ``pid`` attribute, ``is_running``, ``terminate``, ``kill``. Raising
    exceptions from ``terminate`` / ``kill`` lets tests exercise the
    NoSuchProcess / AccessDenied tolerance paths.
    """
    proc = MagicMock(spec=psutil.Process)
    proc.pid = pid
    proc.is_running.return_value = is_running
    if terminate_raises is not None:
        proc.terminate.side_effect = terminate_raises
    if kill_raises is not None:
        proc.kill.side_effect = kill_raises
    return proc


# ---------------------------------------------------------------------------
# terminate_then_kill_procs
# ---------------------------------------------------------------------------


class TestTerminateThenKillProcs:
    """Unit tests for :func:`terminate_then_kill_procs`."""

    def test_empty_list_returns_zero(self):
        """Nothing to reap is a fast no-op, not an error."""
        assert terminate_then_kill_procs([]) == 0

    def test_all_already_dead_returns_zero(self):
        """Processes that are already gone count as zero reaped.

        Callers (both Fix 2 and Fix 5) pass lists that may contain
        already-dead entries, so the filter must short-circuit before
        calling terminate().
        """
        procs = [_fake_proc(pid=1, is_running=False), _fake_proc(pid=2, is_running=False)]
        assert terminate_then_kill_procs(procs) == 0
        for p in procs:
            p.terminate.assert_not_called()
            p.kill.assert_not_called()

    def test_graceful_terminate_no_force_kill(self):
        """When every process dies on terminate, no force-kill is needed."""
        procs = [_fake_proc(pid=1), _fake_proc(pid=2)]
        with patch("clm.infrastructure.workers.process_reaper.psutil.wait_procs") as wait_procs:
            wait_procs.return_value = (procs, [])  # all gone, none alive
            assert terminate_then_kill_procs(procs) == 2

        for p in procs:
            p.terminate.assert_called_once()
            p.kill.assert_not_called()

    def test_force_kill_survivors(self, caplog):
        """Survivors of the terminate wave must be force-killed.

        Also asserts the diagnostic WARNING fires — that warning is
        the operator signal Fix 2's docstring calls out as "the
        diagnostic signal the team has been missing".
        """
        survivor = _fake_proc(pid=42)
        dying = _fake_proc(pid=43)

        with patch("clm.infrastructure.workers.process_reaper.psutil.wait_procs") as wait_procs:
            # First wait: survivor is still alive, dying is gone.
            # Second wait: cleanup after kill, anything goes.
            wait_procs.side_effect = [([dying], [survivor]), ([survivor], [])]
            with caplog.at_level("WARNING"):
                assert terminate_then_kill_procs([survivor, dying]) == 2

        survivor.terminate.assert_called_once()
        survivor.kill.assert_called_once()
        dying.terminate.assert_called_once()
        dying.kill.assert_not_called()
        assert "force-killing" in caplog.text
        assert "42" in caplog.text  # survivor pid in the log line

    def test_tolerates_no_such_process_on_terminate(self):
        """A process that dies between is_running and terminate must not crash."""
        dead = _fake_proc(pid=1, terminate_raises=psutil.NoSuchProcess(1))
        alive = _fake_proc(pid=2)
        with patch("clm.infrastructure.workers.process_reaper.psutil.wait_procs") as wait_procs:
            wait_procs.return_value = ([alive], [])
            # Should not raise — dead.terminate raises NoSuchProcess internally.
            assert terminate_then_kill_procs([dead, alive]) == 2

    def test_tolerates_access_denied_on_terminate(self):
        """AccessDenied during terminate must not crash the whole pass."""
        denied = _fake_proc(pid=1, terminate_raises=psutil.AccessDenied(1))
        ok = _fake_proc(pid=2)
        with patch("clm.infrastructure.workers.process_reaper.psutil.wait_procs") as wait_procs:
            wait_procs.return_value = ([ok], [])
            assert terminate_then_kill_procs([denied, ok]) == 2

    def test_tolerates_no_such_process_on_kill(self):
        """NoSuchProcess during force-kill path is also tolerated."""
        zombie = _fake_proc(pid=1, kill_raises=psutil.NoSuchProcess(1))
        with patch("clm.infrastructure.workers.process_reaper.psutil.wait_procs") as wait_procs:
            wait_procs.side_effect = [([], [zombie]), ([zombie], [])]
            assert terminate_then_kill_procs([zombie]) == 1

    def test_tolerates_access_denied_on_kill(self):
        """AccessDenied during force-kill path is also tolerated."""
        denied = _fake_proc(pid=1, kill_raises=psutil.AccessDenied(1))
        with patch("clm.infrastructure.workers.process_reaper.psutil.wait_procs") as wait_procs:
            wait_procs.side_effect = [([], [denied]), ([denied], [])]
            assert terminate_then_kill_procs([denied]) == 1

    def test_log_prefix_appears_in_warning(self, caplog):
        """A ``log_prefix`` should appear in force-kill warnings.

        Operators chasing a runaway job use the prefix to correlate
        log lines when several reaps run in parallel.
        """
        survivor = _fake_proc(pid=99)
        with patch("clm.infrastructure.workers.process_reaper.psutil.wait_procs") as wait_procs:
            wait_procs.side_effect = [([], [survivor]), ([survivor], [])]
            with caplog.at_level("WARNING"):
                terminate_then_kill_procs([survivor], log_prefix="worker-42")
        assert "worker-42" in caplog.text


# ---------------------------------------------------------------------------
# reap_process_tree
# ---------------------------------------------------------------------------


class TestReapProcessTree:
    """Unit tests for :func:`reap_process_tree`."""

    def test_pid_gone_returns_zero(self):
        """If psutil.Process raises NoSuchProcess, the reap is a no-op."""
        with patch("clm.infrastructure.workers.process_reaper.psutil.Process") as mock_process:
            mock_process.side_effect = psutil.NoSuchProcess(999)
            assert reap_process_tree(999) == 0

    def test_reaps_root_and_descendants(self):
        """Root + recursive children get handed to the low-level helper."""
        grandchild = _fake_proc(pid=300)
        child = _fake_proc(pid=200)
        root = _fake_proc(pid=100)
        root.children.return_value = [child, grandchild]

        with (
            patch(
                "clm.infrastructure.workers.process_reaper.psutil.Process",
                return_value=root,
            ),
            patch("clm.infrastructure.workers.process_reaper.terminate_then_kill_procs") as ttk,
        ):
            ttk.return_value = 3
            assert reap_process_tree(100) == 3

        # Check the list ordering: root first, then its descendants.
        called_with = ttk.call_args[0][0]
        assert called_with[0].pid == 100  # root
        assert [p.pid for p in called_with[1:]] == [200, 300]

    def test_children_no_such_process_is_tolerated(self):
        """If the root dies between Process() and children(), reap empty list.

        This is the "pid was alive at lookup, dead at tree walk"
        micro-race. The helper must still try to kill the root itself
        rather than abandoning the reap entirely.
        """
        root = _fake_proc(pid=100)
        root.children.side_effect = psutil.NoSuchProcess(100)

        with (
            patch(
                "clm.infrastructure.workers.process_reaper.psutil.Process",
                return_value=root,
            ),
            patch("clm.infrastructure.workers.process_reaper.terminate_then_kill_procs") as ttk,
        ):
            ttk.return_value = 1
            assert reap_process_tree(100) == 1

        called_with = ttk.call_args[0][0]
        assert len(called_with) == 1
        assert called_with[0].pid == 100


# ---------------------------------------------------------------------------
# scan_worker_processes
# ---------------------------------------------------------------------------


def _fake_iter_proc(
    pid: int,
    cmdline: list[str],
    *,
    environ: dict | None = None,
    environ_exc: Exception | None = None,
    cwd: str | None = None,
    cwd_exc: Exception | None = None,
) -> MagicMock:
    """Build a fake process for ``psutil.process_iter`` consumption.

    ``psutil.process_iter(['pid', 'cmdline'])`` yields ``Process``
    objects whose ``info`` attribute is a dict of the requested
    fields. The scanner then calls ``environ()`` and ``cwd()`` on the
    same object for enrichment. This fake exposes all three.
    """
    proc = MagicMock(spec=psutil.Process)
    proc.pid = pid
    proc.info = {"pid": pid, "cmdline": cmdline}
    if environ_exc is not None:
        proc.environ.side_effect = environ_exc
    else:
        proc.environ.return_value = environ or {}
    if cwd_exc is not None:
        proc.cwd.side_effect = cwd_exc
    else:
        proc.cwd.return_value = cwd or ""
    return proc


class TestScanWorkerProcesses:
    """Unit tests for :func:`scan_worker_processes`."""

    def test_empty_process_list_returns_empty(self):
        with patch(
            "clm.infrastructure.workers.process_reaper.psutil.process_iter",
            return_value=iter([]),
        ):
            assert scan_worker_processes() == []

    def test_ignores_non_worker_processes(self):
        """Random OS processes must not match the worker filter."""
        procs = [
            _fake_iter_proc(1, ["/usr/bin/python", "script.py"]),
            _fake_iter_proc(2, ["systemd"]),
            _fake_iter_proc(3, ["python", "-m", "pip", "install"]),
        ]
        with patch(
            "clm.infrastructure.workers.process_reaper.psutil.process_iter",
            return_value=iter(procs),
        ):
            assert scan_worker_processes() == []

    def test_detects_notebook_worker(self):
        """Canonical notebook worker cmdline lights up the scanner."""
        env = {
            "DB_PATH": "/tmp/clm_jobs.db",
            "WORKER_ID": "direct-notebook-0-abc123",
            "WORKER_TYPE": "notebook",
        }
        proc = _fake_iter_proc(
            4242,
            ["/usr/bin/python", "-m", "clm.workers.notebook"],
            environ=env,
            cwd="/home/user/proj",
        )
        with patch(
            "clm.infrastructure.workers.process_reaper.psutil.process_iter",
            return_value=iter([proc]),
        ):
            found = scan_worker_processes()

        assert len(found) == 1
        result = found[0]
        assert isinstance(result, DiscoveredWorkerProcess)
        assert result.pid == 4242
        assert result.worker_module == "clm.workers.notebook"
        assert result.worker_type == "notebook"
        assert result.db_path == Path("/tmp/clm_jobs.db")
        assert result.worker_id == "direct-notebook-0-abc123"
        assert result.cwd == Path("/home/user/proj")

    def test_detects_drawio_and_plantuml_workers(self):
        """All three worker module prefixes light up the scanner."""
        procs = [
            _fake_iter_proc(
                100,
                ["python", "-m", "clm.workers.drawio"],
                environ={"DB_PATH": "/x.db"},
            ),
            _fake_iter_proc(
                200,
                ["python", "-m", "clm.workers.plantuml"],
                environ={"DB_PATH": "/x.db"},
            ),
        ]
        with patch(
            "clm.infrastructure.workers.process_reaper.psutil.process_iter",
            return_value=iter(procs),
        ):
            found = scan_worker_processes()

        types = {p.worker_type for p in found}
        assert types == {"drawio", "plantuml"}

    def test_matches_submodule_prefix(self):
        """Future ``clm.workers.notebook.__main__``-style cmdlines still match."""
        proc = _fake_iter_proc(
            1,
            ["python", "-m", "clm.workers.notebook.subrunner"],
            environ={"DB_PATH": "/x.db"},
        )
        with patch(
            "clm.infrastructure.workers.process_reaper.psutil.process_iter",
            return_value=iter([proc]),
        ):
            found = scan_worker_processes()
        assert len(found) == 1
        assert found[0].worker_module == "clm.workers.notebook"

    def test_unreadable_environ_yields_none_fields(self):
        """AccessDenied on environ must not skip the process entirely.

        Fix 5's CLI relies on unreadable-env processes being reported
        so operators can see them (and opt in to killing them with
        --all). Skipping would hide orphans.
        """
        proc = _fake_iter_proc(
            9,
            ["python", "-m", "clm.workers.notebook"],
            environ_exc=psutil.AccessDenied(9),
            cwd_exc=psutil.AccessDenied(9),
        )
        with patch(
            "clm.infrastructure.workers.process_reaper.psutil.process_iter",
            return_value=iter([proc]),
        ):
            found = scan_worker_processes()
        assert len(found) == 1
        assert found[0].pid == 9
        assert found[0].db_path is None
        assert found[0].worker_id is None
        assert found[0].cwd is None

    def test_process_vanishing_mid_scan_is_tolerated(self):
        """NoSuchProcess during info access must not crash the scan."""

        class VanishingProc(MagicMock):
            pid = 7

            @property
            def info(self):
                raise psutil.NoSuchProcess(self.pid)

        proc = VanishingProc(spec=psutil.Process)
        good = _fake_iter_proc(
            8,
            ["python", "-m", "clm.workers.notebook"],
            environ={"DB_PATH": "/x.db"},
        )
        with patch(
            "clm.infrastructure.workers.process_reaper.psutil.process_iter",
            return_value=iter([proc, good]),
        ):
            found = scan_worker_processes()
        assert [p.pid for p in found] == [8]

    def test_cmdline_shorter_than_three_is_ignored(self):
        """A truncated cmdline must not IndexError the scanner."""
        procs = [
            _fake_iter_proc(1, []),
            _fake_iter_proc(2, ["python"]),
            _fake_iter_proc(3, ["python", "-m"]),
        ]
        with patch(
            "clm.infrastructure.workers.process_reaper.psutil.process_iter",
            return_value=iter(procs),
        ):
            assert scan_worker_processes() == []

    def test_empty_db_path_env_maps_to_none(self):
        """``DB_PATH=""`` must become ``None``, not ``Path("")``."""
        proc = _fake_iter_proc(
            5,
            ["python", "-m", "clm.workers.notebook"],
            environ={"DB_PATH": "", "WORKER_ID": ""},
        )
        with patch(
            "clm.infrastructure.workers.process_reaper.psutil.process_iter",
            return_value=iter([proc]),
        ):
            found = scan_worker_processes()
        assert len(found) == 1
        assert found[0].db_path is None
        assert found[0].worker_id is None


# ---------------------------------------------------------------------------
# DiscoveredWorkerProcess
# ---------------------------------------------------------------------------


class TestDiscoveredWorkerProcess:
    """Small sanity checks on the dataclass helpers."""

    def test_worker_type_from_module(self):
        dwp = DiscoveredWorkerProcess(
            pid=1,
            worker_module="clm.workers.notebook",
            cmdline=["python", "-m", "clm.workers.notebook"],
            db_path=None,
            worker_id=None,
            cwd=None,
        )
        assert dwp.worker_type == "notebook"

    def test_frozen(self):
        """Must be frozen so callers cannot mutate scanner results."""
        dwp = DiscoveredWorkerProcess(
            pid=1,
            worker_module="clm.workers.drawio",
            cmdline=[],
            db_path=None,
            worker_id=None,
            cwd=None,
        )
        with pytest.raises(Exception):  # noqa: B017 - dataclass FrozenInstanceError
            dwp.pid = 2  # type: ignore[misc]
