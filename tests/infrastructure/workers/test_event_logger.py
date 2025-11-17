"""Tests for WorkerEventLogger."""

import gc
import sqlite3
import tempfile
from pathlib import Path

import pytest

from clx.infrastructure.database.schema import init_database
from clx.infrastructure.workers.event_logger import WorkerEventLogger, WorkerEventType


@pytest.fixture
def temp_db():
    """Create a temporary database for testing.

    This fixture properly cleans up SQLite connections and WAL files
    on Windows to prevent PermissionError during teardown.
    """
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = Path(f.name)

    # Initialize database
    conn = init_database(db_path)
    conn.close()

    yield db_path

    # Proper cleanup for Windows - close all connections and checkpoint WAL
    # Force garbage collection to release any lingering connections
    gc.collect()

    # Checkpoint WAL to consolidate files back into main database
    try:
        conn = sqlite3.connect(db_path)
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        conn.close()
    except Exception:
        pass

    # Delete database and WAL files
    try:
        db_path.unlink(missing_ok=True)
        # Also remove WAL and SHM files
        for suffix in ['-wal', '-shm']:
            wal_file = Path(str(db_path) + suffix)
            wal_file.unlink(missing_ok=True)
    except PermissionError:
        # On Windows, files might still be locked briefly
        pass


def test_event_logger_creation(temp_db):
    """Test creating event logger."""
    with WorkerEventLogger(temp_db, session_id="test-session") as logger:
        assert logger.db_path == temp_db
        assert logger.session_id == "test-session"


def test_log_worker_starting(temp_db):
    """Test logging worker starting event."""
    with WorkerEventLogger(temp_db) as logger:
        event_id = logger.log_worker_starting(
            worker_type="notebook",
            execution_mode="direct",
            index=0,
            config={"execution_mode": "direct"},
        )

        assert event_id > 0

        # Verify event was logged
        conn = logger.job_queue._get_conn()
        cursor = conn.execute(
            "SELECT event_type, worker_type, message FROM worker_events WHERE id = ?",
            (event_id,),
        )
        row = cursor.fetchone()

        assert row[0] == "worker_starting"
        assert row[1] == "notebook"
        assert "Starting direct worker notebook-0" in row[2]


def test_log_worker_registered(temp_db):
    """Test logging worker registered event."""
    with WorkerEventLogger(temp_db) as logger:
        event_id = logger.log_worker_registered(
            worker_type="plantuml",
            worker_id=1,
            executor_id="container-abc123",
            execution_mode="docker",
        )

        assert event_id > 0

        # Verify event was logged
        conn = logger.job_queue._get_conn()
        cursor = conn.execute(
            "SELECT event_type, worker_id, worker_type, execution_mode FROM worker_events WHERE id = ?",
            (event_id,),
        )
        row = cursor.fetchone()

        assert row[0] == "worker_registered"
        assert row[1] == 1
        assert row[2] == "plantuml"
        assert row[3] == "docker"


def test_log_worker_ready(temp_db):
    """Test logging worker ready event."""
    with WorkerEventLogger(temp_db) as logger:
        event_id = logger.log_worker_ready(
            worker_type="drawio", worker_id=2, execution_mode="direct"
        )

        assert event_id > 0


def test_log_worker_stopping(temp_db):
    """Test logging worker stopping event."""
    import json

    with WorkerEventLogger(temp_db) as logger:
        event_id = logger.log_worker_stopping(
            worker_type="notebook", worker_id=1, reason="user requested"
        )

        assert event_id > 0

        # Verify reason was recorded
        conn = logger.job_queue._get_conn()
        cursor = conn.execute(
            "SELECT metadata FROM worker_events WHERE id = ?", (event_id,)
        )
        row = cursor.fetchone()

        metadata = json.loads(row[0])
        assert metadata["reason"] == "user requested"


def test_log_worker_stopped(temp_db):
    """Test logging worker stopped event."""
    import json

    with WorkerEventLogger(temp_db) as logger:
        event_id = logger.log_worker_stopped(
            worker_type="notebook", worker_id=1, jobs_processed=10, uptime_seconds=300.5
        )

        assert event_id > 0

        # Verify metrics
        conn = logger.job_queue._get_conn()
        cursor = conn.execute(
            "SELECT metadata FROM worker_events WHERE id = ?", (event_id,)
        )
        row = cursor.fetchone()

        metadata = json.loads(row[0])
        assert metadata["jobs_processed"] == 10
        assert metadata["uptime_seconds"] == 300.5


def test_log_worker_failed(temp_db):
    """Test logging worker failed event."""
    import json

    with WorkerEventLogger(temp_db) as logger:
        event_id = logger.log_worker_failed(
            worker_type="plantuml",
            error="Connection timeout",
            worker_id=3,
            stack_trace="Traceback...",
        )

        assert event_id > 0

        # Verify error details
        conn = logger.job_queue._get_conn()
        cursor = conn.execute(
            "SELECT message, metadata FROM worker_events WHERE id = ?", (event_id,)
        )
        row = cursor.fetchone()

        assert "Connection timeout" in row[0]

        metadata = json.loads(row[1])
        assert metadata["error"] == "Connection timeout"
        assert metadata["stack_trace"] == "Traceback..."


def test_log_pool_events(temp_db):
    """Test logging pool lifecycle events."""
    from clx.infrastructure.workers.worker_executor import WorkerConfig

    with WorkerEventLogger(temp_db) as logger:
        # Pool starting
        configs = [
            WorkerConfig(
                worker_type="notebook", execution_mode="direct", count=2, image=None
            )
        ]

        event_id = logger.log_pool_starting(configs, total_workers=2)
        assert event_id > 0

        # Pool started
        event_id = logger.log_pool_started(worker_count=2, duration_seconds=5.2)
        assert event_id > 0

        # Pool stopping
        event_id = logger.log_pool_stopping()
        assert event_id > 0

        # Pool stopped
        event_id = logger.log_pool_stopped(workers_stopped=2, duration_seconds=2.1)
        assert event_id > 0

        # Verify all pool events
        conn = logger.job_queue._get_conn()
        cursor = conn.execute(
            "SELECT event_type FROM worker_events WHERE worker_type = 'all' ORDER BY id"
        )
        rows = cursor.fetchall()

        assert len(rows) == 4
        assert rows[0][0] == "pool_starting"
        assert rows[1][0] == "pool_started"
        assert rows[2][0] == "pool_stopping"
        assert rows[3][0] == "pool_stopped"


def test_session_id_tracking(temp_db):
    """Test session ID tracking."""
    with WorkerEventLogger(temp_db, session_id="session-1") as logger1:
        with WorkerEventLogger(temp_db, session_id="session-2") as logger2:
            # Log events with different sessions
            logger1.log_worker_starting(
                worker_type="notebook", execution_mode="direct", index=0, config={}
            )
            logger2.log_worker_starting(
                worker_type="plantuml", execution_mode="docker", index=0, config={}
            )

            # Query by session
            conn = logger1.job_queue._get_conn()

            cursor = conn.execute(
                "SELECT COUNT(*) FROM worker_events WHERE session_id = ?", ("session-1",)
            )
            assert cursor.fetchone()[0] == 1

            cursor = conn.execute(
                "SELECT COUNT(*) FROM worker_events WHERE session_id = ?", ("session-2",)
            )
            assert cursor.fetchone()[0] == 1
