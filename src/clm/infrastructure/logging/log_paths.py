"""Centralized log path management for CLX.

This module provides consistent log file paths across all CLX components,
including the main CLI and worker processes.
"""

from pathlib import Path

import platformdirs


def get_log_dir() -> Path:
    """Get the system-appropriate log directory for CLX.

    Returns:
        Path to the log directory (created if it doesn't exist)
        - Windows: %LOCALAPPDATA%/clx/Logs
        - macOS: ~/Library/Logs/clx
        - Linux: ~/.local/state/clx/log
    """
    log_dir = Path(platformdirs.user_log_dir("clx", appauthor=False))
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def get_main_log_path() -> Path:
    """Get the path to the main CLX log file.

    Returns:
        Path to clx.log in the system-appropriate log directory
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
