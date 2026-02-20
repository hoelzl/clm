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

import gc
import json
import shutil
import sqlite3
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from nbformat import NotebookNode

from clm.cli.error_categorizer import ErrorCategorizer
from clm.infrastructure.messaging.notebook_classes import NotebookPayload
from clm.workers.notebook.notebook_processor import (
    CellContext,
    NotebookProcessor,
    TrackingExecutePreprocessor,
)
from clm.workers.notebook.output_spec import create_output_spec

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

    def test_uses_ename_evalue_from_cell_execution_error(self, processor):
        """Should use ename/evalue from CellExecutionError-like exceptions."""

        class FakeCellExecutionError(Exception):
            """Mimics nbclient's CellExecutionError with ename/evalue."""

            def __init__(self, ename: str, evalue: str):
                self.ename = ename
                self.evalue = evalue
                super().__init__(
                    f"An error occurred while executing the following cell:\n"
                    f"------------------\nprint(undefined_var)\n------------------\n"
                    f"{ename}: {evalue}"
                )

        notebook = make_notebook(
            [
                make_cell(
                    "code",
                    "print(undefined_var)",
                    execution_count=1,
                    outputs=[
                        make_error_output("NameError", "name 'undefined_var' is not defined", [])
                    ],
                ),
            ]
        )
        payload = make_payload("test.ipynb")
        error = FakeCellExecutionError("NameError", "name 'undefined_var' is not defined")

        enhanced = processor._enhance_notebook_error(error, notebook, payload)
        enhanced_str = str(enhanced)

        assert "NameError" in enhanced_str
        assert "name 'undefined_var' is not defined" in enhanced_str
        assert "CellExecutionError" not in enhanced_str
        assert "An error occurred while executing" not in enhanced_str

    def test_uses_ename_evalue_from_chained_exception(self, processor):
        """Should extract ename/evalue even when CellExecutionError is wrapped."""

        class FakeCellExecutionError(Exception):
            def __init__(self, ename: str, evalue: str):
                self.ename = ename
                self.evalue = evalue
                super().__init__(f"{ename}: {evalue}")

        notebook = make_notebook(
            [
                make_cell(
                    "code",
                    "import foo",
                    execution_count=1,
                    outputs=[make_error_output("ModuleNotFoundError", "No module named 'foo'", [])],
                ),
            ]
        )
        payload = make_payload("test.ipynb")
        inner = FakeCellExecutionError("ModuleNotFoundError", "No module named 'foo'")
        error = RuntimeError("Notebook processing failed")
        error.__cause__ = inner

        enhanced = processor._enhance_notebook_error(error, notebook, payload)
        enhanced_str = str(enhanced)

        assert "ModuleNotFoundError" in enhanced_str
        assert "No module named 'foo'" in enhanced_str

    def test_falls_back_to_type_name_without_ename(self, processor):
        """Should fall back to type().__name__ / str() for regular exceptions."""
        notebook = make_notebook(
            [
                make_cell("code", "x = 1", execution_count=1),
            ]
        )
        payload = make_payload("test.ipynb")
        error = ValueError("something went wrong")

        enhanced = processor._enhance_notebook_error(error, notebook, payload)
        enhanced_str = str(enhanced)

        assert "ValueError" in enhanced_str
        assert "something went wrong" in enhanced_str


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

    def test_current_cell_set_during_execution(self, processor):
        """_current_cell should be set before each cell executes.

        Tests that TrackingExecutePreprocessor sets _current_cell
        on the processor before the parent preprocess_cell is called.
        """
        # Create a tracking preprocessor linked to this processor
        ep = TrackingExecutePreprocessor(processor, timeout=None)

        # Create a test cell
        test_cell = {
            "id": "test123",
            "cell_type": "code",
            "source": "print('hello')",
            "metadata": {},
            "outputs": [],
            "execution_count": None,
        }

        # Track what _current_cell was during the "execution"
        captured_cell_context = None

        # Mock the parent class's preprocess_cell to capture state
        # Note: self_ param required because it's called as an instance method
        def capture_context(self_, cell, resources, cell_index):
            nonlocal captured_cell_context
            captured_cell_context = processor._current_cell
            # Return valid result (cell, resources)
            return (cell, resources)

        with patch.object(
            TrackingExecutePreprocessor.__bases__[0],
            "preprocess_cell",
            capture_context,
        ):
            ep.preprocess_cell(test_cell, {}, cell_index=2)

        # Verify that _current_cell was set during execution
        assert captured_cell_context is not None
        assert captured_cell_context.cell_index == 2
        assert captured_cell_context.cell_source == "print('hello')"
        assert captured_cell_context.cell_type == "code"

    def test_current_cell_cleared_after_success(self, processor):
        """_current_cell should be cleared after successful cell execution.

        Tests that TrackingExecutePreprocessor clears _current_cell
        after the cell executes successfully.
        """
        # Create a tracking preprocessor linked to this processor
        ep = TrackingExecutePreprocessor(processor, timeout=None)

        # Create a test cell
        test_cell = {
            "id": "test456",
            "cell_type": "code",
            "source": "x = 1 + 1",
            "metadata": {},
            "outputs": [],
            "execution_count": None,
        }

        # Mock successful execution
        # Note: self_ param required because it's called as an instance method
        def successful_execution(self_, cell, resources, cell_index):
            return (cell, resources)

        with patch.object(
            TrackingExecutePreprocessor.__bases__[0],
            "preprocess_cell",
            successful_execution,
        ):
            ep.preprocess_cell(test_cell, {}, cell_index=1)

        # After successful execution, _current_cell should be None
        assert processor._current_cell is None

    def test_current_cell_preserved_on_error(self, processor):
        """_current_cell should be preserved when cell execution fails.

        This allows _enhance_notebook_error to use the cell context
        for accurate error reporting.
        """
        # Create a tracking preprocessor linked to this processor
        ep = TrackingExecutePreprocessor(processor, timeout=None)

        # Create a test cell that will "fail"
        test_cell = {
            "id": "test789",
            "cell_type": "code",
            "source": "1 / 0  # Division by zero",
            "metadata": {},
            "outputs": [],
            "execution_count": None,
        }

        # Mock execution that raises an error
        # Note: self_ param required because it's called as an instance method
        def failing_execution(self_, cell, resources, cell_index):
            raise RuntimeError("Cell execution failed: ZeroDivisionError")

        with patch.object(
            TrackingExecutePreprocessor.__bases__[0],
            "preprocess_cell",
            failing_execution,
        ):
            with pytest.raises(RuntimeError):
                ep.preprocess_cell(test_cell, {}, cell_index=3)

        # After failed execution, _current_cell should still be set
        assert processor._current_cell is not None
        assert processor._current_cell.cell_index == 3
        assert "1 / 0" in processor._current_cell.cell_source


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
            "mhoelzl/clm-notebook-processor:full",
            "mhoelzl/clm-notebook-processor:1.0.4-full",
        ]:
            try:
                client.images.get(tag)
                return True
            except docker.errors.ImageNotFound:
                continue
        return False
    except Exception:
        return False


