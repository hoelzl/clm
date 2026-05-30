"""Tests for the Windows-resilient rotating file handler (issue #143, sub-bug B)."""

import logging
from unittest.mock import patch

import pytest

from clm.infrastructure.logging.resilient_handler import ResilientRotatingFileHandler


@pytest.fixture
def log_file(tmp_path):
    return tmp_path / "clm.log"


def test_rollover_permission_error_is_swallowed(log_file):
    """A locked-file rollover (Windows WinError 32) must not raise.

    The stock RotatingFileHandler funnels the PermissionError through
    handleError, which prints a traceback per record. The resilient handler
    swallows it and keeps writing to the current file.
    """
    handler = ResilientRotatingFileHandler(log_file, maxBytes=1, backupCount=1, encoding="utf-8")
    try:
        # Force the parent's doRollover to raise the Windows lock error.
        with patch(
            "logging.handlers.RotatingFileHandler.doRollover",
            side_effect=PermissionError(32, "in use by another process"),
        ):
            # Must not raise.
            handler.doRollover()

        # The stream is reopened so subsequent writes still work.
        record = logging.LogRecord(
            name="t",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="after-failed-rollover",
            args=(),
            exc_info=None,
        )
        handler.emit(record)
        handler.flush()
    finally:
        handler.close()

    assert "after-failed-rollover" in log_file.read_text(encoding="utf-8")


def test_emit_does_not_raise_when_rollover_locked(log_file):
    """End-to-end: emitting records that trigger a locked rollover never
    propagates an error out of the logging machinery."""
    handler = ResilientRotatingFileHandler(log_file, maxBytes=1, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    raised: list[BaseException] = []
    handler.handleError = lambda record: raised.append(  # type: ignore[method-assign]
        record  # pragma: no cover - should not be called
    )
    try:
        with patch(
            "logging.handlers.RotatingFileHandler.doRollover",
            side_effect=PermissionError(32, "in use by another process"),
        ):
            for i in range(5):
                record = logging.LogRecord(
                    name="t",
                    level=logging.INFO,
                    pathname=__file__,
                    lineno=1,
                    msg=f"line-{i}",
                    args=(),
                    exc_info=None,
                )
                handler.emit(record)
    finally:
        handler.close()

    assert raised == [], "handleError should never fire on a locked rollover"


def test_successful_rollover_still_rotates(log_file):
    """When the file is not locked, rollover behaves like the stock handler."""
    handler = ResilientRotatingFileHandler(log_file, maxBytes=1, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    try:
        for i in range(3):
            record = logging.LogRecord(
                name="t",
                level=logging.INFO,
                pathname=__file__,
                lineno=1,
                msg=f"x{i}",
                args=(),
                exc_info=None,
            )
            handler.emit(record)
    finally:
        handler.close()

    # A backup file should have been created by the real rollover.
    backup = log_file.with_name(log_file.name + ".1")
    assert backup.exists()
