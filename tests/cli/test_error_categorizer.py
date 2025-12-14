"""Tests for error categorization.

This module tests the ErrorCategorizer class to ensure errors are
properly classified with correct actionable guidance.
"""

import pytest

from clx.cli.error_categorizer import ErrorCategorizer


class TestDrawioErrorCategorization:
    """Tests for DrawIO error categorization."""

    def test_missing_drawio_executable_categorized_correctly(self):
        """Error about missing DRAWIO_EXECUTABLE should be configuration error."""
        error = ErrorCategorizer.categorize_job_error(
            job_type="drawio",
            input_file="test.drawio",
            error_message="DRAWIO_EXECUTABLE environment variable not set",
            job_payload={},
        )

        assert error.error_type == "configuration"
        assert error.category == "missing_drawio"
        assert "DRAWIO_EXECUTABLE" in error.actionable_guidance

    def test_drawio_not_found_executable_categorized_correctly(self):
        """Error about drawio executable not found should be configuration error."""
        error = ErrorCategorizer.categorize_job_error(
            job_type="drawio",
            input_file="test.drawio",
            error_message="drawio: command not found",
            job_payload={},
        )

        assert error.error_type == "configuration"
        assert error.category == "missing_drawio"

    def test_input_file_not_found_should_not_be_missing_drawio(self):
        """Input file not found should NOT be categorized as missing_drawio.

        This test catches a bug where 'not found' pattern matching is too broad,
        causing 'Input file not found' errors to be incorrectly categorized as
        missing DrawIO executable errors.

        The user sees:
            Error: Input file not found: C:\\Users\\...\\file.drawio
            Action: Install DrawIO desktop and set DRAWIO_EXECUTABLE...

        When they should see:
            Error: Input file not found: C:\\Users\\...\\file.drawio
            Action: Check your DrawIO diagram for errors
        """
        error = ErrorCategorizer.categorize_job_error(
            job_type="drawio",
            input_file="test.drawio",
            error_message="Input file not found: C:\\Users\\tc\\file.drawio",
            job_payload={},
        )

        # This should NOT be categorized as missing_drawio
        # because the file is not found, not the DrawIO executable
        assert error.category != "missing_drawio", (
            "Input file not found should not be categorized as missing_drawio. "
            "The 'not found' pattern is too broad."
        )
        assert error.error_type == "user" or error.error_type == "configuration"
        assert "DRAWIO_EXECUTABLE" not in error.actionable_guidance, (
            "Guidance should not mention DRAWIO_EXECUTABLE for input file errors"
        )

    def test_file_not_found_error_class_not_missing_drawio(self):
        """FileNotFoundError for input files should not be missing_drawio."""
        # Structured error from worker
        error_message = (
            '{"error_message": "Input file not found: /source/file.drawio", '
            '"error_class": "FileNotFoundError"}'
        )
        error = ErrorCategorizer.categorize_job_error(
            job_type="drawio",
            input_file="test.drawio",
            error_message=error_message,
            job_payload={},
        )

        assert error.category != "missing_drawio"

    def test_conversion_error_categorized_as_user_error(self):
        """DrawIO conversion errors should be user errors."""
        error = ErrorCategorizer.categorize_job_error(
            job_type="drawio",
            input_file="test.drawio",
            error_message="Error converting DrawIO file: invalid XML",
            job_payload={},
        )

        assert error.error_type == "user"
        assert error.category == "drawio_processing"

    def test_v8_crash_categorized_appropriately(self):
        """V8/Electron crash in DrawIO should be categorized as infrastructure error.

        This catches crashes like 'Invoke in DisallowJavascriptExecutionScope'
        which are internal DrawIO errors, not user errors.
        """
        error = ErrorCategorizer.categorize_job_error(
            job_type="drawio",
            input_file="test.drawio",
            error_message=(
                "Error converting DrawIO file:\n"
                "Fatal error in , line 0\n"
                "Invoke in DisallowJavascriptExecutionScope"
            ),
            job_payload={},
        )

        # V8 crashes should be categorized as infrastructure errors
        # as there may be nothing wrong with the diagram itself
        assert error.error_type == "infrastructure"
        assert error.category == "drawio_crash"
        # Guidance should mention transient error
        assert (
            "crash" in error.actionable_guidance.lower()
            or "transient" in error.actionable_guidance.lower()
        )


class TestNotebookErrorCategorization:
    """Tests for notebook error categorization."""

    def test_syntax_error_is_user_error(self):
        """SyntaxError should be categorized as user error."""
        error = ErrorCategorizer.categorize_job_error(
            job_type="notebook",
            input_file="test.ipynb",
            error_message="SyntaxError: invalid syntax",
            job_payload={},
        )

        assert error.error_type == "user"
        assert error.category == "notebook_compilation"

    def test_module_not_found_is_user_error(self):
        """ModuleNotFoundError should be user error."""
        error = ErrorCategorizer.categorize_job_error(
            job_type="notebook",
            input_file="test.ipynb",
            error_message="ModuleNotFoundError: No module named 'nonexistent'",
            job_payload={},
        )

        assert error.error_type == "user"
        assert error.category == "missing_module"


class TestPlantumlErrorCategorization:
    """Tests for PlantUML error categorization."""

    def test_missing_plantuml_jar_is_configuration_error(self):
        """Missing PLANTUML_JAR should be configuration error."""
        error = ErrorCategorizer.categorize_job_error(
            job_type="plantuml",
            input_file="test.puml",
            error_message="PLANTUML_JAR environment variable not set",
            job_payload={},
        )

        assert error.error_type == "configuration"
        assert error.category == "missing_plantuml"

    def test_input_file_not_found_should_not_be_missing_plantuml(self):
        """Input file not found should NOT be categorized as missing_plantuml.

        Same bug as DrawIO - 'not found' pattern is too broad.
        """
        error = ErrorCategorizer.categorize_job_error(
            job_type="plantuml",
            input_file="test.puml",
            error_message="Input file not found: /path/to/test.puml",
            job_payload={},
        )

        # This should NOT be categorized as missing_plantuml
        assert error.category != "missing_plantuml", (
            "Input file not found should not be categorized as missing_plantuml"
        )
