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
    VerboseOutputFormatter,
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

    def test_parse_cpp_xeus_cling_error(self):
        """Test parsing C++ xeus-cling compiler error."""
        error_msg = """An error occurred while executing the following code:
class BrokenClass {
public:
    void DoNothing() {}
}

input_line_8:5:2: error: expected ';' after class
}
 ^
 ;
"""

        details = ErrorCategorizer._parse_notebook_error(error_msg)

        assert details.get("error_class") == "CompilationError"
        assert details.get("line_number") == 5
        assert details.get("column_number") == 2
        assert "expected ';' after class" in details.get("short_message", "")

    def test_parse_cpp_clang_style_error(self):
        """Test parsing generic clang-style compiler error."""
        error_msg = """Compilation error:
test.cpp:10:5: error: use of undeclared identifier 'foo'
    foo();
    ^
"""

        details = ErrorCategorizer._parse_notebook_error(error_msg)

        assert details.get("error_class") == "CompilationError"
        assert details.get("line_number") == 10
        assert details.get("column_number") == 5
        assert "use of undeclared identifier" in details.get("short_message", "")

    def test_parse_cell_content_from_enhanced_error(self):
        """Test parsing cell content from enhanced notebook error."""
        error_msg = """Notebook execution failed: test.cpp
  Cell: #3
  Cell content:
    class BrokenClass {
    public:
        void DoNothing() {}
    }
  Error: CompilationError: expected ';' after class
  Line: 5, Column: 2
"""

        details = ErrorCategorizer._parse_notebook_error(error_msg)

        assert details.get("cell_number") == 3
        assert "code_snippet" in details
        assert "BrokenClass" in details.get("code_snippet", "")

    def test_parse_error_with_colon_cell_format(self):
        """Test parsing cell number from 'Cell: #N' format."""
        error_msg = "Cell: #7\nError: SyntaxError"

        details = ErrorCategorizer._parse_notebook_error(error_msg)

        assert details.get("cell_number") == 7

    def test_categorize_cpp_compilation_error(self):
        """Test that C++ compilation errors are categorized correctly."""
        error_msg = """Notebook execution failed: observer.cpp
  Cell: #5
  Cell content:
    class StockObserver {
    public:
        virtual ~StockObserver() = default;
    }
  Error: CompilationError: expected ';' after class
input_line_8:5:2: error: expected ';' after class
"""

        error = ErrorCategorizer.categorize_job_error(
            job_type="notebook",
            input_file="observer.cpp",
            error_message=error_msg,
            job_payload={},
        )

        assert error.error_type == "user"
        assert error.details.get("cell_number") == 5
        assert error.details.get("error_class") == "CompilationError"
        assert "expected ';' after class" in error.details.get("short_message", "")

    def test_no_verbose_in_default_guidance(self):
        """Test that the guidance doesn't suggest --verbose (it's not a valid flag)."""
        error_msg = "Some unknown error"

        error = ErrorCategorizer.categorize_job_error(
            job_type="notebook",
            input_file="test.ipynb",
            error_message=error_msg,
            job_payload={},
        )

        # Should not contain the old misleading message
        assert "--verbose" not in error.actionable_guidance
        # Should contain helpful cell-specific guidance
        assert "Check your notebook" in error.actionable_guidance

    def test_does_not_extract_python_traceback_as_code(self):
        """Test that Python traceback lines are not extracted as code context."""
        # This simulates an error where there's no Cell content section
        # but there is a Python traceback with caret markers
        error_msg = """Notebook execution failed: slides_static_strategy.cpp
  Error: CompilationError: expected ';' after top level declarator
           ^^^^^^^^^^^^^^^
result = await processor.process_notebook(payload, source_dir=source_dir)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
result = await self.create_contents(processed_nb, payload, source_dir=source_dir)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
result = await self._create_using_nbconvert(
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
await self._execute_notebook_with_path(
await loop.run_in_executor(None, run_preprocess)
result = self.fn(*self.args, **self.kwargs)
"""

        details = ErrorCategorizer._parse_notebook_error(error_msg)

        # Should NOT have extracted Python traceback as code snippet
        code_snippet = details.get("code_snippet", "")
        assert "await processor" not in code_snippet
        assert "await self.create_contents" not in code_snippet
        assert "result = " not in code_snippet

    def test_extracts_cell_content_over_traceback(self):
        """Test that Cell content section is preferred over traceback patterns."""
        error_msg = """Notebook execution failed: test.cpp
  Cell: #5
  Cell content:
    class BrokenClass {
    public:
        void DoNothing() {}
    }
  Error: CompilationError: expected ';' after class
           ^^^^^^^^^^^^^^^
result = await processor.process_notebook(payload)
"""

        details = ErrorCategorizer._parse_notebook_error(error_msg)

        # Should have extracted the cell content, not the traceback
        code_snippet = details.get("code_snippet", "")
        assert "BrokenClass" in code_snippet
        assert "await processor" not in code_snippet


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


