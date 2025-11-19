"""Tests for build output formatting and reporting components."""

import json
from datetime import datetime

import pytest

from clx.cli.build_data_classes import BuildError, BuildSummary, BuildWarning
from clx.cli.build_reporter import BuildReporter
from clx.cli.error_categorizer import ErrorCategorizer
from clx.cli.output_formatter import (
    DefaultOutputFormatter,
    JSONOutputFormatter,
    QuietOutputFormatter,
)


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


class TestEnhancedErrorParsing:
    """Test enhanced error parsing capabilities."""

    def test_parse_notebook_error_with_cell_number(self):
        """Test parsing cell number from error message."""
        error_msg = "SyntaxError in cell #5: invalid syntax"
        traceback_msg = "  File 'test.ipynb', line 2"

        details = ErrorCategorizer._parse_notebook_error(error_msg, traceback_msg)

        assert details.get("cell_number") == 5
        assert details.get("error_class") == "SyntaxError"

    def test_parse_notebook_error_with_line_number(self):
        """Test parsing line number from error message."""
        error_msg = "NameError at line 3: name 'x' is not defined"

        details = ErrorCategorizer._parse_notebook_error(error_msg)

        assert details.get("line_number") == 3
        assert details.get("error_class") == "NameError"

    def test_parse_notebook_error_with_code_snippet(self):
        """Test extracting code snippet from traceback."""
        error_msg = "SyntaxError: invalid syntax"
        traceback_msg = """
  File "test.ipynb", line 2
    1: x = 1
    2: y =
    3: z = 3
SyntaxError: invalid syntax
"""

        details = ErrorCategorizer._parse_notebook_error(error_msg, traceback_msg)

        assert "code_snippet" in details
        assert "x = 1" in details["code_snippet"]
        assert "y =" in details["code_snippet"]

    def test_parse_notebook_error_extracts_file_path(self):
        """Test extracting source file path from error."""
        error_msg = "Error occurred"
        traceback_msg = 'File "/path/to/notebook.ipynb", line 5'

        details = ErrorCategorizer._parse_notebook_error(error_msg, traceback_msg)

        assert details.get("source_file") == "/path/to/notebook.ipynb"

    def test_parse_notebook_error_extracts_short_message(self):
        """Test extracting short error message."""
        error_msg = "ValueError: invalid literal for int() with base 10: 'abc'"

        details = ErrorCategorizer._parse_notebook_error(error_msg)

        assert details.get("error_class") == "ValueError"
        assert "short_message" in details
        assert "invalid literal" in details["short_message"]

    def test_categorize_notebook_with_parsed_details(self):
        """Test that categorized errors include parsed details."""
        error_msg = "NameError in cell #3: name 'undefined_var' is not defined"

        error = ErrorCategorizer.categorize_job_error(
            job_type="notebook",
            input_file="test.ipynb",
            error_message=error_msg,
            job_payload={},
        )

        assert error.error_type == "user"
        assert error.details.get("cell_number") == 3
        assert error.details.get("error_class") == "NameError"


