"""The console stays quiet unless you ask it not to.

``clm build`` printed every third-party ``DEBUG`` record on every run —
``docker.utils.config``, ``docker.auth``, ``urllib3.connectionpool`` — and
``--log-level=warning`` did not stop it. Two causes, and only together do they
produce the symptom:

* ``cli/main.py`` set logging up with ``logging.basicConfig(level=INFO)``,
  which installs a ``StreamHandler`` carrying **no level of its own**, so it
  emitted whatever the *logger* allowed;
* ``setup_logging`` then raised the root logger to ``DEBUG`` (so its file
  handler could capture everything) and never retired that handler — it only
  retires handlers it installed itself.

``--log-level`` did not help because it sets the level of the ``clm`` logger,
which is not where a ``docker`` record is filtered.

The contract these tests pin is the one ``--verbose-logging`` has always
advertised: *"Show log messages in console (by default logs go to file only)."*
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from clm.cli._logging_bootstrap import (
    BOOTSTRAP_HANDLER_ATTR,
    install_bootstrap_console_handler,
    retire_bootstrap_console_handlers,
)
from clm.cli.commands.shared import setup_logging


def _bootstrap_handlers(root: logging.Logger) -> list[logging.Handler]:
    return [h for h in root.handlers if getattr(h, BOOTSTRAP_HANDLER_ATTR, False)]


class TestTheBootstrapHandler:
    """What the console shows before a command configures logging."""

    def test_it_installs_at_warning(self) -> None:
        """Not ``INFO``, and — critically — not level-less.

        A handler at ``NOTSET`` defers to the logger, and the logger is opened
        up to ``DEBUG`` for the file sink. That combination is unquietable by
        any flag.
        """
        root = logging.getLogger()
        for handler in list(root.handlers):
            root.removeHandler(handler)
        try:
            handler = install_bootstrap_console_handler()
            assert handler is not None
            assert handler.level == logging.WARNING
            assert handler.level != logging.NOTSET
        finally:
            retire_bootstrap_console_handlers(root)

    def test_it_defers_to_an_existing_configuration(self) -> None:
        """``basicConfig``'s no-op-if-configured rule, kept.

        pytest, an embedding application or a prior call owns the root logger
        if anything is attached to it; adding a second console sink there
        would double every line.
        """
        root = logging.getLogger()
        foreign = logging.NullHandler()
        root.addHandler(foreign)
        try:
            assert install_bootstrap_console_handler() is None
            assert _bootstrap_handlers(root) == []
        finally:
            root.removeHandler(foreign)

    def test_the_logger_stays_permissive(self) -> None:
        """The *handler* is what goes quiet, not the logger.

        Setting the root logger to WARNING instead would discard records
        before any file handler could ever see them — and the file is where
        the detail is supposed to go.
        """
        root = logging.getLogger()
        saved_level = root.level
        for handler in list(root.handlers):
            root.removeHandler(handler)
        try:
            install_bootstrap_console_handler()
            assert root.level == logging.DEBUG
        finally:
            retire_bootstrap_console_handlers(root)
            root.setLevel(saved_level)


class TestSetupLoggingOwnsTheConsole:
    def test_it_retires_the_bootstrap_handler(self) -> None:
        """Otherwise two console sinks are attached, one of them level-less."""
        root = logging.getLogger()
        saved = list(root.handlers)
        for handler in saved:
            root.removeHandler(handler)
        try:
            install_bootstrap_console_handler()
            assert _bootstrap_handlers(root)

            setup_logging("WARNING")
            assert _bootstrap_handlers(root) == [], (
                "the bootstrap console handler survived setup_logging — it "
                "carries no level, so it re-prints every record the root "
                "logger passes, including third-party DEBUG"
            )
        finally:
            for handler in list(root.handlers):
                root.removeHandler(handler)
            root.handlers[:] = saved

    @pytest.mark.parametrize(
        ("log_level", "console_logging", "expected"),
        [
            # Quiet by default, whatever the level asked for.
            ("DEBUG", False, logging.WARNING),
            ("INFO", False, logging.WARNING),
            ("WARNING", False, logging.WARNING),
            # A *stricter* level still wins: asking for ERROR must not be
            # overridden into showing warnings.
            ("ERROR", False, logging.ERROR),
            ("CRITICAL", False, logging.CRITICAL),
            # --verbose-logging is what opens the console up.
            ("DEBUG", True, logging.DEBUG),
            ("INFO", True, logging.INFO),
            ("ERROR", True, logging.ERROR),
        ],
    )
    def test_the_console_threshold(
        self, log_level: str, console_logging: bool, expected: int
    ) -> None:
        from clm.cli.commands.shared import _console_handler_level

        assert _console_handler_level(logging.getLevelName(log_level), console_logging) == expected

    def test_a_console_handler_is_always_installed(self) -> None:
        """Even when quiet: a build that fails must still say so on screen."""
        from rich.logging import RichHandler

        root = logging.getLogger()
        saved = list(root.handlers)
        try:
            setup_logging("INFO", console_logging=False)
            console = [h for h in root.handlers if isinstance(h, RichHandler)]
            assert len(console) == 1
            assert console[0].level == logging.WARNING
        finally:
            root.handlers[:] = saved

    def test_the_file_handler_still_captures_everything(self) -> None:
        """The detail is not dropped, it is redirected — that is the whole deal."""
        from logging.handlers import RotatingFileHandler

        root = logging.getLogger()
        saved = list(root.handlers)
        try:
            setup_logging("WARNING", console_logging=False)
            files = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
            assert len(files) == 1
            assert files[0].level == logging.DEBUG
        finally:
            root.handlers[:] = saved


class TestTheReportedSymptom:
    """The bug as it was actually reported, through a real process.

    In-process assertions cannot see this: the console handler writes to the
    module-level Rich console bound to ``sys.stderr`` at import time, which is
    not what ``capsys`` or ``CliRunner`` capture. A subprocess can.
    """

    SCRIPT = textwrap.dedent(
        """
        import logging, sys
        import clm.cli.main                      # installs the bootstrap handler
        from clm.cli.commands.shared import setup_logging

        setup_logging(sys.argv[1], console_logging=sys.argv[2] == "verbose")

        # The exact loggers from the bug report.
        logging.getLogger("docker.utils.config").debug("TRYING-PATHS-SPAM")
        logging.getLogger("docker.auth").debug("AUTHS-SECTION-SPAM")
        logging.getLogger("urllib3.connectionpool").debug("CONNECTIONPOOL-SPAM")
        logging.getLogger("clm.build").info("CLM-PROGRESS-INFO")
        logging.getLogger("clm.build").warning("CLM-REAL-WARNING")
        logging.shutdown()
        """
    )

    def _run(self, tmp_path: Path, level: str, verbose: str = "quiet") -> str:
        proc = subprocess.run(
            [sys.executable, "-c", self.SCRIPT, level, verbose],
            capture_output=True,
            text=True,
            check=True,
            env={
                **os.environ,
                "CLM_LOG_DIR": str(tmp_path),
                "COLUMNS": "200",
                "NO_COLOR": "1",
                "TERM": "dumb",
            },
        )
        return proc.stdout + proc.stderr

    @pytest.mark.parametrize("level", ["WARNING", "INFO", "DEBUG"])
    def test_third_party_debug_never_reaches_the_console(self, level: str, tmp_path: Path) -> None:
        """The reported spam, at every level a user might pass.

        ``--log-level=warning`` was specifically reported as not helping,
        because it sets the ``clm`` logger's level and a ``docker`` record is
        not filtered there.
        """
        output = self._run(tmp_path, level)
        for marker in ("TRYING-PATHS-SPAM", "AUTHS-SECTION-SPAM", "CONNECTIONPOOL-SPAM"):
            assert marker not in output, f"{marker} leaked to the console at --log-level={level}"

    def test_clm_progress_info_stays_off_the_console(self, tmp_path: Path) -> None:
        """The default-INFO half of the complaint."""
        assert "CLM-PROGRESS-INFO" not in self._run(tmp_path, "INFO")

    def test_warnings_still_reach_the_console(self, tmp_path: Path) -> None:
        """Quiet is not silent. A gate that hides real problems is worse."""
        assert "CLM-REAL-WARNING" in self._run(tmp_path, "INFO")

    def test_verbose_logging_shows_everything(self, tmp_path: Path) -> None:
        """The escape hatch has to actually work, or people will not trust it."""
        output = self._run(tmp_path, "DEBUG", verbose="verbose")
        assert "TRYING-PATHS-SPAM" in output
        assert "CLM-PROGRESS-INFO" in output

    def test_the_detail_lands_in_the_rotating_log_file(self, tmp_path: Path) -> None:
        """Redirected, not discarded — including the third-party records."""
        self._run(tmp_path, "INFO")
        logs = list(tmp_path.rglob("clm.log"))
        assert logs, f"no clm.log written under {tmp_path}"
        text = logs[0].read_text(encoding="utf-8", errors="replace")
        assert "TRYING-PATHS-SPAM" in text
        assert "CONNECTIONPOOL-SPAM" in text
        assert "CLM-PROGRESS-INFO" in text
