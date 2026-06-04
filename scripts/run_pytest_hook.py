#!/usr/bin/env python
"""Pre-commit pytest wrapper.

Git exports ``GIT_DIR``, ``GIT_INDEX_FILE``, ``GIT_WORK_TREE``,
``GIT_COMMON_DIR``, ``GIT_PREFIX``, ``GIT_OBJECT_DIRECTORY``, and
``GIT_ALTERNATE_OBJECT_DIRECTORIES`` into every hook subprocess. Any test
that shells out to ``git init`` or ``git add`` in a ``tmp_path`` directory
will inherit these variables and end up writing to the **main** repo's
``.git/`` directory instead — which is locked by the in-progress commit
transaction. This wrapper clears those variables before invoking pytest,
giving the test's subprocesses a clean environment so their ``git -C
<tmp_path>`` calls create their own gitdirs as intended.

Problem 2's blast radius is wider than just "20 failing tests": racing
subprocess writes against the main repo's shared ``.git/`` can also
corrupt ``.git/index`` (writing tmp_path-derived entries) and
``.git/config`` (one observed case: ``core.bare = true`` written by a
racing ``git init``, breaking work-tree operations on the main repo).
Clearing the GIT_* vars eliminates all of these symptoms at the root.

See ``docs/proposals/PRE_COMMIT_HOOK_HARDENING.md`` for the full context.
"""

from __future__ import annotations

import os
import subprocess
import sys

LEAKING_GIT_VARS = (
    "GIT_DIR",
    "GIT_INDEX_FILE",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_PREFIX",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)

# Cap how many workers ``-n auto`` (set in ``pyproject.toml`` ``addopts``)
# spins up for the pre-commit fast suite. On a high-core developer machine
# ``auto`` resolves to one worker per logical CPU (e.g. 64). Many tests fork
# heavyweight subprocesses (mitmdump, Jupyter kernels, worker API servers), so
# that many workers oversubscribes the box: process startups miss tight
# readiness ceilings and flake (the 10s mitmdump timeout, worker-registration
# starvation under xdist, heartbeat slow-write trips, ephemeral port clashes).
# Benchmarking the fast suite across 12-64 workers on a 64-thread box showed
# wall-clock is flat/noise-dominated in this range, so capping costs no real
# speed while removing the contention that drives the flakes.
#
# Cap history:
#   * 16 -> 8 (2026-06-04, PR #214): a 16-worker run *hard-deadlocked* the fast
#     suite during the 1.7.0 release bump's pre-commit commit (worker-
#     registration starvation, hung > 5 min). An emergency firefight, made
#     before the structural fix below had landed.
#   * 8 -> 16 (2026-06-04, later same day): PR #217 (587038c) fixed that
#     deadlock's ROOT CAUSE — the worker-registration family
#     (tests/infrastructure/workers/test_lifecycle_mock.py, #163) is now pinned
#     to its own ``serial`` xdist_group, so it can no longer race the parallel
#     remainder regardless of worker count. With the contention source
#     serialized, the 8-cap became a redundant over-correction that only slowed
#     the ~6.5k-test parallel remainder (the serial group runs on one worker
#     and is independent of this cap). Measured on the 64-thread dev box:
#     cap 8 ~= 94s, cap 12 ~= 80s, cap 16 ~= 73s, all green; many consecutive
#     16-worker runs showed zero flakes/deadlock. Restoring 16 (the benchmarked
#     plateau) recovers ~22% wall-clock at no observed contention cost. The
#     other contention-sensitive family — the recordings session-state polls —
#     was made event-driven in the same change (``_wait_for_state`` in
#     tests/recordings/test_session.py now blocks on the session's
#     ``on_state_change`` callback instead of busy-spinning, so it can no longer
#     starve the very background thread it waits on as the worker count rises).
#     Two real-subprocess long-poles (the mitmdump prototype smoke tests and the
#     real-ipykernel reaping tests) were also moved to the ``integration``
#     marker, off the per-commit path entirely. If a contention flake ever
#     recurs, dial back toward 12 — but do NOT drop below the point where
#     lifecycle_mock stays serialized (that, not the cap, is the #163 fix).
#
# ``xdist`` honours ``PYTEST_XDIST_AUTO_NUM_WORKERS`` to decide what ``auto``
# becomes, so we set it here for the hook only. This:
#   * is a no-op on machines with <= ``_MAX_AUTO_WORKERS`` logical CPUs (their
#     ``auto`` is already at or below the cap);
#   * never touches CI, which doesn't run this wrapper and resolves ``auto`` to
#     its ~2-4 vCPUs anyway;
#   * leaves a manual ``pytest`` run untouched;
#   * respects an explicit ``PYTEST_XDIST_AUTO_NUM_WORKERS`` the developer has
#     already exported (``setdefault``).
_MAX_AUTO_WORKERS = 16


def main() -> int:
    env = os.environ.copy()
    for var in LEAKING_GIT_VARS:
        env.pop(var, None)
    env.setdefault(
        "PYTEST_XDIST_AUTO_NUM_WORKERS",
        str(min(_MAX_AUTO_WORKERS, os.cpu_count() or _MAX_AUTO_WORKERS)),
    )
    result = subprocess.run(
        ["uv", "run", "pytest", *sys.argv[1:]],
        env=env,
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
