"""Tests for ``clm db`` commands.

Covers ``stats``, ``prune``, ``vacuum``, ``clean``, and the legacy
``delete-database`` command. Each test seeds a real tmp_path SQLite DB
via ``init_database`` + ``JobQueue`` / ``DatabaseManager`` so the CLI
paths exercise the real schema.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.main import cli
from clm.infrastructure.database.db_operations import DatabaseManager
from clm.infrastructure.database.job_queue import JobQueue
from clm.infrastructure.database.schema import init_database


@pytest.fixture
def db_paths(tmp_path):
    jobs = tmp_path / "clm_jobs.db"
    cache = tmp_path / "clm_cache.db"
    return {"jobs": jobs, "cache": cache, "root": tmp_path}


@pytest.fixture
def initialized_dbs(db_paths):
    init_database(db_paths["jobs"])
    # DatabaseManager initialises its own schema on first use.
    with DatabaseManager(db_paths["cache"]):
        pass
    return db_paths


def _invoke(db_paths, *args, input: str | None = None):
    runner = CliRunner()
    return runner.invoke(
        cli,
        [
            "--jobs-db-path",
            str(db_paths["jobs"]),
            "--cache-db-path",
            str(db_paths["cache"]),
            *args,
        ],
        input=input,
    )


def _seed_job(db_path: Path, *, status: str = "completed", input_file: str = "a.py") -> int:
    with JobQueue(db_path) as jq:
        job_id = jq.add_job(
            job_type="notebook",
            input_file=input_file,
            output_file=input_file.replace(".py", ".ipynb"),
            content_hash=f"hash-{input_file}",
            payload={},
        )
        conn = jq._get_conn()
        if status == "completed":
            conn.execute(
                "UPDATE jobs SET status = 'completed', "
                "completed_at = datetime('now', '-60 days') WHERE id = ?",
                (job_id,),
            )
        elif status == "failed":
            conn.execute(
                "UPDATE jobs SET status = 'failed', "
                "created_at = datetime('now', '-60 days') WHERE id = ?",
                (job_id,),
            )
        elif status == "processing":
            conn.execute(
                "UPDATE jobs SET status = 'processing', "
                "started_at = CURRENT_TIMESTAMP WHERE id = ?",
                (job_id,),
            )
        conn.commit()
    return job_id


# ---------------------------------------------------------------------------
# db stats
# ---------------------------------------------------------------------------


class TestDbStats:
    def test_stats_missing_dbs(self, db_paths):
        result = _invoke(db_paths, "db", "stats")
        assert result.exit_code == 0, result.output
        assert "not found" in result.output

    def test_stats_with_seeded_dbs(self, initialized_dbs):
        _seed_job(initialized_dbs["jobs"], status="completed", input_file="x.py")
        _seed_job(initialized_dbs["jobs"], status="failed", input_file="y.py")

        result = _invoke(initialized_dbs, "db", "stats")
        assert result.exit_code == 0, result.output
        assert "Jobs Database" in result.output
        assert "Cache Database" in result.output
        # jobs_by_status breakdown should list both statuses.
        assert "completed" in result.output
        assert "failed" in result.output


# ---------------------------------------------------------------------------
# db prune
# ---------------------------------------------------------------------------


class TestDbPrune:
    def test_prune_dry_run_no_mutation(self, initialized_dbs):
        job_id = _seed_job(initialized_dbs["jobs"], status="completed")

        result = _invoke(initialized_dbs, "db", "prune", "--completed-days=7", "--dry-run")
        assert result.exit_code == 0, result.output
        assert "DRY RUN" in result.output

        # Job survives.
        with JobQueue(initialized_dbs["jobs"]) as jq:
            assert jq.get_job(job_id) is not None

    def test_prune_deletes_old_completed_jobs(self, initialized_dbs):
        job_id = _seed_job(initialized_dbs["jobs"], status="completed")

        result = _invoke(initialized_dbs, "db", "prune", "--completed-days=7")
        assert result.exit_code == 0, result.output
        assert "Prune complete" in result.output

        with JobQueue(initialized_dbs["jobs"]) as jq:
            assert jq.get_job(job_id) is None

    def test_prune_missing_dbs_no_error(self, db_paths):
        result = _invoke(db_paths, "db", "prune", "--completed-days=7")
        assert result.exit_code == 0, result.output
        assert "not found" in result.output

    def test_prune_remove_missing_deletes_jobs_for_missing_files(self, initialized_dbs):
        # Job references a file that doesn't exist on disk.
        _seed_job(initialized_dbs["jobs"], status="completed", input_file="ghost.py")

        result = _invoke(initialized_dbs, "db", "prune", "--remove-missing")
        assert result.exit_code == 0, result.output
        assert "missing source files" in result.output.lower()

    def test_prune_remove_missing_dry_run_preserves(self, initialized_dbs):
        _seed_job(initialized_dbs["jobs"], status="completed", input_file="ghost.py")
        result = _invoke(initialized_dbs, "db", "prune", "--remove-missing", "--dry-run")
        assert result.exit_code == 0, result.output
        # Job still present after dry-run.
        with JobQueue(initialized_dbs["jobs"]) as jq:
            stats = jq.get_database_stats()
            assert stats["jobs_count"] >= 1


# ---------------------------------------------------------------------------
# db vacuum
# ---------------------------------------------------------------------------


class TestDbVacuum:
    def test_vacuum_both(self, initialized_dbs):
        result = _invoke(initialized_dbs, "db", "vacuum")
        assert result.exit_code == 0, result.output
        assert "Vacuuming jobs database" in result.output
        assert "Vacuuming cache database" in result.output

    def test_vacuum_only_jobs(self, initialized_dbs):
        result = _invoke(initialized_dbs, "db", "vacuum", "--which=jobs")
        assert result.exit_code == 0, result.output
        assert "Vacuuming jobs database" in result.output
        assert "Vacuuming cache database" not in result.output

    def test_vacuum_missing_dbs(self, db_paths):
        result = _invoke(db_paths, "db", "vacuum")
        assert result.exit_code == 0, result.output
        assert "not found" in result.output


# ---------------------------------------------------------------------------
# db clean
# ---------------------------------------------------------------------------


class TestDbClean:
    def test_clean_cancel_at_prompt(self, initialized_dbs):
        result = _invoke(initialized_dbs, "db", "clean", input="n\n")
        assert result.exit_code == 0, result.output
        assert "Cancelled" in result.output

    def test_clean_force_runs_prune_and_vacuum(self, initialized_dbs):
        result = _invoke(initialized_dbs, "db", "clean", "--force")
        assert result.exit_code == 0, result.output
        # prune and vacuum both run.
        assert "Pruning jobs database" in result.output
        assert "Vacuuming jobs database" in result.output
        assert "Cleanup complete" in result.output

    def test_clean_remove_missing_passthrough(self, initialized_dbs):
        _seed_job(initialized_dbs["jobs"], status="completed", input_file="gone.py")
        result = _invoke(initialized_dbs, "db", "clean", "--force", "--remove-missing")
        assert result.exit_code == 0, result.output
        assert "missing source files" in result.output.lower()


# ---------------------------------------------------------------------------
# legacy delete-database command
# ---------------------------------------------------------------------------


class TestDeleteDatabase:
    def test_delete_both_when_present(self, initialized_dbs):
        result = _invoke(initialized_dbs, "delete-database", "--which=both")
        assert result.exit_code == 0, result.output
        assert not initialized_dbs["jobs"].exists()
        assert not initialized_dbs["cache"].exists()

    def test_delete_only_jobs(self, initialized_dbs):
        result = _invoke(initialized_dbs, "delete-database", "--which=jobs")
        assert result.exit_code == 0, result.output
        assert not initialized_dbs["jobs"].exists()
        assert initialized_dbs["cache"].exists()

    def test_delete_when_absent(self, db_paths):
        result = _invoke(db_paths, "delete-database", "--which=both")
        assert result.exit_code == 0, result.output
        assert "No databases found" in result.output


# ---------------------------------------------------------------------------
# help
# ---------------------------------------------------------------------------


class TestDbHelp:
    def test_group_help_lists_subcommands(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["db", "--help"])
        assert result.exit_code == 0
        for sub in ("stats", "prune", "vacuum", "clean"):
            assert sub in result.output
