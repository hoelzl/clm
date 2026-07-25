"""
Integration tests for CLI with real backend.

These tests run the CLI with real backend and worker processes.
They verify that the full CLI → Backend → Workers → Output pipeline works.

Mark with @pytest.mark.integration to run separately from unit tests.

**These tests must be able to fail.** They previously wrapped every functional
assertion in ``if result.exit_code == 0:``, so a build that crashed on every
invocation would still produce a green checkmark for
"CLI → Backend → Workers → Output" (finding T4 of the 2026-07-24 adversarial
review). A real build of ``test-spec-2`` succeeds both locally and in CI, so
success is asserted directly. If one of these ever legitimately cannot run in
some environment, skip it with a stated reason — a skip is honest, a swallowed
assertion is not.
"""

import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner
from click.testing import Result as CliResult

from clm.cli.main import cli

# Committed test fixtures. These ship with the repository, so a missing file is
# a broken checkout, not an environment CLM should quietly skip around.
SPEC_FILE = Path("test-data/course-specs/test-spec-2.xml")
DATA_DIR = Path("test-data")

# Course name from test-spec-2.xml, in both languages.
COURSE_DIR_DE = "Kurs 2-de"
COURSE_DIR_EN = "Kurs 2-en"

# A cache row no build can produce: seeded before a run so the test can tell
# whether ``--clear-cache`` actually dropped the table.
SENTINEL_FILE_PATH = "::clear-cache-sentinel::"


def _require_test_data() -> None:
    """Fail loudly if the committed fixtures are missing.

    This used to be ``pytest.skip("Test data not available")``. A skip on data
    that is checked into the repository can only ever mean "these tests are
    silently not running", which is the exact failure mode finding T1
    documented for the worker integration suite.
    """
    assert SPEC_FILE.exists(), (
        f"Committed test fixture {SPEC_FILE} is missing (cwd={Path.cwd()}). "
        f"These tests must run from the repository root."
    )
    assert DATA_DIR.is_dir(), f"Committed test data directory {DATA_DIR} is missing."


def _invoke_build(
    runner: CliRunner,
    tmp_path: Path,
    *,
    output_dir: Path | None = None,
    cache_db: Path | None = None,
    jobs_db: Path | None = None,
    spec_file: Path = SPEC_FILE,
    data_dir: Path = DATA_DIR,
    build_args: list[str] | None = None,
) -> CliResult:
    """Invoke ``clm build`` with every database path pinned under *tmp_path*.

    Pinning the *cache* DB matters and was previously missing: the option
    defaults to ``clm_cache.db`` anchored at the discovered project root
    (``main.py:_anchor_default``), so these tests wrote a multi-megabyte cache
    database into the working tree and shared it across xdist workers.
    """
    return runner.invoke(
        cli,
        [
            "--jobs-db-path",
            str(jobs_db or tmp_path / "jobs.db"),
            "--cache-db-path",
            str(cache_db or tmp_path / "cache.db"),
            "--telemetry-db-path",
            str(tmp_path / "telemetry.db"),
            "build",
            str(spec_file),
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(output_dir or tmp_path / "output"),
            "--log-level",
            "WARNING",
            *(build_args or []),
        ],
    )


def _assert_build_succeeded(result: CliResult) -> None:
    """Assert the build exited cleanly, showing why when it did not."""
    assert result.exit_code == 0, (
        f"clm build exited {result.exit_code}\n"
        f"--- output ---\n{result.output}\n"
        f"--- exception ---\n{result.exception!r}"
    )


def _tree_summary(output_dir: Path, limit: int = 40) -> str:
    """Render the produced tree, so a failure says what *was* built."""
    if not output_dir.exists():
        return "<output dir does not exist>"
    files = sorted(str(p.relative_to(output_dir)) for p in output_dir.rglob("*") if p.is_file())
    if not files:
        return "<no files>"
    suffix = f"\n  … (+{len(files) - limit} more)" if len(files) > limit else ""
    return "\n  " + "\n  ".join(files[:limit]) + suffix


def _assert_course_output_present(output_dir: Path) -> None:
    """Assert the build produced the expected course tree, not just a directory.

    The previous version globbed ``output_dir/kurs-2-*`` — a pattern that has
    matched nothing since the three-tier default output structure landed
    (PR #386) — and then only asserted *inside* ``if course_dirs:``, so it
    could not fail either way.
    """
    assert output_dir.is_dir(), f"{output_dir} was not created"

    trainer = output_dir / "trainer"
    assert trainer.is_dir(), f"no trainer/ tier under {output_dir}: {_tree_summary(output_dir)}"

    for course_dir in (COURSE_DIR_DE, COURSE_DIR_EN):
        assert (trainer / course_dir).is_dir(), (
            f"missing {course_dir} under {trainer}: {_tree_summary(output_dir)}"
        )

    # One representative artefact per output format, proving the notebook
    # worker actually ran rather than the tree merely being scaffolded.
    en_slides = trainer / COURSE_DIR_EN / "Slides"
    expected = [
        en_slides / "Html" / "Completed" / "Week 1" / "01 Slides from Test 3.html",
        en_slides / "Notebooks" / "Completed" / "Week 1" / "01 Slides from Test 3.ipynb",
        en_slides / "Python" / "Completed" / "Week 1" / "01 Slides from Test 3.py",
    ]
    missing = [str(p.relative_to(output_dir)) for p in expected if not p.is_file()]
    assert not missing, f"missing build outputs {missing}: {_tree_summary(output_dir)}"


