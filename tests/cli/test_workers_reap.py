"""Tests for ``clm workers reap`` (Fix 5).

``clm workers reap`` is the self-service recovery command that chains:

1. Orphan-row reap (via :meth:`JobQueue.mark_orphaned_jobs_failed`).
2. Process scan (via :func:`scan_worker_processes`).
3. Process-tree kill (via :func:`reap_process_tree`).
4. Stale worker-row cleanup (same sweep as ``clm workers cleanup``).

The scanner and reaper primitives have their own unit tests in
``tests/infrastructure/workers/test_process_reaper.py``. This file
covers the CLI wiring: arg parsing, DB → process matching, dry-run,
``--all`` opt-in for unmatched processes, confirmation prompt, and
exit codes. All psutil calls are patched at the ``process_reaper``
module boundary so tests do not touch real OS processes.

The CLI imports ``scan_worker_processes`` and ``reap_process_tree``
*inside* the ``workers_reap`` function body, so tests patch the
source module (``clm.infrastructure.workers.process_reaper``) rather
than the consumer module. The function re-runs the import each call,
so a source-module patch is picked up cleanly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from click.testing import CliRunner

from clm.cli.main import cli
from clm.infrastructure.database.job_queue import JobQueue
from clm.infrastructure.database.schema import init_database
from clm.infrastructure.workers.process_reaper import DiscoveredWorkerProcess


def _setup_db(tmp_path: Path) -> Path:
    """Create an empty, schema-initialised jobs DB."""
    db_path = tmp_path / "clm_jobs.db"
    init_database(db_path)
    return db_path


def _seed_inflight_job(db_path: Path, input_file: str = "a.py") -> int:
    """Insert a job row that looks like the worker died mid-flight.

    Orphan detection triggers on
    ``started_at IS NOT NULL AND completed_at IS NULL AND cancelled_at
    IS NULL AND status IN ('processing', 'pending')``. The cheapest way
    to satisfy that is to add a pending job and forcibly set
    ``started_at`` + ``status='processing'`` via a direct SQL update.
    """
    with JobQueue(db_path) as jq:
        job_id = jq.add_job(
            job_type="notebook",
            input_file=input_file,
            output_file=input_file.replace(".py", ".ipynb"),
            content_hash=f"hash-{input_file}",
            payload={},
        )
        conn = jq._get_conn()
        conn.execute(
            """
            UPDATE jobs
            SET status = 'processing', started_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (job_id,),
        )
        conn.commit()
    return job_id


def _make_scan_result(
    pid: int,
    db_path: Path | None,
    *,
    worker_type: str = "notebook",
    worker_id: str | None = None,
    cwd: str | None = None,
) -> DiscoveredWorkerProcess:
    """Build a :class:`DiscoveredWorkerProcess` for scanner mocks."""
    return DiscoveredWorkerProcess(
        pid=pid,
        worker_module=f"clm.workers.{worker_type}",
        cmdline=["python", "-m", f"clm.workers.{worker_type}"],
        db_path=db_path,
        worker_id=worker_id,
        cwd=Path(cwd) if cwd else None,
    )


def _invoke_reap(db_path: Path, *extra_args: str) -> Any:
    """Run ``clm workers reap --jobs-db-path <db> ...`` via CliRunner."""
    runner = CliRunner()
    return runner.invoke(
        cli,
        ["workers", "reap", "--jobs-db-path", str(db_path), *extra_args],
    )


# ---------------------------------------------------------------------------
# Help / basic plumbing
# ---------------------------------------------------------------------------


class TestReapHelp:
    def test_reap_help_lists_all_options(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["workers", "reap", "--help"])
        assert result.exit_code == 0
        assert "--dry-run" in result.output
        assert "--force" in result.output
        assert "--all" in result.output
        assert "--jobs-db-path" in result.output

    def test_reap_missing_db_fails_with_error(self, tmp_path):
        result = _invoke_reap(tmp_path / "does-not-exist.db", "--force")
        assert result.exit_code == 1
        assert "not found" in result.output


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


