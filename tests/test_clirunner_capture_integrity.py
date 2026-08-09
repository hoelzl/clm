"""``CliRunner`` output must survive log records emitted mid-invocation.

The nightly runs the whole suite with ``CLM_ENABLE_TEST_LOGGING=1``, which turns
on pytest's live logging. ``_LiveLoggingStreamHandler.emit`` suspends *and
resumes* global capture around every record, and resuming rebinds
``sys.stdout``/``sys.stderr`` to pytest's own capture objects — destroying the
Click isolation that ``result.output`` is read from. Every CLI write after the
first log record then lands in pytest's "Captured stdout call" section instead,
and the test's assertions fail against a truncated (often empty) string.

That is what failed the 2026-07-31 nightly (four ``test_cache_explain`` tests,
as ``ValueError: substring not found``) and the 2026-08-08 one (two
``test_release_cli`` tests). It is latent in every one of the ~90 ``CliRunner``
modules, and it went unseen on PRs because the unit tier — where all of those
modules live — is the one CI tier that sets neither ``CLM_ENABLE_TEST_LOGGING``
nor ``--log-cli-level``.

``tests/conftest.py::_install_clirunner_live_log_guard`` is the fix. This module
is the thing that fails if it stops working — including if a future Click
renames the private stream type the guard detects, or a future pytest changes
how the live handler reaches the terminal.

Full analysis: ``docs/claude/design/test-flakiness-root-causes.md``.
"""

from __future__ import annotations

import logging

import click
import pytest
from click.testing import CliRunner

from tests.conftest import _inside_click_runner

logger = logging.getLogger("clm.tests.capture_integrity")


@click.command()
def _echo_log_echo() -> None:
    """Write, log, write again — on both streams."""
    click.echo("before-stdout")
    click.echo("before-stderr", err=True)
    logger.warning("a record emitted in the middle of the command")
    click.echo("after-stdout")
    click.echo("after-stderr", err=True)


@click.command()
def _log_then_fail() -> None:
    """Log, then fail — the shape that made ``result.output`` come back empty."""
    logger.warning("a record emitted before the failure")
    raise click.ClickException("the error message")


@pytest.fixture
def _live_logging(request):
    """Attach a live-log handler to the root logger for one test.

    Reproduces what ``CLM_ENABLE_TEST_LOGGING=1`` does on a non-xdist run,
    without depending on how this particular suite was invoked — so the canary
    holds under ``-n auto`` (where conftest deliberately leaves live logging
    off) just as it does under ``-n0``.
    """
    from _pytest.logging import _LiveLoggingStreamHandler

    terminal_reporter = request.config.pluginmanager.get_plugin("terminalreporter")
    capture_manager = request.config.pluginmanager.get_plugin("capturemanager")
    if terminal_reporter is None or capture_manager is None:
        pytest.skip("live logging needs both the terminal reporter and the capture manager")

    handler = _LiveLoggingStreamHandler(terminal_reporter, capture_manager)
    handler.setLevel(logging.INFO)
    handler.set_when("call")
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield
    finally:
        root.removeHandler(handler)


def test_output_survives_a_log_record(_live_logging):
    """Writes on *both* sides of a log record reach ``result.output``."""
    result = CliRunner().invoke(_echo_log_echo, [])

    assert result.exit_code == 0, result.output
    # The "before" writes have never been at risk; assert them so a failure
    # distinguishes "capture broke at the record" from "capture never worked".
    assert "before-stdout" in result.output
    assert "before-stderr" in result.output
    # These are the ones the live-log handler used to steal.
    assert "after-stdout" in result.output, (
        "stdout written after a log record is missing from result.output — "
        "the live-log guard in tests/conftest.py is not working"
    )
    assert "after-stderr" in result.output, (
        "stderr written after a log record is missing from result.output — "
        "the live-log guard in tests/conftest.py is not working"
    )


def test_click_exception_after_a_log_record_still_reports(_live_logging):
    """``ClickException.show()`` writes to stderr, which the clobber also stole.

    This is the exact shape of the 2026-08-08 ``test_add_rejects_unknown_topic``
    failure, where ``result.output`` came back as ``''``.
    """
    result = CliRunner().invoke(_log_then_fail, [])

    assert result.exit_code != 0
    assert "the error message" in result.output


def test_streams_are_restored_after_the_invocation(_live_logging):
    """The guard must not leak: outside an invocation nothing is patched away."""
    assert not _inside_click_runner()
    CliRunner().invoke(_echo_log_echo, [])
    assert not _inside_click_runner()


def test_isolation_detection_recognises_a_live_runner():
    """The guard's trigger condition actually fires inside an invocation.

    If Click renames its private ``_NamedTextIOWrapper``, the guard would
    silently stop firing and every other test here would still pass on a run
    where live logging happens to be off. This one pins the detector itself.
    """
    seen: list[bool] = []

    @click.command()
    def probe() -> None:
        seen.append(_inside_click_runner())

    assert not _inside_click_runner()
    result = CliRunner().invoke(probe, [])

    assert result.exit_code == 0, result.output
    assert seen == [True], (
        "_inside_click_runner() did not recognise an active CliRunner isolation — "
        "Click's private stream type has probably been renamed; update "
        "_CLICK_RUNNER_STREAM_TYPES in tests/conftest.py"
    )