def _seed_cache_sentinel(cache_db: Path) -> None:
    """Insert a row into the cache DB that no build could ever write.

    The schema mirrors ``DatabaseManager.init_db``; only the columns the
    assertions read are populated.
    """
    cache_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(cache_db)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT,
                content_hash TEXT,
                correlation_id TEXT,
                result BLOB,
                output_metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "INSERT INTO processed_files (file_path, content_hash, output_metadata) "
            "VALUES (?, ?, ?)",
            (SENTINEL_FILE_PATH, "sentinel", "sentinel"),
        )
        conn.commit()
    finally:
        conn.close()


def _cache_row_counts(cache_db: Path) -> tuple[int, int]:
    """Return ``(sentinel_rows, genuine_rows)`` in the cache DB."""
    assert cache_db.exists(), f"cache database {cache_db} was never created"
    conn = sqlite3.connect(cache_db)
    try:
        sentinel = conn.execute(
            "SELECT COUNT(*) FROM processed_files WHERE file_path = ?",
            (SENTINEL_FILE_PATH,),
        ).fetchone()[0]
        genuine = conn.execute(
            "SELECT COUNT(*) FROM processed_files WHERE file_path != ?",
            (SENTINEL_FILE_PATH,),
        ).fetchone()[0]
        return sentinel, genuine
    finally:
        conn.close()


@pytest.mark.integration
class TestCliWithSqliteBackend:
    """Integration tests using SQLite backend (no external dependencies)"""

    def test_build_simple_course_with_sqlite(self, tmp_path):
        """A full CLI build of test-spec-2 succeeds and produces the course tree."""
        _require_test_data()
        runner = CliRunner()
        output_dir = tmp_path / "output"

        result = _invoke_build(
            runner, tmp_path, output_dir=output_dir, build_args=["--ignore-cache"]
        )

        _assert_build_succeeded(result)
        _assert_course_output_present(output_dir)

    def test_build_with_clear_cache(self, tmp_path):
        """``--clear-cache`` drops existing cache rows; without it they survive.

        Asserting on a sentinel row is what makes this test able to fail: the
        build repopulates ``processed_files`` immediately after clearing it, so
        a plain row count proves nothing. The no-flag run is the control — the
        pair fails if ``--clear-cache`` becomes a no-op *or* if clearing starts
        happening unconditionally.
        """
        _require_test_data()
        runner = CliRunner()
        cache_db = tmp_path / "cache.db"

        # --- with --clear-cache: the pre-existing row must be gone -----------
        _seed_cache_sentinel(cache_db)
        assert _cache_row_counts(cache_db) == (1, 0)

        result = _invoke_build(
            runner,
            tmp_path,
            output_dir=tmp_path / "output-cleared",
            cache_db=cache_db,
            jobs_db=tmp_path / "jobs-cleared.db",
            build_args=["--clear-cache"],
        )
        _assert_build_succeeded(result)

        sentinel, genuine = _cache_row_counts(cache_db)
        assert sentinel == 0, "--clear-cache did not drop the pre-existing cache rows"
        assert genuine > 0, "the build did not repopulate the cache after clearing it"

        # --- without --clear-cache: the same row must survive ----------------
        _seed_cache_sentinel(cache_db)
        result = _invoke_build(
            runner,
            tmp_path,
            output_dir=tmp_path / "output-kept",
            cache_db=cache_db,
            jobs_db=tmp_path / "jobs-kept.db",
        )
        _assert_build_succeeded(result)

        sentinel, genuine = _cache_row_counts(cache_db)
        assert sentinel == 1, "a build without --clear-cache dropped existing cache rows"
        assert genuine > 0

    def test_build_with_custom_db_path(self, tmp_path):
        """Custom database paths are honoured and the build still succeeds."""
        _require_test_data()
        runner = CliRunner()

        cache_db = tmp_path / "custom" / "my_cache.db"
        jobs_db = tmp_path / "custom" / "my_jobs.db"
        cache_db.parent.mkdir(parents=True, exist_ok=True)

        result = _invoke_build(runner, tmp_path, cache_db=cache_db, jobs_db=jobs_db)

        _assert_build_succeeded(result)
        assert cache_db.exists(), f"cache database not created at {cache_db}"
        assert _cache_row_counts(cache_db)[1] > 0, "custom cache database was never populated"

    def test_build_output_directory_creation(self, tmp_path):
        """A non-existent output directory is created by the build."""
        _require_test_data()
        runner = CliRunner()
        output_dir = tmp_path / "new_output_dir"
        assert not output_dir.exists()

        result = _invoke_build(runner, tmp_path, output_dir=output_dir)

        _assert_build_succeeded(result)
        assert output_dir.is_dir(), "output directory was not created"
        _assert_course_output_present(output_dir)


