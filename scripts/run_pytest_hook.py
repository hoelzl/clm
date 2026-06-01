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