class TestReapNoOrphans:
    """Clean DB + no surviving processes → quiet, exit 0."""

    def test_clean_db_clean_processes(self, tmp_path):
        db_path = _setup_db(tmp_path)
        with patch(
            "clm.infrastructure.workers.process_reaper.scan_worker_processes",
            return_value=[],
        ):
            result = _invoke_reap(db_path, "--force")
        assert result.exit_code == 0
        assert "No orphan job rows found" in result.output
        assert "No surviving worker processes found" in result.output
        assert "No stale worker rows to clear" in result.output


class TestReapOrphanRowsOnly:
    """DB has orphan rows but no surviving processes (already dead)."""

    def test_orphan_rows_are_marked_failed(self, tmp_path):
        db_path = _setup_db(tmp_path)
        job_id = _seed_inflight_job(db_path, "lecture1.py")
        with patch(
            "clm.infrastructure.workers.process_reaper.scan_worker_processes",
            return_value=[],
        ):
            result = _invoke_reap(db_path, "--force")

        assert result.exit_code == 0
        assert "Marked 1 orphan job row(s)" in result.output
        assert "lecture1.py" in result.output
        # Actual DB state: job is now failed with the canonical error.
        with JobQueue(db_path) as jq:
            job = jq.get_job(job_id)
            assert job is not None
            assert job.status == "failed"
            assert job.error == JobQueue.ORPHAN_ERROR_MESSAGE


class TestReapProcessMatching:
    """DB path matching is the safety rail: only this DB's workers die."""

    def test_matches_current_db_by_env_var(self, tmp_path):
        db_path = _setup_db(tmp_path)
        mine = _make_scan_result(1111, db_path, worker_id="direct-notebook-0-aaa")

        with (
            patch(
                "clm.infrastructure.workers.process_reaper.scan_worker_processes",
                return_value=[mine],
            ),
            patch(
                "clm.infrastructure.workers.process_reaper.reap_process_tree",
                return_value=3,
            ) as mock_reap,
        ):
            result = _invoke_reap(db_path, "--force")

        assert result.exit_code == 0
        assert "[match]" in result.output
        assert "pid=1111" in result.output
        mock_reap.assert_called_once_with(1111, log_prefix="worker-1111")
        assert "Reaped 3 process(es) across 1 worker tree(s)" in result.output

    def test_unmatched_different_db_is_skipped_by_default(self, tmp_path):
        """A worker from a different worktree must not be killed.

        This is the safety behaviour the ``--all`` flag opts out of.
        """
        db_path = _setup_db(tmp_path)
        other_db = tmp_path / "other.db"
        other_db.touch()
        theirs = _make_scan_result(2222, other_db)

        with (
            patch(
                "clm.infrastructure.workers.process_reaper.scan_worker_processes",
                return_value=[theirs],
            ),
            patch(
                "clm.infrastructure.workers.process_reaper.reap_process_tree",
            ) as mock_reap,
        ):
            result = _invoke_reap(db_path, "--force")

        assert result.exit_code == 0
        assert "[skip]" in result.output
        assert "different DB" in result.output
        mock_reap.assert_not_called()
        assert "Nothing to kill" in result.output

    def test_unmatched_env_unreadable_is_skipped_by_default(self, tmp_path):
        """Process with unreadable env is shown but not reaped by default."""
        db_path = _setup_db(tmp_path)
        unknown = _make_scan_result(3333, None)

        with (
            patch(
                "clm.infrastructure.workers.process_reaper.scan_worker_processes",
                return_value=[unknown],
            ),
            patch(
                "clm.infrastructure.workers.process_reaper.reap_process_tree",
            ) as mock_reap,
        ):
            result = _invoke_reap(db_path, "--force")

        assert result.exit_code == 0
        assert "env unreadable" in result.output
        mock_reap.assert_not_called()

    def test_all_flag_reaps_unmatched_too(self, tmp_path):
        """``--all`` reaps both matched and unmatched processes."""
        db_path = _setup_db(tmp_path)
        other_db = tmp_path / "other.db"
        other_db.touch()
        mine = _make_scan_result(1, db_path)
        theirs = _make_scan_result(2, other_db)
        unknown = _make_scan_result(3, None)

        with (
            patch(
                "clm.infrastructure.workers.process_reaper.scan_worker_processes",
                return_value=[mine, theirs, unknown],
            ),
            patch(
                "clm.infrastructure.workers.process_reaper.reap_process_tree",
                return_value=1,
            ) as mock_reap,
        ):
            result = _invoke_reap(db_path, "--force", "--all")

        assert result.exit_code == 0
        assert mock_reap.call_count == 3
        killed_pids = {call.args[0] for call in mock_reap.call_args_list}
        assert killed_pids == {1, 2, 3}


