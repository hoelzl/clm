"""Integration tests for status command."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from clx.cli.main import cli
from clx.infrastructure.database.job_queue import JobQueue
from clx.infrastructure.database.schema import init_database


@pytest.mark.db_only
class TestStatusCommandIntegration:
    """Tests for status command with database (no workers)."""

    @pytest.fixture
    def db_path(self, tmp_path):
        """Create temporary database."""
        db_path = tmp_path / "test_jobs.db"
        init_database(db_path)
        return db_path

    @pytest.fixture
    def job_queue(self, db_path):
        """Create JobQueue instance."""
        with JobQueue(db_path) as queue:
            yield queue

    @pytest.fixture
    def runner(self):
        """Create Click test runner."""
        return CliRunner()

    def test_status_command_no_database(self, runner, tmp_path):
        """Test status command when database doesn't exist."""
        db_path = tmp_path / "nonexistent.db"

        result = runner.invoke(cli, ["status", "--jobs-db-path", str(db_path)])

        assert result.exit_code == 2  # Error
        assert "not found" in result.output.lower() or "not accessible" in result.output.lower()

    def test_status_command_empty_database(self, runner, db_path):
        """Test status command with empty database."""
        result = runner.invoke(cli, ["status", "--jobs-db-path", str(db_path)])

        assert result.exit_code == 2  # Error
        assert "no workers" in result.output.lower()

    def test_status_command_with_workers(self, runner, db_path, job_queue):
        """Test status command with registered workers."""
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

        result = runner.invoke(cli, ["status", "--jobs-db-path", str(db_path)])

        assert result.exit_code == 0  # Healthy
        assert "notebook" in result.output.lower()
        assert "plantuml" in result.output.lower()
        assert "1 total" in result.output.lower()

    def test_status_command_json_format(self, runner, db_path, job_queue):
        """Test status command with JSON format."""
        # Register a worker
        conn = job_queue._get_conn()
        conn.execute(
            """
            INSERT INTO workers (worker_type, container_id, status, execution_mode)
            VALUES ('notebook', 'nb-worker-1', 'idle', 'direct')
            """
        )
        conn.commit()

        result = runner.invoke(cli, ["status", "--jobs-db-path", str(db_path), "--format=json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "status" in data
        assert data["status"] == "healthy"
        assert "workers" in data
        assert "queue" in data
        assert data["workers"]["notebook"]["total"] == 1

    def test_status_command_compact_format(self, runner, db_path, job_queue):
        """Test status command with compact format."""
        # Register a worker
        conn = job_queue._get_conn()
        conn.execute(
            """
            INSERT INTO workers (worker_type, container_id, status, execution_mode)
            VALUES ('notebook', 'nb-worker-1', 'idle', 'direct')
            """
        )
        conn.commit()

        result = runner.invoke(cli, ["status", "--jobs-db-path", str(db_path), "--format=compact"])

        assert result.exit_code == 0
        assert "healthy" in result.output
        assert "notebook" in result.output

    def test_status_command_workers_only(self, runner, db_path, job_queue):
        """Test status command with --workers flag."""
        # Register a worker
        conn = job_queue._get_conn()
        conn.execute(
            """
            INSERT INTO workers (worker_type, container_id, status, execution_mode)
            VALUES ('notebook', 'nb-worker-1', 'idle', 'direct')
            """
        )
        conn.commit()

        result = runner.invoke(cli, ["status", "--jobs-db-path", str(db_path), "--workers"])

        assert result.exit_code == 0
        assert "notebook" in result.output.lower()
        # Should not show queue status
        assert "job queue" not in result.output.lower()

    def test_status_command_jobs_only(self, runner, db_path, job_queue):
        """Test status command with --jobs flag."""
        # Register a worker
        conn = job_queue._get_conn()
        conn.execute(
            """
            INSERT INTO workers (worker_type, container_id, status, execution_mode)
            VALUES ('notebook', 'nb-worker-1', 'idle', 'direct')
            """
        )
        conn.commit()

        # Add some jobs
        for i in range(3):
            job_queue.add_job(
                job_type="notebook",
                input_file=f"/path/to/input{i}.ipynb",
                output_file=f"/path/to/output{i}.html",
                content_hash=f"hash{i}",
                payload={},
            )

        result = runner.invoke(cli, ["status", "--jobs-db-path", str(db_path), "--jobs"])

        assert result.exit_code == 0
        assert "job queue" in result.output.lower()
        assert "pending" in result.output.lower()
        # Should not show CLX System Status header
        assert "clx system status" not in result.output.lower()

    def test_status_command_no_color(self, runner, db_path, job_queue):
        """Test status command with --no-color flag."""
        # Register a worker
        conn = job_queue._get_conn()
        conn.execute(
            """
            INSERT INTO workers (worker_type, container_id, status, execution_mode)
            VALUES ('notebook', 'nb-worker-1', 'idle', 'direct')
            """
        )
        conn.commit()

        result = runner.invoke(cli, ["status", "--jobs-db-path", str(db_path), "--no-color"])

        assert result.exit_code == 0
        # Check that there are no ANSI escape codes
        assert "\033[" not in result.output

    def test_status_command_with_pending_jobs(self, runner, db_path, job_queue):
        """Test status command with many pending jobs."""
        # Register a worker
        conn = job_queue._get_conn()
        conn.execute(
            """
            INSERT INTO workers (worker_type, container_id, status, execution_mode)
            VALUES ('notebook', 'nb-worker-1', 'idle', 'direct')
            """
        )
        conn.commit()

        # Add many pending jobs (triggers warning)
        for i in range(15):
            job_queue.add_job(
                job_type="notebook",
                input_file=f"/path/to/input{i}.ipynb",
                output_file=f"/path/to/output{i}.html",
                content_hash=f"hash{i}",
                payload={},
            )

        result = runner.invoke(cli, ["status", "--jobs-db-path", str(db_path)])

        assert result.exit_code == 1  # Warning
        assert "15" in result.output
        assert "pending" in result.output.lower()

    def test_status_command_help(self, runner):
        """Test status command help text."""
        result = runner.invoke(cli, ["status", "--help"])

        assert result.exit_code == 0
        assert "Show CLX system status" in result.output
        assert "--workers" in result.output
        assert "--jobs" in result.output
        assert "--format" in result.output
        assert "--jobs-db-path" in result.output
