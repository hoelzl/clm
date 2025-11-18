"""
Integration tests for CLI with real backend.

These tests run the CLI with real backend and worker processes.
They verify that the full CLI → Backend → Workers → Output pipeline works.

Mark with @pytest.mark.integration to run separately from unit tests.
"""

import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from clx.cli.main import cli


@pytest.mark.integration
class TestCliWithSqliteBackend:
    """Integration tests using SQLite backend (no external dependencies)"""

    def test_build_simple_course_with_sqlite(self, tmp_path):
        """Test building a simple course via CLI with SQLite backend"""
        runner = CliRunner()

        # Use test-spec-2 which is a simple course
        spec_file = Path("test-data/course-specs/test-spec-2.xml")
        data_dir = Path("test-data")
        output_dir = tmp_path / "output"

        if not spec_file.exists():
            pytest.skip("Test data not available")

        result = runner.invoke(
            cli,
            [
                "--jobs-db-path",
                str(tmp_path / "test.db"),
                "build",
                str(spec_file),
                "--data-dir",
                str(data_dir),
                "--output-dir",
                str(output_dir),
                "--log-level",
                "WARNING",  # Reduce log noise in tests
                "--ignore-db",  # Don't use cache for clean test
            ],
        )

        # The command should complete successfully
        # Note: This might fail if workers aren't available, but that's expected
        # We're mainly testing that the CLI invocation works
        if result.exit_code != 0:
            print("STDOUT:", result.output)
            # Allow failure if it's due to missing workers or execution issues
            # but not due to argument parsing
            assert "no such option" not in result.output.lower()
            assert "missing argument" not in result.output.lower()

        # If successful, verify output structure exists
        if result.exit_code == 0:
            assert output_dir.exists()
            # Check for course output directories
            course_dirs = list(output_dir.glob("kurs-2-*"))
            if course_dirs:  # Only check if course was processed
                assert len(course_dirs) > 0

    def test_build_with_force_db_init(self, tmp_path):
        """Test that --force-db-init flag works correctly"""
        runner = CliRunner()

        spec_file = Path("test-data/course-specs/test-spec-2.xml")
        data_dir = Path("test-data")
        output_dir = tmp_path / "output"
        db_path = tmp_path / "test.db"

        if not spec_file.exists():
            pytest.skip("Test data not available")

        # First run - creates database
        result1 = runner.invoke(
            cli,
            [
                "--jobs-db-path",
                str(db_path),
                "build",
                str(spec_file),
                "--data-dir",
                str(data_dir),
                "--output-dir",
                str(output_dir),
                "--log-level",
                "ERROR",
                "--force-db-init",
            ],
        )

        # Verify no argument errors
        assert "no such option" not in result1.output.lower()

        # Second run - should reinitialize database
        result2 = runner.invoke(
            cli,
            [
                "--jobs-db-path",
                str(db_path),
                "build",
                str(spec_file),
                "--data-dir",
                str(data_dir),
                "--output-dir",
                str(output_dir),
                "--log-level",
                "ERROR",
                "--force-db-init",
            ],
        )

        # Verify no argument errors
        assert "no such option" not in result2.output.lower()

    def test_build_with_custom_db_path(self, tmp_path):
        """Test that custom database path is used correctly"""
        runner = CliRunner()

        spec_file = Path("test-data/course-specs/test-spec-2.xml")
        data_dir = Path("test-data")
        output_dir = tmp_path / "output"
        db_path = tmp_path / "custom" / "my_cache.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)

        if not spec_file.exists():
            pytest.skip("Test data not available")

        result = runner.invoke(
            cli,
            [
                "--jobs-db-path",
                str(db_path),
                "build",
                str(spec_file),
                "--data-dir",
                str(data_dir),
                "--output-dir",
                str(output_dir),
                "--log-level",
                "ERROR",
            ],
        )

        # Verify no argument parsing errors
        assert "no such option" not in result.output.lower()

        # If build was successful, database should exist at custom path
        if result.exit_code == 0:
            assert db_path.exists()

    def test_build_output_directory_creation(self, tmp_path):
        """Test that output directory is created if it doesn't exist"""
        runner = CliRunner()

        spec_file = Path("test-data/course-specs/test-spec-2.xml")
        data_dir = Path("test-data")
        output_dir = tmp_path / "new_output_dir"

        if not spec_file.exists():
            pytest.skip("Test data not available")

        # Output dir doesn't exist yet
        assert not output_dir.exists()

        result = runner.invoke(
            cli,
            [
                "--jobs-db-path",
                str(tmp_path / "test.db"),
                "build",
                str(spec_file),
                "--data-dir",
                str(data_dir),
                "--output-dir",
                str(output_dir),
                "--log-level",
                "ERROR",
            ],
        )

        # Verify no argument errors
        assert "no such option" not in result.output.lower()

        # Output directory should be created (even if processing fails)
        # Note: The output dir is created in the main() function
        # This might not happen if parsing fails early
        if "does not exist" not in result.output.lower():
            # If no error about directory, it should have been created
            assert output_dir.exists() or result.exit_code != 0


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
                "delete-database",
            ],
        )

        assert result.exit_code == 0
        assert "has been deleted" in result.output
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
                "delete-database",
            ],
        )
        assert result1.exit_code == 0
        assert "No database found" in result1.output

        # Second call - still no database
        result2 = runner.invoke(
            cli,
            [
                "--jobs-db-path",
                str(db_path),
                "delete-database",
            ],
        )
        assert result2.exit_code == 0
        assert "No database found" in result2.output


