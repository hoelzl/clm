"""Docker workers that never activate: liveness, reaping, and diagnostics (issue #1078).

The build waits longer for 'created' Docker workers than for Direct ones,
because Docker Desktop's container startup jitter is unbounded. These tests
pin the pieces that keep that longer wait from hiding a real crash:

- ``DockerWorkerExecutor.has_exited`` answers True only on proof;
- the pool's health monitor marks a 'created' Docker worker whose container
  exited dead (so the build's wait ends early) and logs its output;
- ``stop_pools`` copies the output of Docker workers that never served the
  build into clm.log before their containers are removed.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clm.infrastructure.database.job_queue import JobQueue
from clm.infrastructure.database.schema import init_database
from clm.infrastructure.workers.pool_manager import WorkerPoolManager
from clm.infrastructure.workers.worker_executor import DockerWorkerExecutor, WorkerConfig

POOL_LOGGER = "clm.infrastructure.workers.pool_manager"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "jobs.db"
    init_database(path)
    return path


@pytest.fixture
def workspace_path(tmp_path: Path) -> Path:
    path = tmp_path / "workspace"
    path.mkdir()
    return path


@pytest.fixture
def docker_config() -> WorkerConfig:
    return WorkerConfig(
        worker_type="notebook",
        count=1,
        execution_mode="docker",
        image="docker.io/mhoelzl/clm-notebook-processor:latest",
    )


@pytest.fixture
def manager(db_path, workspace_path, docker_config):
    with patch("docker.from_env"):
        pool = WorkerPoolManager(
            db_path=db_path,
            workspace_path=workspace_path,
            worker_configs=[docker_config],
            session_id="session-A",
        )
    yield pool
    pool.running = False
    pool.job_queue.close()


def _seed_worker(db_path: Path, session_id: str, container_id: str, status: str = "created"):
    jq = JobQueue(db_path)
    try:
        cur = jq._get_conn().execute(
            """
            INSERT INTO workers (worker_type, container_id, status, session_id)
            VALUES ('notebook', ?, ?, ?)
            """,
            (container_id, status, session_id),
        )
        assert cur.lastrowid is not None
        return cur.lastrowid
    finally:
        jq.close()


def _status(db_path: Path, worker_id: int) -> str | None:
    jq = JobQueue(db_path)
    try:
        row = (
            jq._get_conn()
            .execute("SELECT status FROM workers WHERE id = ?", (worker_id,))
            .fetchone()
        )
        return row[0] if row else None
    finally:
        jq.close()


def _run_monitor_until(pool: WorkerPoolManager, condition, timeout: float = 10.0) -> None:
    pool.running = True
    thread = threading.Thread(target=pool._monitor_health, args=(0.05,), daemon=True)
    thread.start()
    try:
        deadline = time.time() + timeout
        while time.time() < deadline and not condition():
            time.sleep(0.05)
    finally:
        pool.running = False
        pool._monitor_stop_event.set()
        thread.join(timeout=5)


class TestDockerHasExited:
    """Uncertainty must read as "not exited": a false positive would kill a
    healthy-but-slow pool, the very failure #1078 is about."""

    def _executor(self, db_path, workspace_path):
        return DockerWorkerExecutor(
            docker_client=MagicMock(), db_path=db_path, workspace_path=workspace_path
        )

    def test_untracked_container_is_not_exited(self, db_path, workspace_path):
        executor = self._executor(db_path, workspace_path)
        assert executor.has_exited("never-started-here") is False
        executor.docker_client.containers.get.assert_not_called()

    @pytest.mark.parametrize("status", ["exited", "dead"])
    def test_exited_container_has_exited(self, db_path, workspace_path, status):
        executor = self._executor(db_path, workspace_path)
        container = MagicMock()
        container.status = status
        executor.containers["c1"] = container
        assert executor.has_exited("c1") is True

    @pytest.mark.parametrize("status", ["running", "created", "restarting"])
    def test_live_or_starting_container_has_not_exited(self, db_path, workspace_path, status):
        executor = self._executor(db_path, workspace_path)
        container = MagicMock()
        container.status = status
        executor.containers["c1"] = container
        assert executor.has_exited("c1") is False

    def test_removed_container_has_exited(self, db_path, workspace_path):
        import docker.errors

        executor = self._executor(db_path, workspace_path)
        container = MagicMock()
        container.reload.side_effect = docker.errors.NotFound("gone")
        executor.containers["c1"] = container
        assert executor.has_exited("c1") is True

    def test_docker_api_error_is_not_exited(self, db_path, workspace_path):
        executor = self._executor(db_path, workspace_path)
        container = MagicMock()
        container.reload.side_effect = RuntimeError("Docker API timed out")
        executor.containers["c1"] = container
        assert executor.has_exited("c1") is False


