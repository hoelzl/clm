"""Tests for StatusCollector class."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from clm.cli.status.collector import StatusCollector
from clm.cli.status.models import SystemHealth
from clm.infrastructure.database.job_queue import JobQueue
from clm.infrastructure.database.schema import init_database


class TestStatusCollector:
    """Test StatusCollector with real database."""

    @pytest.fixture
    def db_path(self, tmp_path):
        """Create temporary database."""
        db_path = tmp_path / "test_status.db"
        init_database(db_path)
        return db_path

    @pytest.fixture
    def job_queue(self, db_path):
        """Create JobQueue instance."""
        with JobQueue(db_path) as queue:
            yield queue

    def test_collect_database_not_found(self, tmp_path):
        """Test collecting status when database doesn't exist."""
        db_path = tmp_path / "nonexistent.db"
        with StatusCollector(db_path=db_path) as collector:
            status = collector.collect()

            assert status.health == SystemHealth.ERROR
            assert not status.database.accessible
            assert not status.database.exists
            assert "not found" in status.database.error_message.lower()

    def test_collect_empty_database(self, db_path):
        """Test collecting status from empty database."""
        with StatusCollector(db_path=db_path) as collector:
            status = collector.collect()

            # Should be error since no workers are registered
            assert status.health == SystemHealth.ERROR
            assert status.database.accessible
            assert status.database.exists
            assert "No workers registered" in status.errors

    def test_collect_with_idle_workers(self, db_path, job_queue):
        """Test collecting status with idle workers."""
        # Register workers
        conn = job_queue._get_conn()
        conn.execute(
            """
            INSERT INTO workers (worker_type, container_id, status, execution_mode)
            VALUES ('notebook', 'nb-worker-1', 'idle', 'direct')
            """
        )
        conn.execute(
            """
            INSERT INTO workers (worker_type, container_id, status, execution_mode)
            VALUES ('plantuml', 'pu-worker-1', 'idle', 'docker')
            """
        )
        conn.commit()

        with StatusCollector(db_path=db_path) as collector:
            status = collector.collect()

            # Should be healthy
            assert status.health == SystemHealth.HEALTHY
            assert status.workers["notebook"].total == 1
            assert status.workers["notebook"].idle == 1
            assert status.workers["notebook"].execution_mode == "direct"
            assert status.workers["plantuml"].total == 1
            assert status.workers["plantuml"].execution_mode == "docker"

    def test_collect_with_busy_workers(self, db_path, job_queue):
        """Test collecting status with busy workers processing jobs."""
        # Register worker
        conn = job_queue._get_conn()
        cursor = conn.execute(
            """
            INSERT INTO workers (worker_type, container_id, status, execution_mode)
            VALUES ('notebook', 'nb-worker-1', 'busy', 'direct')
            """
        )
        worker_id = cursor.lastrowid

        # Create a job
        job_id = job_queue.add_job(
            job_type="notebook",
            input_file="/path/to/input.ipynb",
            output_file="/path/to/output.html",
            content_hash="abc123",
            payload={"test": "data"},
        )

        # Mark job as processing
        conn.execute(
            """
            UPDATE jobs
            SET status = 'processing', worker_id = ?, started_at = ?
            WHERE id = ?
            """,
            (worker_id, datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"), job_id),
        )
        conn.commit()

        with StatusCollector(db_path=db_path) as collector:
            status = collector.collect()

            assert status.health == SystemHealth.HEALTHY
            assert status.workers["notebook"].busy == 1
            assert len(status.workers["notebook"].busy_workers) == 1
            assert (
                status.workers["notebook"].busy_workers[0].document_path == "/path/to/input.ipynb"
            )

    def test_collect_includes_heartbeat_fields_when_present(self, db_path, job_queue):
        """Heartbeat join surfaces per-cell info on BusyWorkerInfo.

        Regression guard for the v8 schema + worker_heartbeats join. When
        a notebook worker has published a heartbeat row, the collector
        should populate ``current_cell`` / ``total_cells`` /
        ``last_output_excerpt`` on the matching ``BusyWorkerInfo``.
        Workers without a heartbeat row (the other branch in this test)
        get ``None`` and the SQL join doesn't drop their rows.
        """
        conn = job_queue._get_conn()
        # Worker A — will get a heartbeat row.
        cursor_a = conn.execute(
            """
            INSERT INTO workers (worker_type, container_id, status, execution_mode)
            VALUES ('notebook', 'nb-A', 'busy', 'direct')
            """
        )
        wid_a = cursor_a.lastrowid
        # Worker B — no heartbeat, but still busy.
        cursor_b = conn.execute(
            """
            INSERT INTO workers (worker_type, container_id, status, execution_mode)
            VALUES ('notebook', 'nb-B', 'busy', 'direct')
            """
        )
        wid_b = cursor_b.lastrowid

        job_a = job_queue.add_job(
            job_type="notebook",
            input_file="/p/a.ipynb",
            output_file="/p/a.html",
            content_hash="ha",
            payload={"format": "notebook"},
        )
        job_b = job_queue.add_job(
            job_type="notebook",
            input_file="/p/b.ipynb",
            output_file="/p/b.html",
            content_hash="hb",
            payload={"format": "notebook"},
        )

        now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "UPDATE jobs SET status='processing', worker_id=?, started_at=? WHERE id=?",
            (wid_a, now_str, job_a),
        )
        conn.execute(
            "UPDATE jobs SET status='processing', worker_id=?, started_at=? WHERE id=?",
            (wid_b, now_str, job_b),
        )
        # Heartbeat for worker A only.
        conn.execute(
            """
            INSERT INTO worker_heartbeats (
                worker_id, job_id, current_cell_index, total_cells,
                current_cell_started_at, last_output_excerpt,
                last_output_at, heartbeat_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                wid_a,
                job_a,
                4,
                10,
                now_str,
                "Epoch 1/3 - loss: 0.123",
                now_str,
                now_str,
            ),
        )
        conn.commit()

        with StatusCollector(db_path=db_path) as collector:
            status = collector.collect()

        busy = {bw.worker_id: bw for bw in status.workers["notebook"].busy_workers}
        assert "nb-A" in busy and "nb-B" in busy, (
            "LEFT JOIN must not drop workers without a heartbeat row"
        )

        # Worker A has heartbeat → fields populated.
        a = busy["nb-A"]
        assert a.current_cell == 4
        assert a.total_cells == 10
        assert a.last_output_excerpt == "Epoch 1/3 - loss: 0.123"
        assert a.cell_elapsed_seconds is not None
        assert a.since_last_output_seconds is not None

        # Worker B has no heartbeat → all per-cell fields are None.
        b = busy["nb-B"]
        assert b.current_cell is None
        assert b.total_cells is None
        assert b.last_output_excerpt is None
        assert b.cell_elapsed_seconds is None
        assert b.since_last_output_seconds is None

    def test_collect_with_pending_jobs(self, db_path, job_queue):
        """Test collecting status with pending jobs."""
        # Register worker
        conn = job_queue._get_conn()
        conn.execute(
            """
            INSERT INTO workers (worker_type, container_id, status, execution_mode)
            VALUES ('notebook', 'nb-worker-1', 'idle', 'direct')
            """
        )
        conn.commit()

        # Add pending jobs
        for i in range(15):
            job_queue.add_job(
                job_type="notebook",
                input_file=f"/path/to/input{i}.ipynb",
                output_file=f"/path/to/output{i}.html",
                content_hash=f"hash{i}",
                payload={"test": "data"},
            )

        with StatusCollector(db_path=db_path) as collector:
            status = collector.collect()

        # Should be warning due to high pending jobs
        assert status.health == SystemHealth.WARNING
        assert status.queue.pending == 15
        assert any("15 jobs pending" in w for w in status.warnings)

    def test_collect_queue_stats(self, db_path, job_queue):
        """Test collecting queue statistics."""
        # Register worker
        conn = job_queue._get_conn()
        conn.execute(
            """
            INSERT INTO workers (worker_type, container_id, status, execution_mode)
            VALUES ('notebook', 'nb-worker-1', 'idle', 'direct')
            """
        )
        conn.commit()

        # Add jobs with different statuses
        # Pending
        for i in range(3):
            job_queue.add_job(
                job_type="notebook",
                input_file=f"/path/to/pending{i}.ipynb",
                output_file=f"/path/to/pending{i}.html",
                content_hash=f"pending{i}",
                payload={},
            )

        # Completed in last hour (use UTC format compatible with SQLite datetime())
        thirty_min_ago = (datetime.now(UTC) - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
        for i in range(5):
            job_id = job_queue.add_job(
                job_type="notebook",
                input_file=f"/path/to/completed{i}.ipynb",
                output_file=f"/path/to/completed{i}.html",
                content_hash=f"completed{i}",
                payload={},
            )
            conn.execute(
                """
                UPDATE jobs
                SET status = 'completed', completed_at = ?
                WHERE id = ?
                """,
                (thirty_min_ago, job_id),
            )

        # Failed in last hour
        for i in range(2):
            job_id = job_queue.add_job(
                job_type="notebook",
                input_file=f"/path/to/failed{i}.ipynb",
                output_file=f"/path/to/failed{i}.html",
                content_hash=f"failed{i}",
                payload={},
            )
            conn.execute(
                """
                UPDATE jobs
                SET status = 'failed', completed_at = ?, error = 'Test error'
                WHERE id = ?
                """,
                (thirty_min_ago, job_id),
            )

        conn.commit()

        with StatusCollector(db_path=db_path) as collector:
            status = collector.collect()

        assert status.queue.pending == 3
        assert status.queue.completed_last_hour == 5
        assert status.queue.failed_last_hour == 2

    def test_collect_mixed_execution_modes(self, db_path, job_queue):
        """Test collecting status with mixed execution modes."""
        # Register workers with different execution modes
        conn = job_queue._get_conn()
        conn.execute(
            """
            INSERT INTO workers (worker_type, container_id, status, execution_mode)
            VALUES ('notebook', 'nb-worker-1', 'idle', 'direct')
            """
        )
        conn.execute(
            """
            INSERT INTO workers (worker_type, container_id, status, execution_mode)
            VALUES ('notebook', 'nb-worker-2', 'idle', 'docker')
            """
        )
        conn.commit()

        with StatusCollector(db_path=db_path) as collector:
            status = collector.collect()

        assert status.workers["notebook"].execution_mode == "mixed"

    def test_collect_database_info(self, db_path):
        """Test collecting database metadata."""
        with StatusCollector(db_path=db_path) as collector:
            status = collector.collect()

        assert status.database.path == str(db_path)
        assert status.database.accessible
        assert status.database.exists
        assert status.database.size_bytes is not None
        assert status.database.size_bytes > 0
        assert status.database.last_modified is not None

    def test_default_db_path_detection(self, tmp_path, monkeypatch):
        """Test default database path detection."""
        # Change to temp directory
        monkeypatch.chdir(tmp_path)

        # Create database in current directory
        db_path = tmp_path / "clm_jobs.db"
        init_database(db_path)

        # Collector should find it automatically
        collector = StatusCollector()
        assert collector.db_path == db_path

    def test_collect_queue_stats_uses_correct_timestamp_comparison(self, db_path, job_queue):
        """Test that queue stats correctly compare timestamps.

        This test ensures that:
        1. Jobs completed within the last hour are correctly counted
        2. The timestamp comparison works correctly with SQLite's datetime functions

        This is a regression test for a bug where UTC timestamps were compared
        incorrectly, causing completed/failed job counts to always be 0.
        """
        # Register worker
        conn = job_queue._get_conn()
        conn.execute(
            """
            INSERT INTO workers (worker_type, container_id, status, execution_mode)
            VALUES ('notebook', 'nb-worker-1', 'idle', 'direct')
            """
        )
        conn.commit()

        # Use SQLite's datetime format (no timezone suffix) for proper comparison
        # SQLite's datetime('now') returns UTC time in 'YYYY-MM-DD HH:MM:SS' format
        now = datetime.now(UTC).replace(tzinfo=None)

        # Job completed 30 minutes ago (should be counted)
        job_id_recent = job_queue.add_job(
            job_type="notebook",
            input_file="/path/to/recent.ipynb",
            output_file="/path/to/recent.html",
            content_hash="recent",
            payload={},
        )
        recent_time = (now - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "UPDATE jobs SET status = 'completed', completed_at = ? WHERE id = ?",
            (recent_time, job_id_recent),
        )

        # Job completed 2 hours ago (should NOT be counted)
        job_id_old = job_queue.add_job(
            job_type="notebook",
            input_file="/path/to/old.ipynb",
            output_file="/path/to/old.html",
            content_hash="old",
            payload={},
        )
        old_time = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "UPDATE jobs SET status = 'completed', completed_at = ? WHERE id = ?",
            (old_time, job_id_old),
        )

        # Job failed 15 minutes ago (should be counted)
        job_id_failed = job_queue.add_job(
            job_type="notebook",
            input_file="/path/to/failed.ipynb",
            output_file="/path/to/failed.html",
            content_hash="failed",
            payload={},
        )
        failed_time = (now - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "UPDATE jobs SET status = 'failed', completed_at = ?, error = 'Test error' WHERE id = ?",
            (failed_time, job_id_failed),
        )

        conn.commit()

        with StatusCollector(db_path=db_path) as collector:
            status = collector.collect()

        # Should count the recent completed and failed jobs, but not the old one
        assert status.queue.completed_last_hour == 1, "Should count 1 completed job from last hour"
        assert status.queue.failed_last_hour == 1, "Should count 1 failed job from last hour"


class TestDeriveCurrentCourseSpec:
    """Verify the heuristic that drives the monitor's header label."""

    @pytest.fixture
    def db_path(self, tmp_path):
        db = tmp_path / "test_status.db"
        init_database(db)
        return db

    def _register_idle_worker(self, db_path: Path) -> None:
        """Keep the collector out of the no-workers ERROR short-circuit."""
        with JobQueue(db_path) as queue:
            conn = queue._get_conn()
            conn.execute(
                """
                INSERT INTO workers (worker_type, container_id, status, execution_mode)
                VALUES ('notebook', 'nb-1', 'idle', 'direct')
                """
            )
            conn.commit()

    def test_returns_none_for_quiet_database(self, db_path):
        self._register_idle_worker(db_path)
        with StatusCollector(db_path=db_path) as collector:
            status = collector.collect()
        assert status.current_course_spec is None

    def test_picks_up_xml_spec_from_processing_outputs(self, tmp_path, db_path):
        # Make a fake course-spec layout so the heuristic has something to find.
        course_dir = tmp_path / "course"
        course_dir.mkdir()
        spec = course_dir / "python-best-practice.xml"
        spec.write_text("<course/>")
        out_dir = course_dir / "build" / "html" / "de"
        out_dir.mkdir(parents=True)

        self._register_idle_worker(db_path)
        with JobQueue(db_path) as queue:
            jid = queue.add_job(
                job_type="notebook",
                input_file="anything.py",
                output_file=str(out_dir / "lesson_1.ipynb"),
                content_hash="h",
                payload={},
            )
            conn = queue._get_conn()
            conn.execute(
                "UPDATE jobs SET status='processing', started_at=datetime('now') WHERE id=?",
                (jid,),
            )
            conn.commit()

        with StatusCollector(db_path=db_path) as collector:
            status = collector.collect()
        assert status.current_course_spec == "python-best-practice.xml"