class TestVerboseOutputFormatter:
    """Test verbose output formatter for detailed error reporting."""

    def test_verbose_formatter_creation(self):
        """Test creating verbose output formatter."""
        formatter = VerboseOutputFormatter(show_progress=True, use_color=True)
        assert formatter is not None
        assert formatter.show_progress is True
        assert formatter.use_color is True
        assert formatter._files_started == {}
        assert formatter._files_completed == 0
        assert formatter._files_failed == 0

    def test_verbose_formatter_always_shows_errors(self):
        """Test that verbose formatter always shows errors regardless of severity."""
        formatter = VerboseOutputFormatter()

        # Test error with severity "error"
        error = BuildError(
            error_type="user",
            category="test",
            severity="error",
            file_path="test.py",
            message="Test error",
            actionable_guidance="Fix it",
        )
        assert formatter.should_show_error(error) is True

        # Test error with severity "warning" - verbose should still show it
        warning_error = BuildError(
            error_type="user",
            category="test",
            severity="warning",
            file_path="test.py",
            message="Warning error",
            actionable_guidance="Fix it",
        )
        assert formatter.should_show_error(warning_error) is True

    def test_verbose_formatter_always_shows_warnings(self):
        """Test that verbose formatter always shows warnings regardless of severity."""
        formatter = VerboseOutputFormatter()

        # Test warning with severity "high"
        high_warning = BuildWarning(
            category="test",
            message="High warning",
            severity="high",
        )
        assert formatter.should_show_warning(high_warning) is True

        # Test warning with severity "low" - verbose should still show it
        low_warning = BuildWarning(
            category="test",
            message="Low warning",
            severity="low",
        )
        assert formatter.should_show_warning(low_warning) is True

        # Test warning with severity "medium"
        medium_warning = BuildWarning(
            category="test",
            message="Medium warning",
            severity="medium",
        )
        assert formatter.should_show_warning(medium_warning) is True

    def test_verbose_formatter_vs_default_error_display(self):
        """Test verbose shows all errors vs default which filters by severity."""
        verbose = VerboseOutputFormatter()
        default = DefaultOutputFormatter()

        # Low severity warning error - default should NOT show, verbose should
        warning_error = BuildError(
            error_type="user",
            category="test",
            severity="warning",
            file_path="test.py",
            message="Warning",
            actionable_guidance="Fix it",
        )

        assert verbose.should_show_error(warning_error) is True
        assert default.should_show_error(warning_error) is False

    def test_verbose_formatter_vs_default_warning_display(self):
        """Test verbose shows all warnings vs default which filters by severity."""
        verbose = VerboseOutputFormatter()
        default = DefaultOutputFormatter()

        # Low priority warning - default should NOT show, verbose should
        low_warning = BuildWarning(
            category="test",
            message="Low warning",
            severity="low",
        )

        assert verbose.should_show_warning(low_warning) is True
        assert default.should_show_warning(low_warning) is False

    def test_verbose_formatter_file_started(self):
        """Test verbose formatter tracks file processing start."""
        formatter = VerboseOutputFormatter()

        # Call show_file_started
        formatter.show_file_started("test.ipynb", "notebook", job_id=123)

        # Check that the file is being tracked
        assert "test.ipynb" in formatter._files_started

    def test_verbose_formatter_file_completed_success(self):
        """Test verbose formatter tracks file processing completion (success)."""
        formatter = VerboseOutputFormatter()

        # First start the file
        formatter.show_file_started("test.ipynb", "notebook", job_id=123)

        # Then complete it successfully
        formatter.show_file_completed("test.ipynb", "notebook", job_id=123, success=True)

        # Check that tracking was updated
        assert "test.ipynb" not in formatter._files_started
        assert formatter._files_completed == 1
        assert formatter._files_failed == 0

    def test_verbose_formatter_file_completed_failure(self):
        """Test verbose formatter tracks file processing completion (failure)."""
        formatter = VerboseOutputFormatter()

        # First start the file
        formatter.show_file_started("test.ipynb", "notebook", job_id=123)

        # Then mark it as failed
        formatter.show_file_completed("test.ipynb", "notebook", job_id=123, success=False)

        # Check that tracking was updated
        assert "test.ipynb" not in formatter._files_started
        assert formatter._files_completed == 0
        assert formatter._files_failed == 1

    def test_verbose_summary_shows_all_errors(self, capsys):
        """Test verbose summary shows all errors, not just first 5."""
        formatter = VerboseOutputFormatter(show_progress=False, use_color=False)

        # Create 10 errors
        errors = []
        for i in range(10):
            errors.append(
                BuildError(
                    error_type="user",
                    category="test",
                    severity="error",
                    file_path=f"test{i}.py",
                    message=f"Error {i}",
                    actionable_guidance=f"Fix {i}",
                )
            )

        summary = BuildSummary(
            duration=10.0,
            total_files=10,
            errors=errors,
            warnings=[],
        )

        formatter.show_summary(summary)

        captured = capsys.readouterr()
        # Check that all 10 errors are mentioned (not just first 5)
        for i in range(10):
            assert f"test{i}.py" in captured.err, f"Error {i} should appear in verbose output"

        # Should NOT contain "... and X more errors"
        assert "more errors" not in captured.err

    def test_verbose_summary_shows_all_warnings(self, capsys):
        """Test verbose summary shows all warnings, not just first 3."""
        formatter = VerboseOutputFormatter(show_progress=False, use_color=False)

        # Create 7 warnings
        warnings = []
        for i in range(7):
            warnings.append(
                BuildWarning(
                    category="test",
                    message=f"Warning {i}",
                    severity="high",
                    file_path=f"test{i}.py",
                )
            )

        summary = BuildSummary(
            duration=10.0,
            total_files=10,
            errors=[],
            warnings=warnings,
        )

        formatter.show_summary(summary)

        captured = capsys.readouterr()
        # Check that all 7 warnings are mentioned (not just first 3)
        for i in range(7):
            assert f"Warning {i}" in captured.err, f"Warning {i} should appear in verbose output"

        # Should NOT contain "... and X more warnings"
        assert "more warnings" not in captured.err