class TestJSONOutputFormatter:
    """Test JSON output formatter for CI/CD integration."""

    def test_json_formatter_creation(self):
        """Test creating JSON output formatter."""
        formatter = JSONOutputFormatter()
        assert formatter is not None
        assert formatter.output_data["status"] == "in_progress"
        assert formatter.output_data["errors"] == []
        assert formatter.output_data["warnings"] == []

    def test_json_formatter_build_start(self):
        """Test recording build start in JSON formatter."""
        formatter = JSONOutputFormatter()
        formatter.show_build_start("Test Course", 10)

        assert formatter.output_data["course_name"] == "Test Course"
        assert formatter.output_data["total_files"] == 10

    def test_json_formatter_stage_tracking(self):
        """Test stage tracking in JSON formatter."""
        formatter = JSONOutputFormatter()
        formatter.show_stage_start("Notebooks", 1, 3, 5)

        assert len(formatter.output_data["stages"]) == 1
        stage = formatter.output_data["stages"][0]
        assert stage["name"] == "Notebooks"
        assert stage["stage_num"] == 1
        assert stage["total_stages"] == 3
        assert stage["num_jobs"] == 5

    def test_json_formatter_never_shows_errors_immediately(self):
        """Test that JSON formatter never shows errors immediately."""
        formatter = JSONOutputFormatter()
        error = BuildError(
            error_type="user",
            category="test",
            severity="error",
            file_path="test.py",
            message="Test error",
            actionable_guidance="Fix it",
        )

        assert not formatter.should_show_error(error)

    def test_json_formatter_never_shows_warnings_immediately(self):
        """Test that JSON formatter never shows warnings immediately."""
        formatter = JSONOutputFormatter()
        warning = BuildWarning(
            category="test",
            message="Test warning",
            severity="high",
        )

        assert not formatter.should_show_warning(warning)

    def test_json_formatter_summary_with_success(self, capsys):
        """Test JSON output with successful build."""
        formatter = JSONOutputFormatter()
        formatter.show_build_start("Test Course", 10)

        summary = BuildSummary(
            duration=120.5,
            total_files=10,
            errors=[],
            warnings=[],
            start_time=datetime(2025, 1, 1, 12, 0, 0),
            end_time=datetime(2025, 1, 1, 12, 2, 0),
        )

        formatter.show_summary(summary)

        # Capture stdout output
        captured = capsys.readouterr()
        output_data = json.loads(captured.out)

        assert output_data["status"] == "success"
        assert output_data["duration_seconds"] == 120.5
        assert output_data["files_processed"] == 10
        assert output_data["files_succeeded"] == 10
        assert output_data["files_failed"] == 0
        assert output_data["error_count"] == 0
        assert output_data["warning_count"] == 0
        assert output_data["start_time"] == "2025-01-01T12:00:00"
        assert output_data["end_time"] == "2025-01-01T12:02:00"

    def test_json_formatter_summary_with_errors(self, capsys):
        """Test JSON output with build errors."""
        formatter = JSONOutputFormatter()
        formatter.show_build_start("Test Course", 10)

        error = BuildError(
            error_type="user",
            category="notebook_compilation",
            severity="error",
            file_path="test.py",
            message="SyntaxError: invalid syntax",
            actionable_guidance="Fix the syntax error",
            job_id=123,
            correlation_id="abc-123",
            details={"cell_number": 5},
        )

        summary = BuildSummary(
            duration=120.5,
            total_files=10,
            errors=[error],
            warnings=[],
        )

        formatter.show_summary(summary)

        # Capture stdout output
        captured = capsys.readouterr()
        output_data = json.loads(captured.out)

        assert output_data["status"] == "failed"
        assert output_data["error_count"] == 1
        assert len(output_data["errors"]) == 1

        error_dict = output_data["errors"][0]
        assert error_dict["error_type"] == "user"
        assert error_dict["category"] == "notebook_compilation"
        assert error_dict["severity"] == "error"
        assert error_dict["file_path"] == "test.py"
        assert error_dict["message"] == "SyntaxError: invalid syntax"
        assert error_dict["actionable_guidance"] == "Fix the syntax error"
        assert error_dict["job_id"] == 123
        assert error_dict["correlation_id"] == "abc-123"
        assert error_dict["details"]["cell_number"] == 5

    def test_json_formatter_summary_with_fatal_errors(self, capsys):
        """Test JSON output with fatal errors."""
        formatter = JSONOutputFormatter()

        fatal_error = BuildError(
            error_type="infrastructure",
            category="no_workers",
            severity="fatal",
            file_path="",
            message="No workers available",
            actionable_guidance="Start workers",
        )

        summary = BuildSummary(
            duration=10.0,
            total_files=10,
            errors=[fatal_error],
            warnings=[],
        )

        formatter.show_summary(summary)

        # Capture stdout output
        captured = capsys.readouterr()
        output_data = json.loads(captured.out)

        assert output_data["status"] == "fatal"
        assert output_data["error_count"] == 1

    def test_json_formatter_summary_with_warnings(self, capsys):
        """Test JSON output with warnings."""
        formatter = JSONOutputFormatter()

        warning = BuildWarning(
            category="duplicate_topic_id",
            message="Duplicate ID found",
            severity="high",
            file_path="test.py",
        )

        summary = BuildSummary(
            duration=120.5,
            total_files=10,
            errors=[],
            warnings=[warning],
        )

        formatter.show_summary(summary)

        # Capture stdout output
        captured = capsys.readouterr()
        output_data = json.loads(captured.out)

        assert output_data["status"] == "success"
        assert output_data["warning_count"] == 1
        assert len(output_data["warnings"]) == 1

        warning_dict = output_data["warnings"][0]
        assert warning_dict["category"] == "duplicate_topic_id"
        assert warning_dict["message"] == "Duplicate ID found"
        assert warning_dict["severity"] == "high"
        assert warning_dict["file_path"] == "test.py"


class TestCIDetection:
    """Test CI environment detection."""

    def test_ci_detection_github_actions(self, monkeypatch):
        """Test detection of GitHub Actions CI environment."""
        from clx.cli.main import _is_ci_environment

        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        assert _is_ci_environment() is True

    def test_ci_detection_gitlab_ci(self, monkeypatch):
        """Test detection of GitLab CI environment."""
        from clx.cli.main import _is_ci_environment

        monkeypatch.setenv("GITLAB_CI", "true")
        assert _is_ci_environment() is True

    def test_ci_detection_generic_ci(self, monkeypatch):
        """Test detection of generic CI environment."""
        from clx.cli.main import _is_ci_environment

        monkeypatch.setenv("CI", "true")
        assert _is_ci_environment() is True

    def test_ci_detection_no_ci(self, monkeypatch):
        """Test no CI environment detected."""
        from clx.cli.main import _is_ci_environment

        # Clear all CI environment variables
        for var in [
            "CI",
            "GITHUB_ACTIONS",
            "GITLAB_CI",
            "JENKINS_HOME",
            "CIRCLECI",
            "TRAVIS",
            "BUILDKITE",
            "DRONE",
        ]:
            monkeypatch.delenv(var, raising=False)

        assert _is_ci_environment() is False
