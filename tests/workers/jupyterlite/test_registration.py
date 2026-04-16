"""Tests that the jupyterlite worker is wired into the dispatch infrastructure.

The Phase 2 contract is: when a course requests ``jupyterlite`` output,
the standard build path resolves ``service_name='jupyterlite-builder'``
to the ``jupyterlite`` queue and can start a worker process in direct
mode via ``clm.workers.jupyterlite``. These tests lock in those
registrations so a future refactor cannot silently disconnect them.
"""

from __future__ import annotations


def test_service_to_job_type_includes_jupyterlite() -> None:
    # The mapping is an in-method literal; read it back by invoking
    # execute_operation indirectly via a light-weight introspection.
    from clm.infrastructure.backends import sqlite_backend as mod

    source = (mod.__file__ or "").replace(".pyc", ".py")
    text = open(source, encoding="utf-8").read()
    assert '"jupyterlite-builder": "jupyterlite"' in text


def test_direct_worker_module_map_includes_jupyterlite() -> None:
    from clm.infrastructure.workers.worker_executor import DirectWorkerExecutor

    assert DirectWorkerExecutor.MODULE_MAP["jupyterlite"] == "clm.workers.jupyterlite"


def test_jupyterlite_worker_module_importable() -> None:
    import importlib

    mod = importlib.import_module("clm.workers.jupyterlite")
    assert mod is not None


def test_worker_config_accepts_jupyterlite() -> None:
    """get_worker_config('jupyterlite') must not raise the 'Unknown worker type' error."""
    from clm.infrastructure.config import WorkersManagementConfig

    config = WorkersManagementConfig()
    config.jupyterlite.count = 1
    wc = config.get_worker_config("jupyterlite")
    assert wc.worker_type == "jupyterlite"
    assert wc.count >= 1


def test_get_all_worker_configs_excludes_jupyterlite_by_default() -> None:
    """Opt-in contract: jupyterlite is not auto-started."""
    from clm.infrastructure.config import WorkersManagementConfig

    config = WorkersManagementConfig()
    # Default count is None -> jupyterlite should not appear.
    configs = config.get_all_worker_configs()
    types = [c.worker_type for c in configs]
    assert "jupyterlite" not in types
    assert set(types) == {"notebook", "plantuml", "drawio"}


def test_get_all_worker_configs_includes_jupyterlite_when_enabled() -> None:
    from clm.infrastructure.config import WorkersManagementConfig

    config = WorkersManagementConfig()
    config.jupyterlite.count = 1
    configs = config.get_all_worker_configs()
    types = [c.worker_type for c in configs]
    assert "jupyterlite" in types
