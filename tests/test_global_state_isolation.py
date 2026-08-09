"""Isolation guarantees for worker-global logging/config state (#694).

The 2026-07-26 nightly flaked because three tests on one xdist worker formed
a poisoning chain:

1. ``tests/infrastructure/test_config.py`` reloaded the process-global
   ``ClmConfig`` singleton under a monkeypatched ``CLM_LOGGING__LOG_LEVEL
   =ERROR``; monkeypatch reverted the env var, but the singleton kept the
   poisoned value.
2. A later in-process ``clm build`` (``tests/snapshot/test_build_cli.py``)
   resolved that poisoned value and applied it globally via
   ``setup_logging`` → ``logging.getLogger("clm").setLevel(ERROR)``, never
   restored.
3. Hundreds of tests later, ``test_cache_miss_falls_back_to_direct_execution``
   lost its ``cache miss`` WARNING to the ERROR gate and its ``caplog``
   assertion failed — while the unrelated traitlets logger kept capturing,
   which is exactly what the CI log showed.

The autouse ``_restore_worker_global_state`` fixture in ``tests/conftest.py``
is the class fix: it snapshots and restores the clm logger chain and the
config singleton around every test. These two tests pin the property: the
first pollutes on purpose, the second proves the pollution cannot cross the
test boundary. They must run in one worker in order, hence the shared
``xdist_group``.
"""

import logging

import pytest

from clm.cli.commands.shared import setup_logging
from clm.infrastructure.config import get_config


@pytest.mark.xdist_group("global_state_isolation")
class TestGlobalStateIsolation:
    def test_a_deliberately_pollutes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Reproduce the #694 poisoning: poisoned singleton + raised clm level."""
        monkeypatch.setenv("CLM_LOGGING__LOG_LEVEL", "ERROR")
        get_config(reload=True)
        logging.getLogger("clm").setLevel(logging.CRITICAL)
        # Both mutations took effect *within* this test...
        assert get_config().logging.log_level == "ERROR"
        assert logging.getLogger("clm").level == logging.CRITICAL

    def test_b_sees_clean_state(self) -> None:
        """...and neither survives the test boundary, thanks to the fixture."""
        assert logging.getLogger("clm").level != logging.CRITICAL, (
            "clm logger level leaked across a test boundary — the autouse "
            "_restore_worker_global_state fixture in tests/conftest.py is broken"
        )
        assert get_config().logging.log_level == "INFO", (
            "config singleton kept a monkeypatched value across a test "
            "boundary — the autouse _restore_worker_global_state fixture in "
            "tests/conftest.py is broken"
        )


class TestSetupLoggingLeavesForeignHandlersAlone:
    """``setup_logging`` must retire only the handlers it installed itself.

    It used to clear *and close* every handler on the root logger. pytest
    attaches its live-log and log-capture handlers there once for the whole run
    loop, so the first in-process ``clm build`` on an xdist worker tore them off
    — and closed them — for every test that followed. Beyond breaking pytest's
    own reporting, that accidental immunity is what turned the CliRunner capture
    bug into a nondeterministic 1-in-5 nightly flake instead of a reproducible
    failure (``docs/claude/design/test-flakiness-root-causes.md``).

    Closing a handler you did not open is also just wrong: it can tear down a
    file or socket an embedding application is still writing to.
    """

    def test_a_foreign_root_handler_survives(self) -> None:
        root = logging.getLogger()
        foreign = logging.NullHandler()
        root.addHandler(foreign)
        try:
            setup_logging("INFO")

            assert foreign in root.handlers, (
                "setup_logging removed a root handler it did not install — it "
                "must retire only its own (see _retire_previously_installed_handlers)"
            )
        finally:
            root.removeHandler(foreign)

    def test_a_foreign_root_handler_is_not_closed(self) -> None:
        closed: list[bool] = []

        class _RecordingHandler(logging.NullHandler):
            def close(self) -> None:
                closed.append(True)
                super().close()

        root = logging.getLogger()
        foreign = _RecordingHandler()
        root.addHandler(foreign)
        try:
            setup_logging("INFO")

            assert not closed, "setup_logging closed a handler it did not open"
        finally:
            root.removeHandler(foreign)

    def test_its_own_handlers_do_not_accumulate(self) -> None:
        """Repeat calls must still replace, not stack, clm's own file handlers."""
        root = logging.getLogger()

        setup_logging("INFO")
        after_first = len(root.handlers)
        setup_logging("INFO")
        after_second = len(root.handlers)

        assert after_second == after_first, (
            "setup_logging leaked a handler on a repeat call — the retire step "
            "is no longer matching the handlers it installs"
        )