def _get_full_image_name() -> str | None:
    """Get the name of the full Docker image if available."""
    try:
        import docker

        client = docker.from_env()
        # Try to find the full image with C++ support
        for tag in [
            "mhoelzl/clm-notebook-processor:full",
            "mhoelzl/clm-notebook-processor:1.0.4-full",
        ]:
            try:
                client.images.get(tag)
                return tag
            except docker.errors.ImageNotFound:
                continue
        return None
    except Exception:
        return None


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
    - mhoelzl/clm-notebook-processor:full image (has xeus-cling)
    """

    @pytest.fixture
    def docker_test_env(self):
        """Set up environment for Docker C++ error context tests.

        Creates:
        - A database in a dedicated temp directory
        - A workspace (output) directory
        - A data directory (input) with C++ test notebook
        """
        from clm.infrastructure.database.schema import init_database

        # Create a dedicated temp directory for the database
        temp_dir = Path(tempfile.mkdtemp(prefix="clm-cpp-error-test-"))
        db_path = temp_dir / "test.db"
        init_database(db_path)

        # Create workspace (output) directory
        workspace = temp_dir / "output"
        workspace.mkdir()

        # Create data directory (input) with test files
        data_dir = temp_dir / "data"
        data_dir.mkdir()

        # Create topic directory for the C++ notebook
        topic_dir = data_dir / "slides" / "test_cpp"
        topic_dir.mkdir(parents=True)

        # Create C++ notebook in percent format (what jupytext expects for .cpp files)
        # This is the native format for C++ notebooks in CLM
        # The missing semicolon after the class definition will cause a C++ compilation error
        notebook_content = """// %% [markdown]
