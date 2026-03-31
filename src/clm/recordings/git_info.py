"""Git commit capture for recording assignment.

Captures the current HEAD commit and dirty state of a course repository
at the time a recording is assigned, so recordings can be correlated
with the exact version of the course materials.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from loguru import logger


def get_git_info(repo_path: Path) -> dict[str, Any]:
    """Get current git commit hash and dirty status for a repository.

    Args:
        repo_path: Path to the git repository.

    Returns:
        Dict with 'commit' (str or None) and 'dirty' (bool or None).
        Values are None if git operations fail (e.g., not a git repo).
    """
    commit = _git_rev_parse(repo_path)
    dirty = _git_is_dirty(repo_path) if commit else None
    return {"commit": commit, "dirty": dirty}


def _git_rev_parse(repo_path: Path) -> str | None:
    """Get the HEAD commit hash, or None on failure."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.debug("git rev-parse failed in {}: {}", repo_path, e)
        return None


def _git_is_dirty(repo_path: Path) -> bool | None:
    """Check if the working tree has uncommitted changes."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip() != ""
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.debug("git status failed in {}: {}", repo_path, e)
        return None
