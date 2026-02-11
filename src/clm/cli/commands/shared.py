"""Shared utilities for CLI commands.

This module contains utilities used by multiple CLI command modules.
"""

import locale
import logging
import sys
from logging.handlers import RotatingFileHandler

from rich.console import Console
from rich.logging import RichHandler

from clm.infrastructure.logging.log_paths import get_main_log_path as get_log_file_path

# Shared console for CLI output - uses stderr to avoid mixing with JSON output
cli_console = Console(file=sys.stderr)

# Set locale
try:
    locale.setlocale(locale.LC_ALL, "en_US.UTF-8")
except locale.Error:
    try:
        locale.setlocale(locale.LC_ALL, "C.UTF-8")
    except locale.Error:
        pass

LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def setup_logging(log_level_name: str, console_logging: bool = False):
    """Configure logging for CLX.

    By default, logs go to a file in the system-appropriate log directory.
    Console logging can be enabled for debugging.

    Args:
        log_level_name: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        console_logging: If True, also log to console via Rich
    """
    log_level = logging.getLevelName(log_level_name.upper())
    log_file = get_log_file_path()

    # Clear any existing handlers and close them properly
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        handler.close()
        root_logger.removeHandler(handler)

    # File handler with rotation (10 MB max, keep 3 backups)
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)  # Capture all levels in file
    file_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    # Console handler (only if requested)
    if console_logging:
        console_handler = RichHandler(
            console=cli_console,
            rich_tracebacks=True,
            show_path=False,
        )
        console_handler.setLevel(log_level)
        root_logger.addHandler(console_handler)

    # Set levels
    root_logger.setLevel(logging.DEBUG)  # Let handlers filter
    logging.getLogger("clx").setLevel(log_level)


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the given name.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Configured logger instance
    """
    return logging.getLogger(name)


def print_separator(section: str = "", char: str = "="):
    """Print a separator line using Rich console."""
    if section:
        cli_console.rule(f"[bold]{section}[/bold]", characters=char)
    else:
        cli_console.rule(characters=char)


def is_ci_environment() -> bool:
    """Detect if running in a CI/CD environment.

    Checks for common CI environment variables:
    - CI=true (generic)
    - GITHUB_ACTIONS=true (GitHub Actions)
    - GITLAB_CI=true (GitLab CI)
    - JENKINS_HOME (Jenkins)
    - CIRCLECI=true (CircleCI)
    - TRAVIS=true (Travis CI)
    - BUILDKITE=true (Buildkite)
    - DRONE=true (Drone CI)

    Returns:
        True if running in a CI environment, False otherwise
    """
    import os

    ci_indicators = [
        "CI",
        "GITHUB_ACTIONS",
        "GITLAB_CI",
        "JENKINS_HOME",
        "CIRCLECI",
        "TRAVIS",
        "BUILDKITE",
        "DRONE",
    ]

    return any(os.getenv(indicator) for indicator in ci_indicators)