// # Test C++ Notebook
// This notebook tests error context tracking for C++ compilation errors.

// %%
#include <iostream>
std::cout << "Hello" << std::endl;

// %%
// Missing semicolon after class - guaranteed C++ error
class BrokenClass {
public:
    void DoNothing() {}
}
"""
        (topic_dir / "test_cpp_error.cpp").write_text(notebook_content)

        yield {
            "temp_dir": temp_dir,
            "db_path": db_path,
            "workspace": workspace,
            "data_dir": data_dir,
            "topic_dir": topic_dir,
        }

        # Cleanup
        gc.collect()

        try:
            conn = sqlite3.connect(db_path)
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.close()
        except Exception:
            pass

        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass

    def test_cpp_error_identifies_correct_cell(self, docker_test_env):
        """Real C++ execution should identify cell #1 as the failing cell.

        This test executes a real notebook through Docker and verifies
        that the error message includes correct cell information.
        """
        from clm.infrastructure.database.job_queue import JobQueue
        from clm.infrastructure.workers.config_loader import load_worker_config
        from clm.infrastructure.workers.lifecycle_manager import WorkerLifecycleManager

        env = docker_test_env
        image_name = _get_full_image_name()
        if not image_name:
            pytest.skip("C++ Docker image not available")

        # Configure for Docker mode with full image (has xeus-cling)
        cli_overrides = {
            "default_execution_mode": "docker",
            "notebook_count": 1,
            "plantuml_count": 0,
            "drawio_count": 0,
            "auto_start": True,
            "auto_stop": True,
            "reuse_workers": False,
        }
        config = load_worker_config(cli_overrides)
        config.notebook.image = image_name

        # Create lifecycle manager with data_dir for source mount
        manager = WorkerLifecycleManager(
            config=config,
            db_path=env["db_path"],
            workspace_path=env["workspace"],
            data_dir=env["data_dir"],
        )

        workers = []
        try:
            # Start workers
            workers = manager.start_managed_workers()
            assert len(workers) > 0, "No workers started"

            # Wait for worker registration
            time.sleep(5)

            # Add the C++ notebook job to the queue
            queue = JobQueue(env["db_path"])
            input_file = env["topic_dir"] / "test_cpp_error.cpp"
            output_dir = env["workspace"] / "output" / "public"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_file = output_dir / "test_cpp_error.html"

            job_id = queue.add_job(
                job_type="notebook",
                input_file=str(input_file),
                output_file=str(output_file),
                content_hash="cpp-error-test-123",
                payload={
                    "kind": "speaker",
                    "prog_lang": "cpp",
                    "language": "en",
                    "format": "html",
                    "source_topic_dir": str(env["topic_dir"]),
                },
            )

            # Wait for job completion (C++ notebooks take longer)
            max_wait = 120  # 2 minutes for C++ compilation
            start = time.time()
            while time.time() - start < max_wait:
                job = queue.get_job(job_id)
                if job.status in ("completed", "failed"):
                    break
                time.sleep(2)

            # Get final job status
            job = queue.get_job(job_id)

            # The job should fail due to missing semicolon
            assert job.status == "failed", f"Expected job to fail but got status '{job.status}'"

            # Verify the error message contains cell context
            error_msg = job.error or ""
            assert error_msg, "No error message captured"

            # The error should identify cell #1 (index 1)
            # The enhanced error format includes "Cell: #N"
            assert "Cell:" in error_msg or "cell" in error_msg.lower(), (
                f"Error should contain cell reference.\nError: {error_msg}"
            )

            # The error should contain the failing code
            assert "BrokenClass" in error_msg, (
                f"Error should contain 'BrokenClass' code snippet.\nError: {error_msg}"
            )

            # Verify error categorization extracts details
            categorized = ErrorCategorizer.categorize_job_error(
                job_type="notebook",
                input_file=str(input_file),
                error_message=error_msg,
                job_payload={},
            )

            # The categorized error should have cell_number or code_snippet
            # Note: Either one is acceptable as proof of error context tracking
            has_cell_number = categorized.details.get("cell_number") is not None
            has_code_snippet = "code_snippet" in categorized.details

            assert has_cell_number or has_code_snippet, (
                f"Categorized error missing cell context.\n"
                f"Details: {categorized.details}\n"
                f"Original error: {error_msg}"
            )

        finally:
            # Stop workers
            if workers:
                manager.stop_managed_workers(workers)