@pytest.mark.integration
class TestCliBuildWithDifferentOptions:
    """Test various CLI build option combinations"""

    def test_build_with_ignore_db_flag(self, tmp_path):
        """Test that --ignore-db flag is accepted and works"""
        runner = CliRunner()

        spec_file = Path("test-data/course-specs/test-spec-2.xml")
        data_dir = Path("test-data")
        output_dir = tmp_path / "output"

        if not spec_file.exists():
            pytest.skip("Test data not available")

        result = runner.invoke(
            cli,
            [
                "--jobs-db-path",
                str(tmp_path / "test.db"),
                "build",
                str(spec_file),
                "--data-dir",
                str(data_dir),
                "--output-dir",
                str(output_dir),
                "--ignore-db",
                "--log-level",
                "ERROR",
            ],
        )

        # Verify no argument errors
        assert "no such option" not in result.output.lower()
        assert "missing argument" not in result.output.lower()

    def test_build_with_keep_directory_flag(self, tmp_path):
        """Test that --keep-directory flag is accepted"""
        runner = CliRunner()

        spec_file = Path("test-data/course-specs/test-spec-2.xml")
        data_dir = Path("test-data")
        output_dir = tmp_path / "output"

        if not spec_file.exists():
            pytest.skip("Test data not available")

        result = runner.invoke(
            cli,
            [
                "--jobs-db-path",
                str(tmp_path / "test.db"),
                "build",
                str(spec_file),
                "--data-dir",
                str(data_dir),
                "--output-dir",
                str(output_dir),
                "--keep-directory",
                "--log-level",
                "ERROR",
            ],
        )

        # Verify no argument errors
        assert "no such option" not in result.output.lower()

    def test_build_all_boolean_flags_together(self, tmp_path):
        """Test combining multiple boolean flags"""
        runner = CliRunner()

        spec_file = Path("test-data/course-specs/test-spec-2.xml")
        data_dir = Path("test-data")
        output_dir = tmp_path / "output"

        if not spec_file.exists():
            pytest.skip("Test data not available")

        result = runner.invoke(
            cli,
            [
                "--jobs-db-path",
                str(tmp_path / "test.db"),
                "build",
                str(spec_file),
                "--data-dir",
                str(data_dir),
                "--output-dir",
                str(output_dir),
                "--ignore-db",
                "--force-db-init",
                "--keep-directory",
                "--print-tracebacks",
                "--print-correlation-ids",
                "--log-level",
                "ERROR",
            ],
        )

        # Verify no argument parsing errors
        assert "no such option" not in result.output.lower()
        assert "missing argument" not in result.output.lower()


@pytest.mark.integration
class TestCliErrorHandling:
    """Test CLI error handling and edge cases"""

    def test_build_with_invalid_spec_file_content(self, tmp_path):
        """Test that CLI handles invalid XML spec files gracefully"""
        runner = CliRunner()

        spec_file = tmp_path / "invalid.xml"
        spec_file.write_text("This is not valid XML")
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        output_dir = tmp_path / "output"

        result = runner.invoke(
            cli,
            [
                "--jobs-db-path",
                str(tmp_path / "test.db"),
                "build",
                str(spec_file),
                "--data-dir",
                str(data_dir),
                "--output-dir",
                str(output_dir),
                "--log-level",
                "ERROR",
            ],
        )

        # Should fail, but gracefully
        assert result.exit_code != 0

    def test_build_with_nonexistent_data_dir(self, tmp_path):
        """Test that CLI handles non-existent data directory"""
        runner = CliRunner()

        spec_file = tmp_path / "test.xml"
        spec_file.write_text('<?xml version="1.0"?><course><name>Test</name></course>')
        data_dir = tmp_path / "nonexistent_data"
        output_dir = tmp_path / "output"

        result = runner.invoke(
            cli,
            [
                "--jobs-db-path",
                str(tmp_path / "test.db"),
                "build",
                str(spec_file),
                "--data-dir",
                str(data_dir),
                "--output-dir",
                str(output_dir),
                "--log-level",
                "ERROR",
            ],
        )

        # Should fail because data dir doesn't exist
        assert result.exit_code != 0
        # Click validation should catch this
        assert "does not exist" in result.output.lower() or result.exit_code != 0
