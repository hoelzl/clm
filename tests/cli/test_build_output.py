"""Tests for build output formatting and reporting components."""

import pytest
from clx.cli.build_data_classes import BuildError, BuildWarning, BuildSummary
from clx.cli.error_categorizer import ErrorCategorizer
from clx.cli.output_formatter import DefaultOutputFormatter, QuietOutputFormatter
from clx.cli.build_reporter import BuildReporter


class TestBuildDataClasses:
    """Test data classes for build reporting."""

    def test_build_error_creation(self):
        """Test creating a build error."""
        error = BuildError(
            error_type="user",
            category="notebook_compilation",
            severity="error",
            file_path="test.py",
            message="SyntaxError",
            actionable_guidance="Fix the syntax error",
        )
        assert error.error_type == "user"
        assert error.category == "notebook_compilation"
        assert error.severity == "error"
        assert str(error)  # Should have string representation

    def test_build_warning_creation(self):
        """Test creating a build warning."""
        warning = BuildWarning(
            category="duplicate_topic_id",
            message="Duplicate ID found",
            severity="high",
        )
        assert warning.category == "duplicate_topic_id"
        assert warning.severity == "high"
        assert str(warning)  # Should have string representation

    def test_build_summary_creation(self):
        """Test creating a build summary."""
        summary = BuildSummary(
            duration=120.5,
            total_files=100,
        )
        assert summary.duration == 120.5
        assert summary.total_files == 100
        assert summary.successful_files == 100
        assert not summary.has_errors()
        assert not summary.has_fatal_errors()

    def test_build_summary_with_errors(self):
        """Test build summary with errors."""
        error = BuildError(
            error_type="user",
            category="notebook_compilation",
            severity="error",
            file_path="test.py",
            message="Error",
            actionable_guidance="Fix it",
        )
        summary = BuildSummary(
            duration=120.5,
            total_files=100,
            errors=[error],
        )
        assert summary.has_errors()
        assert summary.failed_files == 1
        assert summary.successful_files == 99


class TestErrorCategorizer:
    """Test error categorization logic."""

    def test_categorize_notebook_syntax_error(self):
        """Test categorizing notebook syntax error as user error."""
        error = ErrorCategorizer.categorize_job_error(
            job_type="notebook",
            input_file="test.py",
            error_message="SyntaxError: invalid syntax",
            job_payload={},
        )
        assert error.error_type == "user"
        assert error.category == "notebook_compilation"
        assert error.severity == "error"

    def test_categorize_plantuml_missing_jar(self):
        """Test categorizing missing PlantUML JAR as configuration error."""
        error = ErrorCategorizer.categorize_job_error(
            job_type="plantuml",
            input_file="diagram.puml",
            error_message="PLANTUML_JAR environment variable not set",
            job_payload={},
        )
        assert error.error_type == "configuration"
        assert error.category == "missing_plantuml"
        assert error.severity == "error"

    def test_categorize_drawio_missing_executable(self):
        """Test categorizing missing DrawIO executable as configuration error."""
        error = ErrorCategorizer.categorize_job_error(
            job_type="drawio",
            input_file="diagram.drawio",
            error_message="DrawIO executable not found",
            job_payload={},
        )
        assert error.error_type == "configuration"
        assert error.category == "missing_drawio"
        assert error.severity == "error"

    def test_categorize_no_workers_error(self):
        """Test categorizing no workers error as infrastructure error."""
        error = ErrorCategorizer.categorize_no_workers_error("notebook")
        assert error.error_type == "infrastructure"
        assert error.category == "no_workers"
        assert error.severity == "fatal"


class TestOutputFormatter:
    """Test output formatting."""

    def test_default_formatter_creation(self):
        """Test creating default output formatter."""
        formatter = DefaultOutputFormatter(show_progress=True, use_color=True)
        assert formatter.show_progress is True
        assert formatter.use_color is True

    def test_quiet_formatter_creation(self):
        """Test creating quiet output formatter."""
        formatter = QuietOutputFormatter()
        assert formatter is not None

    def test_formatter_error_display_logic(self):
        """Test error display logic in formatters."""
        default_formatter = DefaultOutputFormatter()
        quiet_formatter = QuietOutputFormatter()

        error = BuildError(
            error_type="user",
            category="test",
            severity="error",
            file_path="test.py",
            message="Test error",
            actionable_guidance="Fix it",
        )

        # Default formatter should show errors
        assert default_formatter.should_show_error(error)

        # Quiet formatter should show errors
        assert quiet_formatter.should_show_error(error)


class TestBuildReporter:
    """Test build reporter."""

    def test_build_reporter_creation(self):
        """Test creating build reporter."""
        formatter = QuietOutputFormatter()
        reporter = BuildReporter(output_formatter=formatter)
        assert reporter is not None
        assert reporter.errors == []
        assert reporter.warnings == []

    def test_build_reporter_error_collection(self):
        """Test error collection in build reporter."""
        formatter = QuietOutputFormatter()
        reporter = BuildReporter(output_formatter=formatter)

        error = BuildError(
            error_type="user",
            category="test",
            severity="error",
            file_path="test.py",
            message="Test error",
            actionable_guidance="Fix it",
        )

        reporter.report_error(error)
        assert len(reporter.errors) == 1
        assert reporter.errors[0] == error

    def test_build_reporter_warning_collection(self):
        """Test warning collection in build reporter."""
        formatter = QuietOutputFormatter()
        reporter = BuildReporter(output_formatter=formatter)

        warning = BuildWarning(
            category="test",
            message="Test warning",
            severity="high",
        )

        reporter.report_warning(warning)
        assert len(reporter.warnings) == 1
        assert reporter.warnings[0] == warning
