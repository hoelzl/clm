"""Tests for notebook error context tracking.

This module provides test-driven development tests for:
1. Error context extraction from executed notebooks
2. Cell identification for C++ and Python errors
3. Code snippet extraction
4. Integration with error categorizer

These tests follow a TDD approach:
- Tests are written first with expected behavior
- Implementation is updated to make tests pass
"""

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest
from nbformat import NotebookNode

from clx.cli.error_categorizer import ErrorCategorizer
from clx.infrastructure.messaging.notebook_classes import NotebookPayload
from clx.workers.notebook.notebook_processor import NotebookProcessor
from clx.workers.notebook.output_spec import create_output_spec

# =============================================================================
# Test Fixtures - Notebook Creation Helpers
# =============================================================================


def make_cell(
    cell_type: str,
    source: str,
    execution_count: int | None = None,
    outputs: list | None = None,
) -> dict:
    """Create a notebook cell dictionary.

    Args:
        cell_type: "code" or "markdown"
        source: Cell content
        execution_count: Execution count (for code cells)
        outputs: Cell outputs (for code cells)

    Returns:
        Cell dictionary
    """
    cell = {
        "id": uuid.uuid4().hex[:16],
        "cell_type": cell_type,
        "source": source,
        "metadata": {},
    }
    if cell_type == "code":
        cell["outputs"] = outputs or []
        cell["execution_count"] = execution_count
    return cell


def make_error_output(ename: str, evalue: str, traceback: list[str]) -> dict:
    """Create an error output for a code cell.

    Args:
        ename: Error name (e.g., "SyntaxError", "CompilationError")
        evalue: Error value/message
        traceback: List of traceback lines

    Returns:
        Error output dictionary
    """
    return {
        "output_type": "error",
        "ename": ename,
        "evalue": evalue,
        "traceback": traceback,
    }


def make_stderr_output(text: str) -> dict:
    """Create a stderr stream output.

    Args:
        text: Stderr text content

    Returns:
        Stream output dictionary
    """
    return {
        "output_type": "stream",
        "name": "stderr",
        "text": text,
    }


def make_notebook(cells: list[dict], kernel: str = "python3") -> NotebookNode:
    """Create a NotebookNode from cells.

    Args:
        cells: List of cell dictionaries
        kernel: Kernel name ("python3" or "xcpp17")

    Returns:
        NotebookNode notebook
    """
    kernelspec = {
        "python3": {"name": "python3", "display_name": "Python 3"},
        "xcpp17": {"name": "xcpp17", "display_name": "C++17"},
    }.get(kernel, {"name": kernel, "display_name": kernel})

    return NotebookNode(
        {
            "cells": cells,
            "metadata": {
                "kernelspec": kernelspec,
                "language_info": {"name": "python" if kernel == "python3" else "c++"},
            },
            "nbformat": 4,
            "nbformat_minor": 5,
        }
    )


def make_payload(
    input_file: str = "test.ipynb",
    prog_lang: str = "python",
) -> NotebookPayload:
    """Create a minimal NotebookPayload for testing.

    Args:
        input_file: Input file name
        prog_lang: Programming language

    Returns:
        NotebookPayload instance
    """
    return NotebookPayload(
        input_file=f"/test/{input_file}",
        input_file_name=input_file,
        output_file="/test/output",
        data="{}",  # Empty notebook JSON
        format="html",
        kind="speaker",
        language="en",
        prog_lang=prog_lang,
        correlation_id="test-correlation-id",
        other_files={},
    )


# =============================================================================
# Unit Tests - _find_failing_cell
# =============================================================================


