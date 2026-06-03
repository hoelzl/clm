"""Git provenance capture for build outputs.

Captures the HEAD commit and dirty state of a course *source* repository at
build time, so generated outputs can be correlated with the exact source
revision they were derived from (issue #208, step 1).

This is a core-level helper deliberately free of any dependency on the
optional ``[recordings]`` extra, which carries its own loguru-based copy in
:mod:`clm.recordings.git_info`. The build pipeline must not import an optional
extra, so the logic is duplicated here with stdlib logging; the two may be
converged later (recordings could re-export this one).
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def get_git_info(repo_path: Path) -> dict[str, Any]:
    """Return ``{"commit": <sha|None>, "dirty": <bool|None>}`` for *repo_path*.

    ``commit`` and ``dirty`` are ``None`` when git is unavailable or
    *repo_path* is not inside a git work tree. Capturing provenance must never
    fail a build, so every git error is swallowed and reported as ``None``.
    """
    commit = _git_rev_parse(repo_path)
    dirty = _git_is_dirty(repo_path) if commit else None
    return {"commit": commit, "dirty": dirty}


def _git_rev_parse(repo_path: Path) -> str | None:
    """Return the HEAD commit hash, or ``None`` on any failure."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as e:
        logger.debug("git rev-parse failed in %s: %s", repo_path, e)
        return None


def _git_is_dirty(repo_path: Path) -> bool | None:
    """Return whether the work tree has uncommitted changes, or ``None``."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip() != ""
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as e:
        logger.debug("git status failed in %s: %s", repo_path, e)
        return None