@pytest.mark.integration
class TestDeleteDatabaseIntegration:
    """Integration tests for delete_database command"""

    def test_delete_database_removes_existing_db(self, tmp_path):
        """Test that delete_database actually removes the database file"""
        runner = CliRunner()

        db_path = tmp_path / "test.db"
        db_path.write_text("dummy database content")

        assert db_path.exists()

        result = runner.invoke(
            cli,
            [
                "--jobs-db-path",
                str(db_path),
                "--cache-db-path",
                str(tmp_path / "nonexistent_cache.db"),
                "db",
                "delete",
                "--which=jobs",
            ],
        )

        assert result.exit_code == 0
        assert "Deleted:" in result.output
        assert not db_path.exists()

    def test_delete_database_idempotent(self, tmp_path):
        """Test that delete_database can be called multiple times safely"""
        runner = CliRunner()

        db_path = tmp_path / "test.db"

        # First call - no database exists
        result1 = runner.invoke(
            cli,
            [
                "--jobs-db-path",
                str(db_path),
                "--cache-db-path",
                str(tmp_path / "nonexistent_cache.db"),
                "db",
                "delete",
                "--which=jobs",
            ],
        )
        assert result1.exit_code == 0
        assert "No databases found" in result1.output

        # Second call - still no database
        result2 = runner.invoke(
            cli,
            [
                "--jobs-db-path",
                str(db_path),
                "--cache-db-path",
                str(tmp_path / "nonexistent_cache.db"),
                "db",
                "delete",
                "--which=jobs",
            ],
        )
        assert result2.exit_code == 0
        assert "No databases found" in result2.output


@pytest.mark.integration
class TestCliBuildWithDifferentOptions:
    """Test various CLI build option combinations"""

    def test_build_with_ignore_cache_flag(self, tmp_path):
        """``--ignore-cache`` still produces a complete build."""
        _require_test_data()
        runner = CliRunner()
        output_dir = tmp_path / "output"

        result = _invoke_build(
            runner, tmp_path, output_dir=output_dir, build_args=["--ignore-cache"]
        )

        _assert_build_succeeded(result)
        _assert_course_output_present(output_dir)

    def test_build_all_boolean_flags_together(self, tmp_path):
        """Combining the boolean build flags still produces a complete build."""
        _require_test_data()
        runner = CliRunner()
        output_dir = tmp_path / "output"

        result = _invoke_build(
            runner,
            tmp_path,
            output_dir=output_dir,
            build_args=["--ignore-cache", "--clear-cache", "--print-correlation-ids"],
        )

        _assert_build_succeeded(result)
        _assert_course_output_present(output_dir)


@pytest.mark.integration
class TestCliErrorHandling:
    """Test CLI error handling and edge cases"""

    def test_build_with_invalid_spec_file_content(self, tmp_path):
        """An unparseable spec file fails with a spec-file diagnostic, not a traceback."""
        runner = CliRunner()

        spec_file = tmp_path / "invalid.xml"
        spec_file.write_text("This is not valid XML")
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        result = _invoke_build(runner, tmp_path, spec_file=spec_file, data_dir=data_dir)

        assert result.exit_code != 0
        output = result.output.lower()
        assert "spec file error" in output, result.output
        assert "xml parsing error" in output, result.output

    def test_build_with_nonexistent_data_dir(self, tmp_path):
        """A non-existent ``--data-dir`` is rejected by Click's own validation.

        The old assertion was ``assert "does not exist" in output or exit_code != 0``
        immediately after ``assert exit_code != 0`` — a tautology that passed on
        any failure whatsoever, including an unrelated crash.
        """
        runner = CliRunner()

        spec_file = tmp_path / "test.xml"
        spec_file.write_text('<?xml version="1.0"?><course><name>Test</name></course>')
        data_dir = tmp_path / "nonexistent_data"

        result = _invoke_build(runner, tmp_path, spec_file=spec_file, data_dir=data_dir)

        # A Click usage error, not a generic failure.
        assert result.exit_code == 2, result.output
        output = result.output.lower()
        assert "--data-dir" in output, result.output
        assert "does not exist" in output, result.output
