"""The pool-size clamp firing *during managed-worker startup*.

Finding T8 of the 2026-07-24 adversarial review: the suite's autouse
env-neutralisers are each individually justified, but together they make the
production defaults a monoculture — and the *interaction* invisible.
``_neutralise_pool_size_cap`` in ``tests/conftest.py`` pins
``_compute_cpu_cap`` / ``_compute_mem_cap`` to 128 and clears
``CLM_MAX_WORKERS`` for every test, so no test outside
``test_pool_size_cap.py`` had ever observed the clamp actually engaging. That
module tests ``compute_pool_size_cap`` in isolation; nothing connected it to
the path that consumes it.

This module closes that gap: it re-enables the cap inside the test body (which
runs after the autouse fixture, so ``monkeypatch.setenv`` wins) and asserts the
clamp reaches ``WorkerLifecycleManager.start_managed_workers`` — i.e. that a
spec asking for more workers than the operator cap allows starts the capped
number, loudly.

Deliberately an *operator* cap (``CLM_MAX_WORKERS``) rather than the CPU/RAM
caps: those depend on the host, so asserting on them would make the test pass
on a 32-core dev box and fail on a 2-core CI runner. The neutraliser's
128-worker pinning stays in force here, which is exactly the regime the rest of
the suite runs under.
"""

import logging
from pathlib import Path

import pytest

from clm.infrastructure.database.schema import init_database
from clm.infrastructure.workers.config_loader import load_worker_config
from clm.infrastructure.workers.lifecycle_manager import WorkerLifecycleManager

REQUESTED_WORKERS = 3
OPERATOR_CAP = 1


def _worker_overrides(count: int) -> dict:
    """CLI overrides for a notebook-only, direct-mode managed pool."""
    return {
        "default_execution_mode": "direct",
        "notebook_count": count,
        "plantuml_count": 0,
        "drawio_count": 0,
        "auto_start": True,
        "auto_stop": True,
        "reuse_workers": False,
    }


def test_operator_cap_clamps_the_resolved_worker_config(monkeypatch, caplog):
    """``CLM_MAX_WORKERS`` clamps the count and says so at WARNING."""
    monkeypatch.setenv("CLM_MAX_WORKERS", str(OPERATOR_CAP))
    config = load_worker_config(_worker_overrides(REQUESTED_WORKERS))

    with caplog.at_level(logging.WARNING, logger="clm.infrastructure.config"):
        counts = {c.worker_type: c.count for c in config.get_all_worker_configs()}

    assert counts["notebook"] == OPERATOR_CAP
    assert any("capping to" in record.message for record in caplog.records), (
        f"the clamp must be visible on the operator's terminal; "
        f"logged: {[r.message for r in caplog.records]}"
    )


def test_no_clamp_without_an_operator_cap(monkeypatch):
    """Control: with the cap cleared, the requested count survives.

    Without this, the test above would also pass if the resolver had started
    returning 1 for some unrelated reason.
    """
    from clm.infrastructure.workers import pool_size_cap

    # This module relies on the autouse neutraliser pinning the host caps, so
    # the assertion below is about the *operator* cap and nothing else. Say so
    # explicitly: if the exemption match in ``_neutralise_pool_size_cap`` ever
    # swallows this module again, the failure names the cause instead of
    # reading as a mysterious off-by-one on a small runner.
    assert pool_size_cap._compute_cpu_cap() >= REQUESTED_WORKERS, (
        "the autouse pool-size neutraliser is not in force for this module — "
        "check the exemption match in tests/conftest.py::_neutralise_pool_size_cap"
    )

    monkeypatch.delenv("CLM_MAX_WORKERS", raising=False)
    config = load_worker_config(_worker_overrides(REQUESTED_WORKERS))

    counts = {c.worker_type: c.count for c in config.get_all_worker_configs()}
    assert counts["notebook"] == REQUESTED_WORKERS


@pytest.mark.integration
def test_operator_cap_clamps_managed_worker_startup(tmp_path, monkeypatch):
    """The clamp reaches real startup: 3 requested, 1 started.

    This is the interaction the unit tests of ``compute_pool_size_cap`` cannot
    see. It starts real Direct-mode worker subprocesses, so it is marked
    ``integration``.
    """
    monkeypatch.setenv("CLM_MAX_WORKERS", str(OPERATOR_CAP))

    db_path: Path = tmp_path / "jobs.db"
    init_database(db_path)
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()

    config = load_worker_config(_worker_overrides(REQUESTED_WORKERS))
    manager = WorkerLifecycleManager(
        config=config,
        db_path=db_path,
        workspace_path=workspace_path,
    )

    workers = []
    try:
        workers = manager.start_managed_workers()
        assert len(workers) == OPERATOR_CAP, (
            f"requested {REQUESTED_WORKERS} workers under a cap of {OPERATOR_CAP}, "
            f"but started {len(workers)}"
        )
        assert all(w.worker_type == "notebook" for w in workers)
    finally:
        manager.stop_managed_workers(workers)
