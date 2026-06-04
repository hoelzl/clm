"""Centralized log path management for CLM.

This module provides consistent log file paths across all CLM components,
including the main CLI and worker processes.
"""

import os
from pathlib import Path

import platformdirs

#: Environment variable that overrides the log directory. When set, CLM writes
#: ``clm.log`` (and ``workers/``) under this directory instead of the system
#: default. This exists so that processes which must not share the single
#: global log file can each point at their own directory — most importantly the
#: test suite under pytest-xdist, where many worker processes would otherwise
#: race to open/rotate the same ``clm.log`` and hit ``PermissionError`` on
#: Windows. It is also a convenient way to relocate logs in production.
LOG_DIR_ENV_VAR = "CLM_LOG_DIR"


def get_log_dir() -> Path:
    """Get the system-appropriate log directory for CLM.

    If the ``CLM_LOG_DIR`` environment variable is set, that directory is used
    verbatim (created if needed); otherwise the platform default is used.

    Returns:
        Path to the log directory (created if it doesn't exist)
        - ``CLM_LOG_DIR`` if set
        - Windows: %LOCALAPPDATA%/clm/Logs
        - macOS: ~/Library/Logs/clm
        - Linux: ~/.local/state/clm/log
    """
    override = os.environ.get(LOG_DIR_ENV_VAR)
    if override:
        log_dir = Path(override)
    else:
        log_dir = Path(platformdirs.user_log_dir("clm", appauthor=False))
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def get_main_log_path() -> Path:
    """Get the path to the main CLM log file.

    Returns:
        Path to clm.log in the system-appropriate log directory
    """
    return get_log_dir() / "clm.log"


def get_worker_log_dir() -> Path:
    """Get the directory for worker log files.

    Returns:
        Path to workers/ subdirectory in log directory
    """
    worker_log_dir = get_log_dir() / "workers"
    worker_log_dir.mkdir(parents=True, exist_ok=True)
    return worker_log_dir


def get_worker_log_path(worker_type: str, index: int) -> Path:
    """Get log file path for a specific worker.

    Args:
        worker_type: Type of worker (notebook, plantuml, drawio)
        index: Worker index

    Returns:
        Path to worker-specific log file
    """
    return get_worker_log_dir() / f"{worker_type}-{index}.log"
