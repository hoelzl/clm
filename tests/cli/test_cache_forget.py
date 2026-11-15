"""Tests for ``clm cache forget`` (issue #1245).

A partial Draw.io render was cached as a success and replayed by every
build; recovering needed raw SQL against two databases. ``forget`` removes
every cached row for a file: the source-keyed rows in ``clm_cache.db`` and
the job-cache rows of the outputs it produced in ``clm_jobs.db``. The tests
seed both databases through clm's own writers, so the keys are the ones a
build stores.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from click.testing import CliRunner

from clm.cli.commands.cache import forget_cached_results
from clm.cli.main import cli
from clm.core.messaging.base_classes import ImageResult
from clm.infrastructure.database.db_operations import DatabaseManager
from clm.infrastructure.database.job_queue import JobQueue
from clm.infrastructure.database.schema import init_database


def _seed(tmp_path: Path, source: Path, output: Path, hashes=("h1", "h2")) -> tuple[Path, Path]:
    """Cache ``len(hashes)`` versions of ``source`` -> ``output`` in both DBs."""
    cache_db = tmp_path / "clm_cache.db"
    jobs_db = tmp_path / "clm_jobs.db"
    with DatabaseManager(cache_db) as db:
        for h in hashes:
            result = ImageResult(
                correlation_id="corr",
                output_file=str(output),
                input_file=str(source),
                content_hash=h,
                result=b"png bytes " + h.encode(),
            )
            db.store_result(str(source), h, "corr", result)
    init_database(jobs_db)
    with JobQueue(jobs_db) as queue:
        for h in hashes:
            queue.add_to_cache(str(output), h, {"format": "png"})
    return cache_db, jobs_db


def _count(db: Path, table: str) -> int:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def _paths(tmp_path: Path, name: str = "merging") -> tuple[Path, Path]:
    source = tmp_path / "img" / f"{name}.drawio"
    output = tmp_path / "img-generated" / f"{name}.png"
    return source, output


class TestForgetCachedResults:
    def test_source_path_clears_both_databases(self, tmp_path):
        source, output = _paths(tmp_path)
        cache_db, jobs_db = _seed(tmp_path, source, output)

        report = forget_cached_results([source], cache_db, jobs_db)

        assert report["rows"]["processed_files"] == 2
        assert report["rows"]["results_cache"] == 2
        assert report["output_files"] == [str(output)]
        assert _count(cache_db, "processed_files") == 0
        assert _count(jobs_db, "results_cache") == 0

    def test_other_files_are_untouched(self, tmp_path):
        source, output = _paths(tmp_path)
        other_source, other_output = _paths(tmp_path, "other")
        cache_db, jobs_db = _seed(tmp_path, source, output)
        _seed(tmp_path, other_source, other_output, hashes=("h1",))

        forget_cached_results([source], cache_db, jobs_db)

        assert _count(cache_db, "processed_files") == 1
        assert _count(jobs_db, "results_cache") == 1

    def test_output_path_clears_the_job_cache(self, tmp_path):
        # A job-cache row can outlive its processed_files row (clm_cache.db
        # deleted); naming the output still reaches it.
        source, output = _paths(tmp_path)
        cache_db, jobs_db = _seed(tmp_path, source, output)

        report = forget_cached_results([output], cache_db, jobs_db)

        assert report["rows"]["results_cache"] == 2
        assert report["rows"]["processed_files"] == 0

    def test_dry_run_deletes_nothing(self, tmp_path):
        source, output = _paths(tmp_path)
        cache_db, jobs_db = _seed(tmp_path, source, output)

        report = forget_cached_results([source], cache_db, jobs_db, dry_run=True)

        assert report["rows"]["processed_files"] == 2
        assert _count(cache_db, "processed_files") == 2
        assert _count(jobs_db, "results_cache") == 2

    def test_relative_path_matches_absolute_key(self, tmp_path, monkeypatch):
        source, output = _paths(tmp_path)
        cache_db, jobs_db = _seed(tmp_path, source, output)
        monkeypatch.chdir(tmp_path)

        report = forget_cached_results([Path("img/merging.drawio")], cache_db, jobs_db)

        assert report["rows"]["processed_files"] == 2

    def test_missing_databases_report_zero(self, tmp_path):
        source, _output = _paths(tmp_path)

        report = forget_cached_results(
            [source], tmp_path / "absent_cache.db", tmp_path / "absent_jobs.db"
        )

        assert not any(report["rows"].values())
        assert report["cache_db_exists"] is False


class TestForgetCommand:
    def _invoke(self, tmp_path: Path, *args: str):
        return CliRunner().invoke(
            cli,
            [
                "--cache-db-path",
                str(tmp_path / "clm_cache.db"),
                "--jobs-db-path",
                str(tmp_path / "clm_jobs.db"),
                "cache",
                "forget",
                *args,
            ],
            catch_exceptions=False,
        )

    def test_human_output(self, tmp_path):
        source, output = _paths(tmp_path)
        _seed(tmp_path, source, output)

        result = self._invoke(tmp_path, str(source))

        assert result.exit_code == 0, result.output
        assert "Deleted    2 row(s) from processed_files" in result.output
        assert "Deleted    2 row(s) from results_cache" in result.output
        assert str(output) in result.output

    def test_json_dry_run(self, tmp_path):
        source, output = _paths(tmp_path)
        _seed(tmp_path, source, output)

        result = self._invoke(tmp_path, str(source), "--dry-run", "--json")

        assert result.exit_code == 0, result.output
        report = json.loads(result.stdout)
        assert report["dry_run"] is True
        assert report["rows"]["processed_files"] == 2
        assert _count(tmp_path / "clm_cache.db", "processed_files") == 2

    def test_nothing_cached_says_so(self, tmp_path):
        source, _output = _paths(tmp_path)
        _seed(tmp_path, *_paths(tmp_path, "other"))

        result = self._invoke(tmp_path, str(source))

        assert result.exit_code == 0
        assert "Nothing cached for these paths" in result.output
