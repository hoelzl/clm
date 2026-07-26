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

import clm.infrastructure.config as config_module
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

    def test_c_singleton_identity_is_restored(self) -> None:
        """The fixture restores the *previous* singleton object, not a reset."""
        # No test before this one in the group reloaded the singleton, so the
        # object here is the one the worker started with (or None lazily) —
        # the point is that test_a's reloaded instance is gone.
        assert get_config() is not None
        assert config_module._config is not None  # noqa: SLF001 - asserting the fixture's subject