class TestBuildReporterFileEvents:
    """Test build reporter file event methods."""

    def test_build_reporter_file_started(self):
        """Test build reporter forwards file started events to formatter."""
        formatter = VerboseOutputFormatter()
        reporter = BuildReporter(output_formatter=formatter)

        reporter.report_file_started("test.ipynb", "notebook", job_id=123)

        # Check that the formatter received the event
        assert "test.ipynb" in formatter._files_started

    def test_build_reporter_file_completed(self):
        """Test build reporter forwards file completed events to formatter."""
        formatter = VerboseOutputFormatter()
        reporter = BuildReporter(output_formatter=formatter)

        # Start and complete a file
        reporter.report_file_started("test.ipynb", "notebook", job_id=123)
        reporter.report_file_completed("test.ipynb", "notebook", job_id=123, success=True)

        # Check that the formatter received the events
        assert "test.ipynb" not in formatter._files_started
        assert formatter._files_completed == 1

    def test_default_formatter_ignores_file_events(self):
        """Test that default formatter no-ops on file events."""
        formatter = DefaultOutputFormatter()
        reporter = BuildReporter(output_formatter=formatter)

        # These should not raise errors even though DefaultOutputFormatter
        # doesn't track file events
        reporter.report_file_started("test.ipynb", "notebook", job_id=123)
        reporter.report_file_completed("test.ipynb", "notebook", job_id=123, success=True)
