"""Worker logging bootstrap: entry points must actually get their logs out.

Two properties, both regressing silently in production before 1.26.1:

1. **Importing ``clm.core.utils`` (or anything that pulls it in) must not
   configure the root logger.** Until now the package ``__init__`` ran
   ``logging.basicConfig(level=WARNING)`` at import time. Because every
   worker entry point imports clm modules *before* calling its own
   ``basicConfig(level=INFO)``, that import-time handler made each worker's
   ``basicConfig`` a silent no-op (``basicConfig`` adds a handler only when
   the root logger has none). Result: direct and Docker worker logs were
   empty — 0 bytes measured — even in passing runs, which is why the
   worker-registration flakes had nothing to diagnose from.

2. **A real worker subprocess emits its INFO boot lines to stderr.** This is
   the end-to-end pin: spawn the actual notebook worker against a scratch
   jobs DB and assert the boot/registration records are visible.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from clm.infrastructure.database.schema import init_database


def test_importing_clm_core_utils_leaves_root_logger_unconfigured() -> None:
    """No handler may appear on the root logger as a side effect of import."""
    code = (
        "import logging, sys\n"
        "import clm.core.utils\n"
        "handlers = logging.getLogger().handlers\n"
        "print(f'handlers={handlers}')\n"
        "sys.exit(1 if handlers else 0)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        "importing clm.core.utils configured the root logger: "
        f"{proc.stdout.strip()} {proc.stderr.strip()}"
    )


def test_importing_worker_entry_point_leaves_root_logger_unconfigured() -> None:
    """The worker module itself must not configure logging at import time.

    (Its ``main()``/entry path does — via its own ``basicConfig`` — but the
    import must stay pure so *that* call is the one that wins.)
    """
    code = (
        "import logging, sys\n"
        "import clm.workers.notebook.notebook_worker\n"
        "handlers = logging.getLogger().handlers\n"
        "print(f'handlers={handlers}')\n"
        "sys.exit(1 if handlers else 0)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        "importing the notebook worker configured the root logger: "
        f"{proc.stdout.strip()} {proc.stderr.strip()}"
    )


@pytest.mark.integration
def test_notebook_worker_logs_its_boot_at_info(tmp_path: Path) -> None:
    """The real worker subprocess writes its boot records to stderr.

    The executor merges worker stderr into the per-worker log file
    (``CLM_LOG_DIR/workers/<type>-<n>.log``); if the worker's logging is
    misconfigured that file stays empty and worker failures are
    undiagnosable. Spawn the worker exactly as the executor does and
    require the boot lines to be there.
    """
    db_path = tmp_path / "jobs.db"
    init_database(db_path)

    env = dict(os.environ)
    env.update(
        {
            "WORKER_TYPE": "notebook",
            "WORKER_ID": "logging-probe-worker-0",
            "CLM_JOBS_DB_PATH": str(db_path),
            "CLM_LOG_DIR": str(tmp_path / "clm-logs"),
        }
    )

    proc = subprocess.Popen(
        [sys.executable, "-m", "clm.workers.notebook"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    output = ""
    try:
        # Wait until the worker has registered (proves boot completed),
        # then shut it down and read everything it wrote.
        conn = sqlite3.connect(db_path, timeout=30)
        deadline = time.monotonic() + 90
        registered = False
        while time.monotonic() < deadline:
            try:
                rows = conn.execute("SELECT status FROM workers").fetchall()
            except sqlite3.OperationalError:
                rows = []
            if rows:
                registered = True
                break
            if proc.poll() is not None:
                break
            time.sleep(0.05)
        conn.close()
        assert registered, "worker never registered in the jobs database"

        proc.terminate()
        try:
            output, _ = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            output, _ = proc.communicate(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    assert "Starting notebook worker in SQLite mode" in output, (
        f"worker boot INFO line missing from stderr; got:\n{output[:2000]}"
    )
    assert "Registered notebook worker" in output, (
        f"registration INFO line missing from stderr; got:\n{output[:2000]}"
    )
