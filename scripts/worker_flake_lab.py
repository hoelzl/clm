#!/usr/bin/env python3
"""Reproduce and measure the direct-worker test flake families.

When a real-worker test starts timing out under ``pytest -m "not docker"``
(rotating ``TimeoutError: worker did not reach idle`` failures), the
investigation methodology is written up in
``docs/claude/design/test-flakiness-root-causes.md`` (§5 for mock-pool
contention, §10 for the direct-worker boot thundering herd). This tool is
the harness that doc was measured with, promoted out of session scratch so
the next investigation does not have to rebuild it.

Three subcommands, matching the investigation's three steps:

``boot``
    Spawn ONE worker exactly as ``DirectWorkerExecutor`` does (same env:
    ``WORKER_TYPE``, ``WORKER_ID``, ``CLM_JOBS_DB_PATH``, ``CLM_LOG_DIR``)
    against a scratch jobs DB, and timestamp Popen → DB-registered-idle.
    This is the cold baseline: ~1.4 s on an idle box. If THIS is already
    slow, the flake is not a herd effect — look at import time
    (``python -X importtime -m clm.workers.notebook``) or the DB path.

``herd``
    Spawn N workers simultaneously, each against its own scratch DB, and
    report each one's boot latency (min/max/mean). Boot latency scales with
    concurrent boots (measured on a 64-core box: 16 ≈ 4 s, 48 ≈ 10 s). Run
    with the N your xdist uses (``-n auto`` on many-core machines) to see
    whether the herd alone can push boots past the tests' 15 s poll.

``repro``
    Run the real-worker test files under the default ``-n auto`` N times,
    keeping per-run pytest logs and the worker logs each run produced.
    Use BEFORE changing anything (baseline flake rate) and AFTER (fix
    verification). A fix is only verified green when this loop is green
    several times in a row on a loaded machine.

All scratch output goes to ``.scratch/worker-flake-lab/`` (gitignored).
Windows-first: no POSIX-only calls; ``CREATE_NEW_PROCESS_GROUP`` used for
spawned workers exactly like the executor.

Usage::

    python scripts/worker_flake_lab.py boot
    python scripts/worker_flake_lab.py herd -n 16
    python scripts/worker_flake_lab.py repro -n 5

Run from the repo root (the script locates the repo via its own path).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
SCRATCH = REPO / ".scratch" / "worker-flake-lab"

# The real-worker test files (the flake family). Keep in sync with the
# serial("workerpool") residents listed in docs/developer-guide/testing.md.
WORKER_TEST_FILES = [
    "tests/infrastructure/workers/test_lifecycle_integration.py",
    "tests/infrastructure/workers/test_direct_integration.py",
]

# How the executor polls: workers flip their DB row created → idle on boot.
_POLL_INTERVAL_S = 0.02
_BOOT_DEADLINE_S = 120.0

# Windows: put spawned workers in their own process group so terminate()
# reaches them even if the parent is killed; harmless on POSIX (flag = 0).
_CREATE_FLAGS = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def _prepare_db(db: Path) -> None:
    """Create a scratch jobs DB with the workers table a worker registers in."""
    sys.path.insert(0, str(REPO / "src"))
    from clm.infrastructure.database.schema import init_database  # noqa: PLC0415

    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()
    init_database(db)


def _spawn_worker(db: Path, log_dir: Path, worker_id: str) -> subprocess.Popen:
    """Spawn one direct worker with exactly the executor's env contract."""
    env = dict(os.environ)
    env.update(
        {
            "WORKER_TYPE": "notebook",
            "WORKER_ID": worker_id,
            "CLM_JOBS_DB_PATH": str(db),
            "CLM_LOG_DIR": str(log_dir),
        }
    )
    return subprocess.Popen(
        [sys.executable, "-m", "clm.workers.notebook"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_CREATE_FLAGS,
    )


def _await_idle(db: Path, proc: subprocess.Popen, t0: float) -> tuple[float | None, int | None]:
    """Poll the DB until the worker registers idle; return (latency, early-exit)."""
    conn = sqlite3.connect(db, timeout=60)
    try:
        deadline = time.monotonic() + _BOOT_DEADLINE_S
        while time.monotonic() < deadline:
            try:
                rows = conn.execute("SELECT status FROM workers").fetchall()
            except sqlite3.OperationalError:
                rows = []
            if any(status == "idle" for (status,) in rows):
                return time.perf_counter() - t0, None
            rc = proc.poll()
            if rc is not None:
                return None, rc
            time.sleep(_POLL_INTERVAL_S)
        return None, None
    finally:
        conn.close()


def _stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def cmd_boot(_args: argparse.Namespace) -> int:
    """One worker, idle box: the cold-boot baseline."""
    SCRATCH.mkdir(parents=True, exist_ok=True)
    db = SCRATCH / "boot" / "probe.db"
    _prepare_db(db)
    t0 = time.perf_counter()
    proc = _spawn_worker(db, db.parent / "clm-logs", "boot-probe-worker")
    latency, exit_code = _await_idle(db, proc, t0)
    _stop(proc)
    if latency is not None:
        print(f"cold boot → registered idle: {latency:.2f}s")
        print("(baseline ~1.4s on an idle box; if this is slow, it is NOT a herd effect)")
        return 0
    print(f"worker FAILED to register (early exit {exit_code}) — see {db.parent / 'clm-logs'}")
    return 1


def cmd_herd(args: argparse.Namespace) -> int:
    """N workers at once: how boot latency scales with the herd."""
    SCRATCH.mkdir(parents=True, exist_ok=True)

    def one(i: int) -> dict[str, Any]:
        d = SCRATCH / "herd" / f"w{i}"
        db = d / "probe.db"
        _prepare_db(db)
        t0 = time.perf_counter()
        proc = _spawn_worker(db, d / "clm-logs", f"herd-worker-{i}")
        latency, exit_code = _await_idle(db, proc, t0)
        _stop(proc)
        return {"i": i, "boot_s": round(latency, 2) if latency else None, "early_exit": exit_code}

    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.n) as pool:
        results = sorted(pool.map(one, range(args.n)), key=lambda r: r["i"])
    wall = time.monotonic() - t0

    boots = [r["boot_s"] for r in results if r["boot_s"] is not None]
    print(f"N={args.n} wall={wall:.1f}s")
    if boots:
        print(
            f"registered: n={len(boots)} min={min(boots):.2f} max={max(boots):.2f} "
            f"mean={sum(boots) / len(boots):.2f}"
        )
    else:
        print("none registered")
    for r in results:
        if r["boot_s"] is None:
            print(f"  FAILED: {r}")
    print(
        "(tests poll for registration with a 15s timeout — herd max above ~15s reproduces the flake)"
    )
    return 0 if boots else 1