class TestReapDryRun:
    """Dry-run must observe everything and kill nothing."""

    def test_dry_run_does_not_mark_orphans(self, tmp_path):
        db_path = _setup_db(tmp_path)
        job_id = _seed_inflight_job(db_path)
        with patch(
            "clm.infrastructure.workers.process_reaper.scan_worker_processes",
            return_value=[],
        ):
            result = _invoke_reap(db_path, "--dry-run")

        assert result.exit_code == 0
        assert "[dry-run]" in result.output
        # Orphan row is still processing — dry-run must not mutate it.
        with JobQueue(db_path) as jq:
            job = jq.get_job(job_id)
            assert job is not None
            assert job.status == "processing"

    def test_dry_run_does_not_kill_processes(self, tmp_path):
        db_path = _setup_db(tmp_path)
        mine = _make_scan_result(99, db_path)
        with (
            patch(
                "clm.infrastructure.workers.process_reaper.scan_worker_processes",
                return_value=[mine],
            ),
            patch(
                "clm.infrastructure.workers.process_reaper.reap_process_tree",
            ) as mock_reap,
        ):
            result = _invoke_reap(db_path, "--dry-run")

        assert result.exit_code == 0
        assert "Would kill 1 process tree" in result.output
        mock_reap.assert_not_called()


class TestReapConfirmationPrompt:
    """Without ``--force``, the command must prompt before killing."""

    def test_cancel_at_prompt(self, tmp_path):
        db_path = _setup_db(tmp_path)
        mine = _make_scan_result(55, db_path)
        runner = CliRunner()
        with (
            patch(
                "clm.infrastructure.workers.process_reaper.scan_worker_processes",
                return_value=[mine],
            ),
            patch(
                "clm.infrastructure.workers.process_reaper.reap_process_tree",
            ) as mock_reap,
        ):
            result = runner.invoke(
                cli,
                ["workers", "reap", "--jobs-db-path", str(db_path)],
                input="n\n",
            )

        assert result.exit_code == 0
        assert "Cancelled" in result.output
        mock_reap.assert_not_called()

    def test_confirm_at_prompt(self, tmp_path):
        db_path = _setup_db(tmp_path)
        mine = _make_scan_result(56, db_path)
        runner = CliRunner()
        with (
            patch(
                "clm.infrastructure.workers.process_reaper.scan_worker_processes",
                return_value=[mine],
            ),
            patch(
                "clm.infrastructure.workers.process_reaper.reap_process_tree",
                return_value=1,
            ) as mock_reap,
        ):
            result = runner.invoke(
                cli,
                ["workers", "reap", "--jobs-db-path", str(db_path)],
                input="y\n",
            )

        assert result.exit_code == 0
        mock_reap.assert_called_once()


class TestReapStaleWorkerRows:
    """After killing processes, stale DB rows get swept up too.

    This is what makes ``reap`` a superset of ``cleanup``: operators
    only need one command, not both.
    """

    def test_dead_worker_row_is_deleted(self, tmp_path):
        db_path = _setup_db(tmp_path)
        with JobQueue(db_path) as jq:
            conn = jq._get_conn()
            conn.execute(
                """
                INSERT INTO workers (worker_type, container_id, status, parent_pid)
                VALUES ('notebook', 'dead-1', 'dead', 1234)
                """
            )
            conn.commit()

        with patch(
            "clm.infrastructure.workers.process_reaper.scan_worker_processes",
            return_value=[],
        ):
            result = _invoke_reap(db_path, "--force")

        assert result.exit_code == 0
        assert "Cleared 1 stale worker row" in result.output
        with JobQueue(db_path) as jq:
            conn = jq._get_conn()
            rows = conn.execute("SELECT COUNT(*) FROM workers").fetchone()
            assert rows[0] == 0