class TestFindFailingCell:
    """Tests for _find_failing_cell method."""

    @pytest.fixture
    def processor(self):
        """Create a NotebookProcessor for testing."""
        output_spec = create_output_spec("speaker", prog_lang="python")
        return NotebookProcessor(output_spec=output_spec)

    def test_finds_cell_with_error_output(self, processor):
        """Cell with error output should be identified."""
        cells = [
            make_cell("code", "x = 1", execution_count=1),
            make_cell(
                "code",
                "y = undefined",
                execution_count=2,
                outputs=[make_error_output("NameError", "name 'undefined' is not defined", [])],
            ),
            make_cell("code", "z = 3", execution_count=None),
        ]

        failing_cell, cell_index = processor._find_failing_cell(cells, "")

        assert cell_index == 1
        assert failing_cell["source"] == "y = undefined"

    def test_finds_cell_with_cpp_error_in_stderr(self, processor):
        """Cell with C++ compilation error in stderr should be identified."""
        cells = [
            make_cell("code", "#include <iostream>", execution_count=1),
            make_cell(
                "code",
                "class Foo { }",  # Missing semicolon
                execution_count=2,
                outputs=[make_stderr_output("input_line_5:2:1: error: expected ';' after class")],
            ),
            make_cell("code", "int x = 42;", execution_count=None),
        ]

        failing_cell, cell_index = processor._find_failing_cell(cells, "")

        assert cell_index == 1
        assert "class Foo" in failing_cell["source"]

    def test_finds_cell_with_undefined_in_stderr(self, processor):
        """Cell with 'undefined' keyword in stderr should be identified (C++)."""
        cells = [
            make_cell("code", 'std::cout << "Hello";', execution_count=1),
            make_cell(
                "code",
                "std::vector<int> v;",  # Missing #include
                execution_count=2,
                outputs=[
                    make_stderr_output("error: no template named 'vector' in namespace 'std'")
                ],
            ),
        ]

        failing_cell, cell_index = processor._find_failing_cell(cells, "")

        assert cell_index == 1
        assert "vector" in failing_cell["source"]

    def test_falls_back_to_highest_execution_count(self, processor):
        """When no error output, use cell with highest execution_count."""
        cells = [
            make_cell("code", "x = 1", execution_count=1),
            make_cell("code", "y = 2", execution_count=2),
            make_cell("code", "z = 3", execution_count=3),
            make_cell("code", "w = 4", execution_count=None),  # Never executed
        ]

        failing_cell, cell_index = processor._find_failing_cell(cells, "")

        assert cell_index == 2  # Highest execution_count is cell #2
        assert failing_cell["source"] == "z = 3"

    def test_returns_first_code_cell_as_fallback(self, processor):
        """When no other strategy works, return first code cell."""
        cells = [
            make_cell("markdown", "# Header"),
            make_cell("code", "x = 1"),
            make_cell("code", "y = 2"),
        ]

        failing_cell, cell_index = processor._find_failing_cell(cells, "")

        assert cell_index == 1  # First code cell (index 1)
        assert failing_cell["source"] == "x = 1"


# =============================================================================
# Unit Tests - _enhance_notebook_error
# =============================================================================


class TestEnhanceNotebookError:
    """Tests for _enhance_notebook_error method."""

    @pytest.fixture
    def processor(self):
        """Create a NotebookProcessor for testing."""
        output_spec = create_output_spec("speaker", prog_lang="python")
        return NotebookProcessor(output_spec=output_spec)

    def test_includes_cell_number_from_error_output(self, processor):
        """Enhanced error should include cell number when found from error output."""
        notebook = make_notebook(
            [
                make_cell("code", "x = 1", execution_count=1),
                make_cell(
                    "code",
                    "y = undefined",
                    execution_count=2,
                    outputs=[make_error_output("NameError", "name 'undefined' is not defined", [])],
                ),
            ]
        )
        payload = make_payload("test.ipynb")
        error = Exception("CellExecutionError: NameError")

        enhanced = processor._enhance_notebook_error(error, notebook, payload)

        assert "Cell: #1" in str(enhanced)  # Cell index 1

    def test_includes_cell_content_snippet(self, processor):
        """Enhanced error should include code snippet from failing cell."""
        notebook = make_notebook(
            [
                make_cell(
                    "code",
                    "class BrokenClass {\n  void DoNothing() {}\n}",
                    execution_count=1,
                    outputs=[make_stderr_output("error: expected ';'")],
                ),
            ]
        )
        payload = make_payload("test.cpp", prog_lang="cpp")
        error = Exception("Compilation error")

        enhanced = processor._enhance_notebook_error(error, notebook, payload)

        assert "Cell content:" in str(enhanced)
        assert "BrokenClass" in str(enhanced)

    def test_extracts_cpp_line_column_from_xeus_cling_error(self, processor):
        """C++ line/column info should be extracted from xeus-cling error format."""
        notebook = make_notebook(
            [
                make_cell(
                    "code",
                    "class Foo { }",
                    execution_count=1,
                    outputs=[
                        make_stderr_output("input_line_5:2:1: error: expected ';' after class")
                    ],
                ),
            ],
            kernel="xcpp17",
        )
        payload = make_payload("test.cpp", prog_lang="cpp")
        # Simulate xeus-cling error format in the exception message
        error = Exception("input_line_5:2:1: error: expected ';' after class")

        enhanced = processor._enhance_notebook_error(error, notebook, payload)

        # Should extract line and column from xeus-cling format
        assert "Line: 2" in str(enhanced) or "line 2" in str(enhanced).lower()

    def test_sets_compilation_error_class_for_cpp(self, processor):
        """Error class should be 'CompilationError' for C++ errors."""
        notebook = make_notebook(
            [
                make_cell(
                    "code",
                    "std::vector<int> v;",
                    execution_count=1,
                    outputs=[make_stderr_output("error: no template named 'vector'")],
                ),
            ],
            kernel="xcpp17",
        )
        payload = make_payload("test.cpp", prog_lang="cpp")
        error = Exception("input_line_3:1:1: error: no template named 'vector' in namespace 'std'")

        enhanced = processor._enhance_notebook_error(error, notebook, payload)

        assert "CompilationError" in str(enhanced)


