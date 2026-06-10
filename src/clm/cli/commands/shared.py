"""Shared utilities for CLI commands.

This module contains utilities used by multiple CLI command modules.
"""

import copy
import locale
import logging
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.logging import RichHandler

from clm.infrastructure.logging.log_paths import get_main_log_path as get_log_file_path
from clm.infrastructure.logging.resilient_handler import ResilientRotatingFileHandler

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
    """Configure logging for CLM.

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

    # File handler with rotation (10 MB max, keep 3 backups).
    # ResilientRotatingFileHandler tolerates the Windows "file in use"
    # rollover race that otherwise floods the console with WinError 32
    # tracebacks when worker subprocesses share the log file (issue #143).
    file_handler = ResilientRotatingFileHandler(
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
    logging.getLogger("clm").setLevel(log_level)


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


def has_deck_scope(only: str | None, exclude: tuple[str, ...], shipping_only: bool) -> bool:
    """Whether any deck-scoping option is active (gap #4)."""
    return bool(only) or bool(exclude) or shipping_only


def resolve_scoped_files(
    path: Path,
    *,
    only: str | None,
    exclude: tuple[str, ...],
    shipping_only: bool,
    specs_dir: Path | None,
    data_dir: Path | None,
) -> list[Path]:
    """Resolve a directory *path* to the scoped subset of slide files (gap #4).

    Applies ``--only`` / ``--exclude`` / ``--shipping-only`` to the recursive
    slide-file walk. Used by ``clm slides assign-ids`` and ``clm slides
    normalize`` so both scope decks identically. Raises ``click`` errors on
    misuse (non-directory path, unlocatable specs).
    """
    from clm.core.topic_resolver import find_slide_files_recursive
    from clm.slides.deck_scope import (
        course_root_for_path,
        filter_decks,
        resolve_shipping_set,
    )

    if not path.is_dir():
        raise click.UsageError(
            "--only / --exclude / --shipping-only apply to a directory, not a single file."
        )

    files = list(find_slide_files_recursive(path))

    shipping: set[Path] | None = None
    if shipping_only:
        course_root = data_dir or course_root_for_path(path)
        if course_root is None:
            raise click.ClickException(
                "Could not locate the course root (no 'slides/' ancestor) for "
                "--shipping-only. Pass --data-dir or --specs-dir explicitly."
            )
        resolved_specs_dir = specs_dir or (course_root / "course-specs")
        if not resolved_specs_dir.is_dir():
            raise click.ClickException(
                f"Specs directory not found: {resolved_specs_dir}. Pass --specs-dir explicitly."
            )
        slides_dir = (data_dir / "slides") if data_dir else (course_root / "slides")
        shipping = resolve_shipping_set(resolved_specs_dir, slides_dir)
        if not shipping:
            raise click.ClickException(
                f"No decks reachable from specs in {resolved_specs_dir} "
                "(no *.xml specs, or none resolve)."
            )

    return filter_decks(files, only=only, exclude=exclude, shipping=shipping)


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


def hidden_alias(cmd: click.Command, name: str) -> click.Command:
    """A hidden second name for ``cmd``.

    The alias stays invocable but is not listed in ``--help``, so each
    command shows up exactly once. The shallow copy shares params and
    callback with the canonical command.
    """
    alias = copy.copy(cmd)
    alias.name = name
    alias.hidden = True
    return alias