def cmd_repro(args: argparse.Namespace) -> int:
    """N pytest runs of the worker-family files under -n auto: the flake rate."""
    out = SCRATCH / "repro"
    out.mkdir(parents=True, exist_ok=True)
    failures = 0
    for run in range(1, args.n + 1):
        t0 = time.monotonic()
        proc = subprocess.run(
            ["uv", "run", "pytest", "-m", "not docker", *WORKER_TEST_FILES, "-q"],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=900,
        )
        wall = time.monotonic() - t0
        text = proc.stdout + proc.stderr
        logdir = out / f"run{run}"
        logdir.mkdir(parents=True, exist_ok=True)
        (logdir / "pytest.log").write_text(text, encoding="utf-8")
        summary = next(
            (ln for ln in text.splitlines() if " passed" in ln or " failed" in ln),
            "",
        )
        failed_names = [ln.split()[1] for ln in text.splitlines() if ln.startswith("FAILED ")]
        (logdir / "result.json").write_text(
            json.dumps(
                {
                    "run": run,
                    "exit": proc.returncode,
                    "wall_s": round(wall, 1),
                    "summary": summary.strip(),
                    "failed": failed_names,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        if proc.returncode != 0:
            failures += 1
        print(f"run {run}: exit={proc.returncode} wall={wall:.0f}s :: {summary.strip()[:120]}")
        for name in failed_names:
            print(f"    FAILED: {name}")
    print(f"flake rate: {failures}/{args.n} runs failed")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    boot = sub.add_parser("boot", help="cold-boot latency of ONE direct worker")
    boot.set_defaults(func=cmd_boot)

    herd = sub.add_parser("herd", help="boot-latency scaling with N simultaneous workers")
    herd.add_argument("-n", type=int, default=16, help="herd size (default 16)")
    herd.set_defaults(func=cmd_herd)

    repro = sub.add_parser("repro", help="flake rate of the worker-family test files under -n auto")
    repro.add_argument("-n", type=int, default=5, help="number of pytest runs (default 5)")
    repro.set_defaults(func=cmd_repro)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