# =============================================================================
# Unit Tests - Error Categorizer Integration
# =============================================================================


class TestErrorCategorizerCellExtraction:
    """Tests for ErrorCategorizer cell number and code snippet extraction."""

    def test_extracts_cell_number_from_enhanced_error(self):
        """Categorizer should extract cell_number from enhanced error format."""
        error_msg = """Notebook execution failed: test.cpp
  Cell: #3
  Cell content:
    class BrokenClass {
    public:
        void DoNothing() {}
    }
  Error: CompilationError: expected ';' after class
"""

        details = ErrorCategorizer._parse_notebook_error(error_msg)

        assert details.get("cell_number") == 3

    def test_extracts_code_snippet_from_cell_content(self):
        """Categorizer should extract code_snippet from 'Cell content:' section."""
        error_msg = """Notebook execution failed: test.cpp
  Cell: #3
  Cell content:
    class BrokenClass {
    public:
        void DoNothing() {}
    }
  Error: CompilationError: expected ';' after class
"""

        details = ErrorCategorizer._parse_notebook_error(error_msg)

        assert "code_snippet" in details
        assert "BrokenClass" in details["code_snippet"]

    def test_extracts_cpp_error_details(self):
        """Categorizer should extract C++ compilation error details."""
        error_msg = """Notebook execution failed: test.cpp
  Cell: #1
  Cell content:
    std::vector<int> data;
  Error: CompilationError: no template named 'vector' in namespace 'std'
input_line_3:1:6: error: no template named 'vector' in namespace 'std'
"""

        details = ErrorCategorizer._parse_notebook_error(error_msg)

        assert details.get("error_class") == "CompilationError"
        assert "vector" in details.get("short_message", "")

    def test_categorize_job_error_includes_details(self):
        """categorize_job_error should include all extracted details."""
        error_msg = """Notebook execution failed: test.cpp
  Cell: #2
  Cell content:
    class Foo { }
  Error: CompilationError: expected ';' after class
"""

        error = ErrorCategorizer.categorize_job_error(
            job_type="notebook",
            input_file="test.cpp",
            error_message=error_msg,
            job_payload={},
        )

        assert error.details.get("cell_number") == 2
        assert "code_snippet" in error.details
        assert "Foo" in error.details["code_snippet"]


# =============================================================================
# Integration Tests - Full Error Path (Mocked Execution)
# =============================================================================


class TestErrorPathIntegration:
    """Integration tests for the full error path from execution to display."""

    def test_python_syntax_error_path(self):
        """Python SyntaxError should flow through with cell info."""
        # Simulate what _enhance_notebook_error produces for a Python SyntaxError
        # Note: Python errors don't include Line: info, that's C++ only
        enhanced_msg = """Notebook execution failed: test.ipynb
  Cell: #1
  Cell content:
    x = 1
    y =
    z = 3
  Error: SyntaxError: invalid syntax
"""

        error = ErrorCategorizer.categorize_job_error(
            job_type="notebook",
            input_file="test.ipynb",
            error_message=enhanced_msg,
            job_payload={},
        )

        assert error.error_type == "user"
        assert error.details.get("cell_number") == 1
        # Python errors don't include line number in enhanced format
        assert "code_snippet" in error.details
        # Verify code_snippet contains the actual code
        assert "x = 1" in error.details["code_snippet"]
        # Verify code_snippet doesn't include the Error: line (not the comment)
        assert "invalid syntax" not in error.details["code_snippet"]

    def test_cpp_compilation_error_path(self):
        """C++ CompilationError should flow through with cell info."""
        # Simulate what _enhance_notebook_error produces for a C++ error
        enhanced_msg = """Notebook execution failed: test.cpp
  Cell: #0
  Cell content:
    std::vector<int> data;
  Error: CompilationError: no template named 'vector' in namespace 'std'
  Line: 1, Column: 6
"""

        error = ErrorCategorizer.categorize_job_error(
            job_type="notebook",
            input_file="test.cpp",
            error_message=enhanced_msg,
            job_payload={},
        )

        assert error.error_type == "user"
        assert error.details.get("cell_number") == 0
        assert error.details.get("line_number") == 1  # Should extract Line: 1
        assert "code_snippet" in error.details
        assert "vector" in error.details["code_snippet"]
        # Verify code_snippet doesn't include Error: or Line: lines
        assert "CompilationError" not in error.details["code_snippet"]


