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

from clm.cli._logging_bootstrap import retire_bootstrap_console_handlers
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

# Handlers this module has installed on the root logger, so a repeat call can
# retire its own predecessors without touching anyone else's. See the note in
# ``setup_logging``.
_installed_handlers: list[logging.Handler] = []


def _retire_previously_installed_handlers(root_logger: logging.Logger) -> None:
    """Remove and close only the handlers a previous ``setup_logging`` installed.

    This used to clear the root logger wholesale::

        for handler in root_logger.handlers[:]:
            handler.close()
            root_logger.removeHandler(handler)

    which is hostile to anything that embeds clm in a process it does not own:
    the MCP and web servers, an application importing ``clm``, and — the way it
    surfaced — the test suite. pytest attaches its live-log and log-capture
    handlers to the root logger for the *whole run loop*, so the first
    in-process ``clm build`` on an xdist worker removed **and closed** them for
    every test that followed on that worker. That silently disabled
    ``caplog``-adjacent reporting and, worse, made an unrelated capture bug
    (``docs/claude/design/test-flakiness-root-causes.md``) fire on a random
    subset of tests each run instead of deterministically — which is why the
    nightly looked like a 1-in-5 flake for weeks.

    Closing a handler you did not open is never right; it can tear down a file
    or socket another component is still writing to. Track our own and leave
    the rest alone.
    """
    for handler in _installed_handlers:
        if handler in root_logger.handlers:
            root_logger.removeHandler(handler)
        handler.close()
    _installed_handlers.clear()


def _console_handler_level(log_level: int, console_logging: bool) -> int:
    """Threshold for the console sink.

    ``--verbose-logging`` has always documented itself as "show log messages in
    console (by default logs go to file only)", and this is the line that makes
    that true. Without it the console showed everything the *logger* allowed,
    which after the root logger is opened up to ``DEBUG`` for the file handler
    means every third-party ``DEBUG`` record — ``docker.utils.config`` and
    ``urllib3.connectionpool`` on any Docker-mode build.

    A stricter ``--log-level`` still wins: asking for ``ERROR`` should not be
    overridden into showing warnings. Only the *permissive* direction is
    clamped, and only when console logging was not asked for.
    """
    if console_logging:
        return log_level
    return max(log_level, logging.WARNING)


def setup_logging(log_level_name: str, console_logging: bool = False):
    """Configure logging for CLM.

    By default, logs go to a rotating file in the system-appropriate log
    directory (``CLM_LOG_DIR`` overrides it) and the console shows warnings and
    errors only. ``console_logging`` echoes everything at *log_level* to the
    console as well.

    Only handlers installed by a previous ``setup_logging`` call — and the
    bootstrap console handler from :mod:`clm.cli._logging_bootstrap`, which
    this supersedes — are retired; handlers owned by an embedding application
    (or by pytest) are left in place.

    Args:
        log_level_name: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        console_logging: If True, echo log messages at *log_level* to the console
    """
    log_level = logging.getLevelName(log_level_name.upper())
    log_file = get_log_file_path()

    root_logger = logging.getLogger()
    _retire_previously_installed_handlers(root_logger)
    # This function is the authority on console output from here on, so the
    # pre-command handler goes. Leaving it attached is what made the console
    # unquietable: it carries no level of its own, so it printed every record
    # the root logger passed — and the root logger is opened to DEBUG two
    # dozen lines below so the *file* can capture everything.
    retire_bootstrap_console_handlers(root_logger)

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
    _installed_handlers.append(file_handler)

    # Console handler, always installed: without one, a build that hits a
    # real problem would say nothing at all on screen. What changes with
    # ``console_logging`` is its *threshold*, not its existence.
    console_handler = RichHandler(
        console=cli_console,
        rich_tracebacks=True,
        show_path=False,
    )
    console_handler.setLevel(_console_handler_level(log_level, console_logging))
    root_logger.addHandler(console_handler)
    _installed_handlers.append(console_handler)

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
