"""
Integration tests for error and warning reporting.

These tests verify that:
1. Errors are stored in the database when jobs fail
2. Cached errors are reported when using cached results
3. Duplicate file warnings are detected and reported

Mark with @pytest.mark.integration to run separately from unit tests.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clx.cli.build_data_classes import BuildError, BuildWarning
from clx.cli.build_reporter import BuildReporter
from clx.cli.output_formatter import QuietOutputFormatter
from clx.infrastructure.backends.sqlite_backend import SqliteBackend
from clx.infrastructure.database.db_operations import DatabaseManager
from clx.infrastructure.database.job_queue import JobQueue
from clx.infrastructure.messaging.notebook_classes import NotebookPayload


class TestSqliteBackendErrorStorage:
    """Tests for error storage in SqliteBackend."""

    @pytest.fixture
    def backend_setup(self, tmp_path):
        """Create a SqliteBackend with all dependencies for testing."""
        db_path = tmp_path / "test_jobs.db"
        cache_db_path = tmp_path / "test_cache.db"
        workspace_path = tmp_path / "workspace"
        workspace_path.mkdir()

        db_manager = DatabaseManager(str(cache_db_path), force_init=True)
        db_manager.__enter__()

        formatter = QuietOutputFormatter()
        build_reporter = BuildReporter(formatter)

        backend = SqliteBackend(
            db_path=db_path,
            poll_interval=0.1,
            ignore_db=False,
            db_manager=db_manager,
            workspace_path=workspace_path,
            build_reporter=build_reporter,
            enable_progress_tracking=False,
            skip_worker_check=True,
        )

        yield {
            "backend": backend,
            "job_queue": backend.job_queue,
            "db_manager": db_manager,
            "build_reporter": build_reporter,
            "workspace_path": workspace_path,
            "db_path": db_path,
        }

        db_manager.__exit__(None, None, None)

    def test_get_output_metadata_notebook(self, backend_setup):
        """Test that _get_output_metadata correctly generates metadata for notebooks."""
        backend = backend_setup["backend"]

        payload_dict = {
            "kind": "participant",
            "prog_lang": "python",
            "language": "en",
            "format": "notebook",
        }

        result = backend._get_output_metadata("notebook", payload_dict)
        assert result == "('participant', 'python', 'en', 'notebook')"

    def test_get_output_metadata_plantuml(self, backend_setup):
        """Test that _get_output_metadata correctly generates metadata for plantuml."""
        backend = backend_setup["backend"]

        payload_dict = {"output_format": "png"}
        result = backend._get_output_metadata("plantuml", payload_dict)
        assert result == "png"

    def test_get_output_metadata_drawio(self, backend_setup):
        """Test that _get_output_metadata correctly generates metadata for drawio."""
        backend = backend_setup["backend"]

        payload_dict = {"output_format": "svg"}
        result = backend._get_output_metadata("drawio", payload_dict)
        assert result == "svg"

    def test_report_cached_issues_reports_errors(self, backend_setup):
        """Test that _report_cached_issues reports stored errors."""
        backend = backend_setup["backend"]
        db_manager = backend_setup["db_manager"]
        build_reporter = backend_setup["build_reporter"]

        # Store an error in the database
        error = BuildError(
            error_type="user",
            category="notebook_compilation",
            severity="error",
            file_path="test.py",
            message="Test error",
            actionable_guidance="Fix it",
        )
        db_manager.store_error("test.py", "hash123", "metadata1", error)

        # Call _report_cached_issues
        backend._report_cached_issues("test.py", "hash123", "metadata1")

        # Verify the error was reported
        assert len(build_reporter.errors) == 1
        assert build_reporter.errors[0].message == "Test error"
        assert build_reporter.errors[0].details.get("from_cache") is True

    def test_report_cached_issues_reports_warnings(self, backend_setup):
        """Test that _report_cached_issues reports stored warnings."""
        backend = backend_setup["backend"]
        db_manager = backend_setup["db_manager"]
        build_reporter = backend_setup["build_reporter"]

        # Store a warning in the database
        warning = BuildWarning(
            category="test_warning",
            message="Test warning",
            severity="medium",
        )
        db_manager.store_warning("test.py", "hash123", "metadata1", warning)

        # Call _report_cached_issues
        backend._report_cached_issues("test.py", "hash123", "metadata1")

        # Verify the warning was reported
        assert len(build_reporter.warnings) == 1
        assert build_reporter.warnings[0].message == "Test warning"

    def test_report_cached_issues_no_db_manager(self, backend_setup):
        """Test that _report_cached_issues handles missing db_manager gracefully."""
        backend = backend_setup["backend"]
        backend.db_manager = None

        # Should not raise an exception
        backend._report_cached_issues("test.py", "hash123", "metadata1")

    def test_report_cached_issues_no_build_reporter(self, backend_setup):
        """Test that _report_cached_issues handles missing build_reporter gracefully."""
        backend = backend_setup["backend"]
        backend.build_reporter = None

        # Should not raise an exception
        backend._report_cached_issues("test.py", "hash123", "metadata1")


class TestDuplicateFileDetection:
    """Tests for duplicate file name detection."""

    @pytest.fixture
    def mock_course(self):
        """Create a mock course with duplicate detection capability."""
        from clx.core.course import Course
        from clx.core.course_spec import CourseSpec

        # We'll test the detect_duplicate_output_files method directly
        return None  # We'll create this in individual tests

    def test_report_duplicate_file_warnings_called(self):
        """Test that _report_duplicate_file_warnings is called during build."""
        from clx.cli.build_reporter import BuildReporter
        from clx.cli.main import _report_duplicate_file_warnings
        from clx.cli.output_formatter import QuietOutputFormatter

        # Create mock course with duplicates
        mock_course = MagicMock()
        mock_course.detect_duplicate_output_files.return_value = [
            {
                "output_name": "01 Duplicate.html",
                "output_dir": "/output/en",
                "language": "en",
                "format": "html",
                "kind": "participant",
                "files": [Path("file1.py"), Path("file2.py")],
            }
        ]

        formatter = QuietOutputFormatter()
        build_reporter = BuildReporter(formatter)

        _report_duplicate_file_warnings(mock_course, build_reporter)

        # Verify warning was reported
        assert len(build_reporter.warnings) == 1
        assert "Duplicate output file" in build_reporter.warnings[0].message
        assert build_reporter.warnings[0].category == "duplicate_output_file"
        assert build_reporter.warnings[0].severity == "high"

    def test_report_duplicate_file_warnings_no_duplicates(self):
        """Test that no warnings are reported when there are no duplicates."""
        from clx.cli.build_reporter import BuildReporter
        from clx.cli.main import _report_duplicate_file_warnings
        from clx.cli.output_formatter import QuietOutputFormatter

        mock_course = MagicMock()
        mock_course.detect_duplicate_output_files.return_value = []

        formatter = QuietOutputFormatter()
        build_reporter = BuildReporter(formatter)

        _report_duplicate_file_warnings(mock_course, build_reporter)

        # Verify no warnings were reported
        assert len(build_reporter.warnings) == 0

    def test_report_duplicate_file_warnings_handles_exception(self):
        """Test that exceptions in duplicate detection are handled gracefully."""
        from clx.cli.build_reporter import BuildReporter
        from clx.cli.main import _report_duplicate_file_warnings
        from clx.cli.output_formatter import QuietOutputFormatter

        mock_course = MagicMock()
        mock_course.detect_duplicate_output_files.side_effect = Exception("Test error")

        formatter = QuietOutputFormatter()
        build_reporter = BuildReporter(formatter)

        # Should not raise an exception
        _report_duplicate_file_warnings(mock_course, build_reporter)

        # Verify no warnings were reported (exception was caught)
        assert len(build_reporter.warnings) == 0


@pytest.mark.integration
class TestCachedErrorReportingIntegration:
    """Integration tests for cached error reporting flow."""

    def test_error_stored_on_job_failure(self, tmp_path):
        """Test that errors are stored in the cache database when jobs fail."""
        from clx.cli.build_data_classes import BuildError
        from clx.cli.error_categorizer import ErrorCategorizer

        db_path = tmp_path / "test_jobs.db"
        cache_db_path = tmp_path / "test_cache.db"

        db_manager = DatabaseManager(str(cache_db_path), force_init=True)
        db_manager.__enter__()

        try:
            formatter = QuietOutputFormatter()
            build_reporter = BuildReporter(formatter)

            backend = SqliteBackend(
                db_path=db_path,
                poll_interval=0.1,
                ignore_db=False,
                db_manager=db_manager,
                workspace_path=tmp_path,
                build_reporter=build_reporter,
                enable_progress_tracking=False,
                skip_worker_check=True,
            )

            # Simulate the error storage that happens in wait_for_completion
            # when a job fails
            error = BuildError(
                error_type="user",
                category="notebook_compilation",
                severity="error",
                file_path="test_notebook.py",
                message="NameError: undefined_variable",
                actionable_guidance="Fix the undefined variable",
            )

            # Store the error
            db_manager.store_error(
                file_path="test_notebook.py",
                content_hash="abc123",
                output_metadata="('participant', 'python', 'en', 'notebook')",
                error=error,
            )

            # Verify the error is stored
            errors, _ = db_manager.get_issues(
                "test_notebook.py",
                "abc123",
                "('participant', 'python', 'en', 'notebook')",
            )
            assert len(errors) == 1
            assert errors[0].message == "NameError: undefined_variable"

        finally:
            db_manager.__exit__(None, None, None)

    def test_cached_errors_reported_on_cache_hit(self, tmp_path):
        """Test that stored errors are reported when cache is hit."""
        db_path = tmp_path / "test_jobs.db"
        cache_db_path = tmp_path / "test_cache.db"

        db_manager = DatabaseManager(str(cache_db_path), force_init=True)
        db_manager.__enter__()

        try:
            formatter = QuietOutputFormatter()
            build_reporter = BuildReporter(formatter)

            backend = SqliteBackend(
                db_path=db_path,
                poll_interval=0.1,
                ignore_db=False,
                db_manager=db_manager,
                workspace_path=tmp_path,
                build_reporter=build_reporter,
                enable_progress_tracking=False,
                skip_worker_check=True,
            )

            # Store an error in the cache
            error = BuildError(
                error_type="user",
                category="notebook_compilation",
                severity="error",
                file_path="cached_notebook.py",
                message="Cached error message",
                actionable_guidance="This was cached",
            )
            db_manager.store_error(
                "cached_notebook.py",
                "hash456",
                "('participant', 'python', 'en', 'notebook')",
                error,
            )

            # Simulate a cache hit by calling _report_cached_issues directly
            backend._report_cached_issues(
                "cached_notebook.py",
                "hash456",
                "('participant', 'python', 'en', 'notebook')",
            )

            # Verify the error was reported
            assert len(build_reporter.errors) == 1
            assert build_reporter.errors[0].message == "Cached error message"
            assert build_reporter.errors[0].details.get("from_cache") is True

        finally:
            db_manager.__exit__(None, None, None)

    def test_multiple_cached_errors_for_same_file(self, tmp_path):
        """Test handling of multiple output formats from the same source file."""
        db_path = tmp_path / "test_jobs.db"
        cache_db_path = tmp_path / "test_cache.db"

        db_manager = DatabaseManager(str(cache_db_path), force_init=True)
        db_manager.__enter__()

        try:
            formatter = QuietOutputFormatter()
            build_reporter = BuildReporter(formatter)

            backend = SqliteBackend(
                db_path=db_path,
                poll_interval=0.1,
                ignore_db=False,
                db_manager=db_manager,
                workspace_path=tmp_path,
                build_reporter=build_reporter,
                enable_progress_tracking=False,
                skip_worker_check=True,
            )

            # Store errors for different output formats
            for i, output_metadata in enumerate(
                [
                    "('participant', 'python', 'en', 'notebook')",
                    "('speaker', 'python', 'en', 'notebook')",
                    "('participant', 'python', 'de', 'notebook')",
                ]
            ):
                error = BuildError(
                    error_type="user",
                    category="notebook_compilation",
                    severity="error",
                    file_path="multi_output.py",
                    message=f"Error for output {i}",
                    actionable_guidance="Fix it",
                )
                db_manager.store_error("multi_output.py", "samehash", output_metadata, error)

            # Report cached issues for each output format
            for output_metadata in [
                "('participant', 'python', 'en', 'notebook')",
                "('speaker', 'python', 'en', 'notebook')",
                "('participant', 'python', 'de', 'notebook')",
            ]:
                backend._report_cached_issues("multi_output.py", "samehash", output_metadata)

            # Verify all errors were reported
            assert len(build_reporter.errors) == 3

        finally:
            db_manager.__exit__(None, None, None)