# =============================================================================
# Tests for CellContext Tracking (TDD - Not Yet Implemented)
# =============================================================================


class TestCellContextTracking:
    """Tests for cell execution tracking feature.

    These tests verify the expected behavior of the CellExecutionTracker
    that should be added to NotebookProcessor.
    """

    @pytest.fixture
    def processor(self):
        """Create a NotebookProcessor for testing."""
        output_spec = create_output_spec("speaker", prog_lang="python")
        return NotebookProcessor(output_spec=output_spec)

    def test_processor_has_current_cell_attribute(self, processor):
        """NotebookProcessor should have _current_cell attribute."""
        # This test will FAIL until CellContext is implemented
        assert hasattr(processor, "_current_cell")

    def test_current_cell_is_none_initially(self, processor):
        """_current_cell should be None initially."""
        assert processor._current_cell is None

    @pytest.mark.skip(reason="Requires execution tracking implementation")
    def test_current_cell_set_during_execution(self, processor):
        """_current_cell should be set before each cell executes.

        This test requires mocking the notebook execution to verify
        that _current_cell is set correctly.
        """
        # TODO: Implement after adding CellContext tracking
        pass

    @pytest.mark.skip(reason="Requires execution tracking implementation")
    def test_current_cell_cleared_after_success(self, processor):
        """_current_cell should be cleared after successful cell execution.

        This test requires mocking the notebook execution.
        """
        # TODO: Implement after adding CellContext tracking
        pass


# =============================================================================
# Docker Integration Tests (Require Real C++ Execution)
# =============================================================================


def _is_docker_available() -> bool:
    """Check if Docker daemon is available."""
    try:
        import docker

        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


def _is_cpp_image_available() -> bool:
    """Check if C++ Docker image is available."""
    try:
        import docker

        client = docker.from_env()
        # Try to find the full image with C++ support
        for tag in [
            "mhoelzl/clx-notebook-processor:full",
            "mhoelzl/clx-notebook-processor:0.5.1-full",
        ]:
            try:
                client.images.get(tag)
                return True
            except docker.errors.ImageNotFound:
                continue
        return False
    except Exception:
        return False


@pytest.mark.integration
@pytest.mark.docker
@pytest.mark.skipif(
    not _is_docker_available(),
    reason="Docker daemon not available",
)
@pytest.mark.skipif(
    not _is_cpp_image_available(),
    reason="C++ Docker image not available (need full image)",
)
class TestCppErrorWithDocker:
    """Integration tests that execute real C++ notebooks via Docker.

    These tests require:
    - Docker daemon running
    - mhoelzl/clx-notebook-processor:full image (has xeus-cling)
    """

    @pytest.fixture
    def cpp_error_notebook(self, tmp_path):
        """Create a C++ notebook with a known error."""
        notebook = {
            "cells": [
                {
                    "cell_type": "code",
                    "execution_count": None,
                    "id": "cell-0",
                    "metadata": {},
                    "outputs": [],
                    "source": '#include <iostream>\nstd::cout << "Hello" << std::endl;',
                },
                {
                    "cell_type": "code",
                    "execution_count": None,
                    "id": "cell-1",
                    "metadata": {},
                    "outputs": [],
                    # Missing semicolon - guaranteed C++ error
                    "source": "class BrokenClass {\npublic:\n    void DoNothing() {}\n}",
                },
            ],
            "metadata": {
                "kernelspec": {"name": "xcpp17", "display_name": "C++17"},
                "language_info": {"name": "c++"},
            },
            "nbformat": 4,
            "nbformat_minor": 5,
        }

        notebook_path = tmp_path / "test_error.cpp"
        notebook_path.write_text(json.dumps(notebook))
        return notebook_path

    @pytest.mark.skip(reason="Docker integration test - run manually")
    def test_cpp_error_identifies_correct_cell(self, cpp_error_notebook):
        """Real C++ execution should identify cell #1 as the failing cell.

        This test executes a real notebook through Docker and verifies
        that the error message includes correct cell information.
        """
        # TODO: Implement full Docker-based integration test
        # This would:
        # 1. Start a Docker notebook worker
        # 2. Submit the notebook for processing
        # 3. Capture the error message
        # 4. Verify cell_number == 1 and code_snippet contains "BrokenClass"
        pass
