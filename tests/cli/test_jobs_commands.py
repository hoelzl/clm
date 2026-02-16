"""Tests for the 'clm jobs' command group."""

from pathlib import Path

from click.testing import CliRunner

from clm.cli.main import cli
from clm.infrastructure.database.job_queue import JobQueue
from clm.infrastructure.database.schema import init_database


def _setup_db(tmp_path: Path) -> Path:
    """Create and initialize a temporary jobs database."""
    db_path = tmp_path / "test_jobs.db"
    init_database(db_path)
    return db_path


def _add_pending_jobs(db_path: Path, count: int = 3, job_type: str = "notebook"):
    """Add pending jobs to the database."""
    with JobQueue(db_path) as jq:
        for i in range(count):
            jq.add_job(
                job_type=job_type,
                input_file=f"test{i}.py",
                output_file=f"test{i}.ipynb",
                content_hash=f"hash{i}",
                payload={},
            )


class TestJobsGroupHelp:
    def test_jobs_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["jobs", "--help"])
        assert result.exit_code == 0
        assert "cancel" in result.output
        assert "list" in result.output

    def test_jobs_cancel_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["jobs", "cancel", "--help"])
        assert result.exit_code == 0
        assert "--older-than" in result.output
        assert "--type" in result.output
        assert "--dry-run" in result.output
        assert "--force" in result.output

    def test_jobs_list_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["jobs", "list", "--help"])
        assert result.exit_code == 0
        assert "--status" in result.output
        assert "--limit" in result.output


class TestJobsCancelCommand:
    def test_cancel_no_database(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--jobs-db-path", str(tmp_path / "nonexistent.db"), "jobs", "cancel", "--force"],
        )
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_cancel_no_pending_jobs(self, tmp_path):
        db_path = _setup_db(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--jobs-db-path", str(db_path), "jobs", "cancel", "--force"],
        )
        assert result.exit_code == 0
        assert "No matching pending jobs" in result.output

    def test_cancel_all_pending_with_force(self, tmp_path):
        db_path = _setup_db(tmp_path)
        _add_pending_jobs(db_path, count=3)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--jobs-db-path", str(db_path), "jobs", "cancel", "--force"],
        )
        assert result.exit_code == 0
        assert "Cancelled 3 job(s)" in result.output

        # Verify all jobs are cancelled
        with JobQueue(db_path) as jq:
            assert len(jq.get_jobs_by_status("pending")) == 0

    def test_cancel_with_confirmation_yes(self, tmp_path):
        db_path = _setup_db(tmp_path)
        _add_pending_jobs(db_path, count=2)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--jobs-db-path", str(db_path), "jobs", "cancel"],
            input="y\n",
        )
        assert result.exit_code == 0
        assert "Cancelled 2 job(s)" in result.output

    def test_cancel_with_confirmation_no(self, tmp_path):
        db_path = _setup_db(tmp_path)
        _add_pending_jobs(db_path, count=2)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--jobs-db-path", str(db_path), "jobs", "cancel"],
            input="n\n",
        )
        assert result.exit_code == 0
        assert "Cancelled." in result.output

        # Jobs should still be pending
        with JobQueue(db_path) as jq:
            assert len(jq.get_jobs_by_status("pending")) == 2

    def test_cancel_dry_run(self, tmp_path):
        db_path = _setup_db(tmp_path)
        _add_pending_jobs(db_path, count=3)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--jobs-db-path", str(db_path), "jobs", "cancel", "--dry-run"],
        )
        assert result.exit_code == 0
        assert "DRY RUN" in result.output
        assert "3" in result.output

        # Jobs should still be pending
        with JobQueue(db_path) as jq:
            assert len(jq.get_jobs_by_status("pending")) == 3

    def test_cancel_by_type(self, tmp_path):
        db_path = _setup_db(tmp_path)
        _add_pending_jobs(db_path, count=2, job_type="notebook")
        _add_pending_jobs(db_path, count=1, job_type="drawio")

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--jobs-db-path", str(db_path), "jobs", "cancel", "--type", "notebook", "--force"],
        )
        assert result.exit_code == 0
        assert "Cancelled 2 job(s)" in result.output

        # Drawio job should remain
        with JobQueue(db_path) as jq:
            pending = jq.get_jobs_by_status("pending")
            assert len(pending) == 1
            assert pending[0].job_type == "drawio"

    def test_cancel_older_than(self, tmp_path):
        db_path = _setup_db(tmp_path)
        _add_pending_jobs(db_path, count=3)

        # Make one job old
        with JobQueue(db_path) as jq:
            conn = jq._get_conn()
            conn.execute(
                "UPDATE jobs SET created_at = datetime('now', '-700 seconds') "
                "WHERE input_file = 'test0.py'"
            )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--jobs-db-path",
                str(db_path),
                "jobs",
                "cancel",
                "--older-than",
                "10",
                "--force",
            ],
        )
        assert result.exit_code == 0
        assert "Cancelled 1 job(s)" in result.output

        # Two recent jobs should remain
        with JobQueue(db_path) as jq:
            assert len(jq.get_jobs_by_status("pending")) == 2


class TestJobsListCommand:
    def test_list_no_database(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--jobs-db-path", str(tmp_path / "nonexistent.db"), "jobs", "list"],
        )
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_list_empty(self, tmp_path):
        db_path = _setup_db(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--jobs-db-path", str(db_path), "jobs", "list"],
        )
        assert result.exit_code == 0
        assert "No pending jobs" in result.output

    def test_list_pending_jobs(self, tmp_path):
        db_path = _setup_db(tmp_path)
        _add_pending_jobs(db_path, count=2)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--jobs-db-path", str(db_path), "jobs", "list"],
        )
        assert result.exit_code == 0
        assert "2 pending job(s)" in result.output
        assert "test0.py" in result.output
        assert "test1.py" in result.output

    def test_list_json_format(self, tmp_path):
        db_path = _setup_db(tmp_path)
        _add_pending_jobs(db_path, count=1)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--jobs-db-path", str(db_path), "jobs", "list", "--format", "json"],
        )
        assert result.exit_code == 0
        assert '"job_type": "notebook"' in result.output
        assert '"status": "pending"' in result.output

    def test_list_by_status(self, tmp_path):
        db_path = _setup_db(tmp_path)
        _add_pending_jobs(db_path, count=2)

        # Complete one job
        with JobQueue(db_path) as jq:
            job = jq.get_next_job("notebook")
            jq.update_job_status(job.id, "completed")

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--jobs-db-path", str(db_path), "jobs", "list", "--status", "completed"],
        )
        assert result.exit_code == 0
        assert "1 completed job(s)" in result.output
