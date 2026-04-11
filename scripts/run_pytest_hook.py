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


def main() -> int:
    env = os.environ.copy()
    for var in LEAKING_GIT_VARS:
        env.pop(var, None)
    result = subprocess.run(
        ["uv", "run", "pytest", *sys.argv[1:]],
        env=env,
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
