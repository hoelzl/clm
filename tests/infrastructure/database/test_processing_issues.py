"""Tests for processing issues storage and retrieval in db_operations."""

import tempfile
from pathlib import Path

import pytest

from clm.cli.build_data_classes import BuildError, BuildWarning
from clm.infrastructure.database.db_operations import DatabaseManager


@pytest.fixture
def db_manager():
    """Create a temporary database manager for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    manager = DatabaseManager(db_path, force_init=True)
    manager.__enter__()
    yield manager
    manager.__exit__(None, None, None)
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture
def sample_error():
    """Create a sample BuildError for testing."""
    return BuildError(
        error_type="user",
        category="notebook_compilation",
        severity="error",
        file_path="test_notebook.py",
        message="NameError: undefined_variable",
        actionable_guidance="Fix the undefined variable",
        job_id=123,
        correlation_id="corr-123",
        details={"cell_number": 5, "line_number": 10},
    )


@pytest.fixture
def sample_warning():
    """Create a sample BuildWarning for testing."""
    return BuildWarning(
        category="slow_processing",
        message="Notebook took 30s to process",
        severity="medium",
        file_path="test_notebook.py",
    )


class TestBuildErrorSerialization:
    """Tests for BuildError serialization."""

    def test_to_json_creates_valid_json(self, sample_error):
        json_str = sample_error.to_json()
        assert isinstance(json_str, str)
        assert "NameError" in json_str
        assert "notebook_compilation" in json_str

    def test_from_json_restores_error(self, sample_error):
        json_str = sample_error.to_json()
        restored = BuildError.from_json(json_str)

        assert restored.error_type == sample_error.error_type
        assert restored.category == sample_error.category
        assert restored.severity == sample_error.severity
        assert restored.file_path == sample_error.file_path
        assert restored.message == sample_error.message
        assert restored.actionable_guidance == sample_error.actionable_guidance
        assert restored.job_id == sample_error.job_id
        assert restored.correlation_id == sample_error.correlation_id
        assert restored.details == sample_error.details

    def test_roundtrip_preserves_all_fields(self, sample_error):
        json_str = sample_error.to_json()
        restored = BuildError.from_json(json_str)
        json_str2 = restored.to_json()
        assert json_str == json_str2


class TestBuildWarningSerialization:
    """Tests for BuildWarning serialization."""

    def test_to_json_creates_valid_json(self, sample_warning):
        json_str = sample_warning.to_json()
        assert isinstance(json_str, str)
        assert "slow_processing" in json_str

    def test_from_json_restores_warning(self, sample_warning):
        json_str = sample_warning.to_json()
        restored = BuildWarning.from_json(json_str)

        assert restored.category == sample_warning.category
        assert restored.message == sample_warning.message
        assert restored.severity == sample_warning.severity
        assert restored.file_path == sample_warning.file_path

    def test_roundtrip_preserves_all_fields(self, sample_warning):
        json_str = sample_warning.to_json()
        restored = BuildWarning.from_json(json_str)
        json_str2 = restored.to_json()
        assert json_str == json_str2


class TestProcessingIssuesTable:
    """Tests for the processing_issues table creation."""

    def test_processing_issues_table_exists(self, db_manager):
        cursor = db_manager.conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='processing_issues'"
        )
        result = cursor.fetchone()
        assert result is not None
        assert result[0] == "processing_issues"

    def test_processing_issues_index_exists(self, db_manager):
        cursor = db_manager.conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_processing_issues_lookup'"
        )
        result = cursor.fetchone()
        assert result is not None


class TestStoreError:
    """Tests for storing errors."""

    def test_store_error_creates_entry(self, db_manager, sample_error):
        db_manager.store_error(
            file_path="test.py",
            content_hash="abc123",
            output_metadata="('participant', 'python', 'en', 'notebook')",
            error=sample_error,
        )

        cursor = db_manager.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM processing_issues WHERE issue_type = 'error'")
        count = cursor.fetchone()[0]
        assert count == 1

    def test_store_error_replaces_existing_error(self, db_manager, sample_error):
        # Store first error
        db_manager.store_error(
            file_path="test.py",
            content_hash="abc123",
            output_metadata="('participant', 'python', 'en', 'notebook')",
            error=sample_error,
        )

        # Store second error for same file/hash/metadata
        error2 = BuildError(
            error_type="user",
            category="syntax_error",
            severity="error",
            file_path="test.py",
            message="Different error",
            actionable_guidance="Fix it",
        )
        db_manager.store_error(
            file_path="test.py",
            content_hash="abc123",
            output_metadata="('participant', 'python', 'en', 'notebook')",
            error=error2,
        )

        # Should only have one error (replaced)
        cursor = db_manager.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM processing_issues WHERE issue_type = 'error'")
        count = cursor.fetchone()[0]
        assert count == 1

        # Verify it's the new error
        errors, _ = db_manager.get_issues(
            "test.py", "abc123", "('participant', 'python', 'en', 'notebook')"
        )
        assert len(errors) == 1
        assert errors[0].message == "Different error"

    def test_store_error_different_output_metadata(self, db_manager, sample_error):
        # Store error for one output
        db_manager.store_error(
            file_path="test.py",
            content_hash="abc123",
            output_metadata="('participant', 'python', 'en', 'notebook')",
            error=sample_error,
        )

        # Store error for different output
        db_manager.store_error(
            file_path="test.py",
            content_hash="abc123",
            output_metadata="('speaker', 'python', 'en', 'notebook')",
            error=sample_error,
        )

        cursor = db_manager.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM processing_issues WHERE issue_type = 'error'")
        count = cursor.fetchone()[0]
        assert count == 2


class TestStoreWarning:
    """Tests for storing warnings."""

    def test_store_warning_creates_entry(self, db_manager, sample_warning):
        db_manager.store_warning(
            file_path="test.py",
            content_hash="abc123",
            output_metadata="('participant', 'python', 'en', 'notebook')",
            warning=sample_warning,
        )

        cursor = db_manager.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM processing_issues WHERE issue_type = 'warning'")
        count = cursor.fetchone()[0]
        assert count == 1

    def test_store_multiple_warnings(self, db_manager, sample_warning):
        # Store multiple warnings (warnings don't replace, they accumulate)
        for i in range(3):
            warning = BuildWarning(
                category=f"warning_{i}",
                message=f"Warning message {i}",
                severity="low",
            )
            db_manager.store_warning(
                file_path="test.py",
                content_hash="abc123",
                output_metadata="('participant', 'python', 'en', 'notebook')",
                warning=warning,
            )

        cursor = db_manager.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM processing_issues WHERE issue_type = 'warning'")
        count = cursor.fetchone()[0]
        assert count == 3


class TestGetIssues:
    """Tests for retrieving issues."""

    def test_get_issues_returns_empty_when_none(self, db_manager):
        errors, warnings = db_manager.get_issues("nonexistent.py", "hash", "metadata")
        assert errors == []
        assert warnings == []

    def test_get_issues_returns_stored_error(self, db_manager, sample_error):
        db_manager.store_error(
            file_path="test.py",
            content_hash="abc123",
            output_metadata="metadata1",
            error=sample_error,
        )

        errors, warnings = db_manager.get_issues("test.py", "abc123", "metadata1")
        assert len(errors) == 1
        assert len(warnings) == 0
        assert errors[0].message == sample_error.message

    def test_get_issues_returns_stored_warning(self, db_manager, sample_warning):
        db_manager.store_warning(
            file_path="test.py",
            content_hash="abc123",
            output_metadata="metadata1",
            warning=sample_warning,
        )

        errors, warnings = db_manager.get_issues("test.py", "abc123", "metadata1")
        assert len(errors) == 0
        assert len(warnings) == 1
        assert warnings[0].message == sample_warning.message

    def test_get_issues_returns_both_errors_and_warnings(
        self, db_manager, sample_error, sample_warning
    ):
        db_manager.store_error(
            file_path="test.py",
            content_hash="abc123",
            output_metadata="metadata1",
            error=sample_error,
        )
        db_manager.store_warning(
            file_path="test.py",
            content_hash="abc123",
            output_metadata="metadata1",
            warning=sample_warning,
        )

        errors, warnings = db_manager.get_issues("test.py", "abc123", "metadata1")
        assert len(errors) == 1
        assert len(warnings) == 1

    def test_get_issues_filters_by_content_hash(self, db_manager, sample_error):
        db_manager.store_error(
            file_path="test.py",
            content_hash="hash1",
            output_metadata="metadata1",
            error=sample_error,
        )

        # Different hash should return empty
        errors, warnings = db_manager.get_issues("test.py", "hash2", "metadata1")
        assert len(errors) == 0

        # Same hash should return the error
        errors, warnings = db_manager.get_issues("test.py", "hash1", "metadata1")
        assert len(errors) == 1


class TestClearIssues:
    """Tests for clearing issues."""

    def test_clear_issues_removes_all_for_file(self, db_manager, sample_error, sample_warning):
        db_manager.store_error(
            file_path="test.py",
            content_hash="abc123",
            output_metadata="metadata1",
            error=sample_error,
        )
        db_manager.store_warning(
            file_path="test.py",
            content_hash="abc123",
            output_metadata="metadata1",
            warning=sample_warning,
        )

        db_manager.clear_issues("test.py", "abc123", "metadata1")

        errors, warnings = db_manager.get_issues("test.py", "abc123", "metadata1")
        assert len(errors) == 0
        assert len(warnings) == 0

    def test_clear_issues_only_clears_matching(self, db_manager, sample_error):
        db_manager.store_error(
            file_path="test.py",
            content_hash="abc123",
            output_metadata="metadata1",
            error=sample_error,
        )
        db_manager.store_error(
            file_path="test.py",
            content_hash="abc123",
            output_metadata="metadata2",
            error=sample_error,
        )

        db_manager.clear_issues("test.py", "abc123", "metadata1")

        # metadata1 should be cleared
        errors1, _ = db_manager.get_issues("test.py", "abc123", "metadata1")
        assert len(errors1) == 0

        # metadata2 should still exist
        errors2, _ = db_manager.get_issues("test.py", "abc123", "metadata2")
        assert len(errors2) == 1