def test_monitor_marks_exited_created_docker_worker_dead(manager, db_path, caplog):
    """A Docker worker whose container exited before activating turns 'dead'
    (ending the build's long Docker activation wait early), and its container
    output reaches clm.log. A still-booting container and another session's
    row stay 'created'."""
    docker_executor = MagicMock()
    docker_executor.has_exited.side_effect = lambda cid: cid in ("crashed", "foreign")
    docker_executor.get_container_logs.return_value = "ImportError: no module named nbformat"
    manager.executors["docker"] = docker_executor

    crashed = _seed_worker(db_path, "session-A", "crashed")
    booting = _seed_worker(db_path, "session-A", "booting")
    foreign = _seed_worker(db_path, "session-B", "foreign")

    with caplog.at_level(logging.ERROR, logger=POOL_LOGGER):
        _run_monitor_until(manager, lambda: _status(db_path, crashed) == "dead")

    assert _status(db_path, crashed) == "dead"
    assert _status(db_path, booting) == "created"
    assert _status(db_path, foreign) == "created"
    assert any("ImportError: no module named nbformat" in r.message for r in caplog.records)


def test_monitor_spares_created_worker_on_uncertain_liveness(manager, db_path):
    """Only a strict ``True`` from ``has_exited`` condemns a 'created' worker,
    never a merely truthy/unknown answer."""
    docker_executor = MagicMock()  # has_exited returns a (truthy) MagicMock
    manager.executors["docker"] = docker_executor
    worker = _seed_worker(db_path, "session-A", "booting")

    _run_monitor_until(manager, lambda: docker_executor.has_exited.call_count >= 2)

    assert docker_executor.has_exited.call_count >= 2
    assert _status(db_path, worker) == "created"


def test_stop_pools_logs_output_of_docker_worker_that_never_served(
    manager, db_path, docker_config, caplog
):
    """Stopping removes the container and with it the only record of why a
    Docker worker never activated, so stop_pools copies the output tail of
    every Docker worker that is not idle/busy into clm.log first. Healthy
    workers' output stays out."""
    manager._stop_worker_api_server = MagicMock()  # type: ignore[method-assign]
    executor = MagicMock()
    executor.stop_worker.return_value = True
    executor.get_container_logs.side_effect = lambda cid, tail=40: f"output of {cid}"

    stuck = _seed_worker(db_path, "session-A", "stuck-container", status="dead")
    healthy = _seed_worker(db_path, "session-A", "healthy-container", status="idle")
    manager.workers["notebook"] = [
        {
            "executor": executor,
            "executor_id": "stuck-container",
            "db_worker_id": stuck,
            "config": docker_config,
        },
        {
            "executor": executor,
            "executor_id": "healthy-container",
            "db_worker_id": healthy,
            "config": docker_config,
        },
    ]

    with caplog.at_level(logging.WARNING, logger=POOL_LOGGER):
        manager.stop_pools()

    messages = "\n".join(r.message for r in caplog.records)
    assert "output of stuck-container" in messages
    assert "status 'dead'" in messages
    assert "output of healthy-container" not in messages
