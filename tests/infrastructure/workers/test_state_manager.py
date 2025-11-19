"""Tests for WorkerStateManager."""

import json
import tempfile
from pathlib import Path

import pytest

from clx.infrastructure.workers.state_manager import (
    WorkerInfo,
    WorkerState,
    WorkerStateManager,
)


def test_worker_info_creation():
    """Test WorkerInfo creation."""
    info = WorkerInfo(
        worker_type="notebook",
        execution_mode="direct",
        executor_id="direct-worker-123",
        db_worker_id=1,
        started_at="2025-01-15T10:00:00",
        config={"execution_mode": "direct"},
    )

    assert info.worker_type == "notebook"
    assert info.execution_mode == "direct"
    assert info.executor_id == "direct-worker-123"
    assert info.db_worker_id == 1


def test_worker_state_creation():
    """Test WorkerState creation."""
    workers = [
        WorkerInfo(
            worker_type="notebook",
            execution_mode="direct",
            executor_id="direct-worker-1",
            db_worker_id=1,
            started_at="2025-01-15T10:00:00",
            config={},
        )
    ]

    state = WorkerState(db_path="/tmp/jobs.db", workers=workers, metadata={})

    assert state.version == "1.0"
    assert state.db_path == "/tmp/jobs.db"
    assert len(state.workers) == 1


def test_state_manager_save_and_load():
    """Test saving and loading worker state."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        state_file = tmpdir / "worker-state.json"
        db_path = tmpdir / "jobs.db"

        manager = WorkerStateManager(state_file)

        # Create workers
        workers = [
            WorkerInfo(
                worker_type="notebook",
                execution_mode="direct",
                executor_id="direct-worker-1",
                db_worker_id=1,
                started_at="2025-01-15T10:00:00",
                config={"execution_mode": "direct"},
            ),
            WorkerInfo(
                worker_type="plantuml",
                execution_mode="docker",
                executor_id="container-abc123",
                db_worker_id=2,
                started_at="2025-01-15T10:00:05",
                config={"execution_mode": "docker", "image": "plantuml:latest"},
            ),
        ]

        # Save state
        manager.save_worker_state(workers=workers, db_path=db_path, test_metadata="test_value")

        # Verify file exists
        assert state_file.exists()

        # Verify file permissions (Unix-like systems only)
        import sys

        if sys.platform != "win32" and hasattr(state_file, "stat"):
            mode = state_file.stat().st_mode & 0o777
            # Should be readable/writable by owner only
            assert mode == 0o600

        # Load state
        loaded_state = manager.load_worker_state()

        assert loaded_state is not None
        assert loaded_state.db_path == str(db_path.absolute())
        assert len(loaded_state.workers) == 2
        assert loaded_state.workers[0].worker_type == "notebook"
        assert loaded_state.workers[1].worker_type == "plantuml"
        assert "test_metadata" in loaded_state.metadata


def test_state_manager_load_nonexistent():
    """Test loading when state file doesn't exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        state_file = tmpdir / "nonexistent.json"

        manager = WorkerStateManager(state_file)
        state = manager.load_worker_state()

        assert state is None


def test_state_manager_clear():
    """Test clearing worker state."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        state_file = tmpdir / "worker-state.json"
        db_path = tmpdir / "jobs.db"

        manager = WorkerStateManager(state_file)

        # Save state
        workers = [
            WorkerInfo(
                worker_type="notebook",
                execution_mode="direct",
                executor_id="direct-worker-1",
                db_worker_id=1,
                started_at="2025-01-15T10:00:00",
                config={},
            )
        ]
        manager.save_worker_state(workers=workers, db_path=db_path)

        assert state_file.exists()

        # Clear state
        manager.clear_worker_state()

        assert not state_file.exists()


def test_state_manager_validate_state():
    """Test state validation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        state_file = tmpdir / "worker-state.json"
        db_path = tmpdir / "jobs.db"

        manager = WorkerStateManager(state_file)

        # Save state
        workers = [
            WorkerInfo(
                worker_type="notebook",
                execution_mode="direct",
                executor_id="direct-worker-1",
                db_worker_id=1,
                started_at="2025-01-15T10:00:00",
                config={},
            )
        ]
        manager.save_worker_state(workers=workers, db_path=db_path)

        # Validate with correct path
        assert manager.validate_state(db_path)

        # Validate with wrong path
        wrong_path = tmpdir / "wrong.db"
        assert not manager.validate_state(wrong_path)


def test_state_manager_default_location():
    """Test default state file location."""
    manager = WorkerStateManager()

    assert manager.state_file == Path(".clx") / "worker-state.json"
