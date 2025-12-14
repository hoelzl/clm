"""End-to-end tests for course conversion.

These tests verify that courses in test-data/ can be converted into correct outputs.

Test levels:
1. Structure validation (fast) - Validates operation structure without processing
2. Native worker E2E (medium) - Full conversion with subprocess workers
3. Docker worker E2E (slow, future) - Full conversion with docker containers

Run selectively:
- pytest -m e2e                          # All E2E tests
- pytest -m "e2e and not slow"           # Fast E2E tests only
- pytest -m "e2e and slow"               # Slow E2E tests with workers

Environment variables:
- CLX_E2E_TIMEOUT: Timeout in seconds for wait_for_completion (default: 120 for
  tests with workers, 30 for tests without workers). Set to 0 or negative to use
  the default backend timeout of 1200 seconds (20 minutes).

Test fixtures:
- sqlite_backend_with_notebook_workers: Backend with 2 notebook workers for
  processing courses with notebooks. Uses CLX_E2E_TIMEOUT (default: 120s).
- sqlite_backend_with_plantuml_workers: Backend with 2 plantuml workers for
  processing courses with plantuml files. Uses CLX_E2E_TIMEOUT (default: 120s).
- sqlite_backend_with_drawio_workers: Backend with 2 drawio workers for
  processing courses with draw.io files. Uses CLX_E2E_TIMEOUT (default: 120s).
- sqlite_backend_with_all_workers: Backend with 2 workers each for notebook,
  plantuml, and drawio for processing courses with all file types. Uses
  CLX_E2E_TIMEOUT (default: 120s).
- sqlite_backend_without_workers: Backend without any workers for testing
  backend behavior with zero jobs. Uses CLX_E2E_TIMEOUT (default: 30s).
"""

import json
import logging
import tempfile
from importlib.util import find_spec
from pathlib import Path

import pytest

from clx.infrastructure.backends.dummy_backend import DummyBackend
from clx.infrastructure.database.job_queue import JobQueue
from clx.infrastructure.database.schema import init_database
from clx.infrastructure.workers.pool_manager import WorkerPoolManager
from clx.infrastructure.workers.worker_executor import WorkerConfig

logger = logging.getLogger(__name__)


# Check if worker modules are available
def check_worker_module_available(module_name: str) -> bool:
    """Check if a worker module can be imported."""
    try:
        return find_spec(module_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


# Check availability of worker modules
NOTEBOOK_WORKER_AVAILABLE = check_worker_module_available("clx.workers.notebook")


# Check if external tools are available
def check_plantuml_available() -> bool:
    """Check if PlantUML JAR file is available."""
    import os
    import subprocess
    from pathlib import Path

    # Check PLANTUML_JAR environment variable
    jar_path_env = os.environ.get("PLANTUML_JAR")
    if jar_path_env and Path(jar_path_env).exists():
        # Also check if Java is available
        try:
            subprocess.run(["java", "-version"], capture_output=True, check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    # Check default paths
    default_paths = [
        Path("/app/plantuml.jar"),
        Path(__file__).parent.parent.parent
        / "services"
        / "plantuml-converter"
        / "plantuml-1.2024.6.jar",
    ]
    for path in default_paths:
        if path.exists():
            try:
                subprocess.run(["java", "-version"], capture_output=True, check=True)
                return True
            except (subprocess.CalledProcessError, FileNotFoundError):
                return False
    return False


def check_drawio_available() -> bool:
    """Check if drawio executable is available."""
    import os
    import shutil
    from pathlib import Path

    # Check DRAWIO_EXECUTABLE environment variable
    drawio_path = os.environ.get("DRAWIO_EXECUTABLE", "drawio")

    # First check if it's a direct path that exists (handles Windows paths with spaces)
    if Path(drawio_path).exists():
        return True

    # Otherwise use shutil.which for PATH lookup
    return shutil.which(drawio_path) is not None


# Note: These are evaluated at module import time, before pytest_configure runs.
# For proper detection with auto-configuration, tests should call the check functions directly
# in skipif conditions, or these will be False until environment variables are manually set.
PLANTUML_AVAILABLE = check_plantuml_available()
DRAWIO_AVAILABLE = check_drawio_available()


# ============================================================================
# Notebook Output Validation Helpers
# ============================================================================


def validate_notebook_structure(notebook_path: Path) -> dict:
    """Validate that a notebook file has correct Jupyter format.

    Args:
        notebook_path: Path to .ipynb file

    Returns:
        dict: The parsed notebook structure

    Raises:
        AssertionError: If notebook structure is invalid
    """
    assert notebook_path.exists(), f"Notebook does not exist: {notebook_path}"
    assert notebook_path.suffix == ".ipynb", f"Not a notebook file: {notebook_path}"

    # Parse notebook JSON
    with open(notebook_path, encoding="utf-8") as f:
        notebook = json.load(f)

    # Validate basic Jupyter structure
    assert "cells" in notebook, "Notebook missing 'cells' key"
    assert "metadata" in notebook, "Notebook missing 'metadata' key"
    assert "nbformat" in notebook, "Notebook missing 'nbformat' key"
    assert "nbformat_minor" in notebook, "Notebook missing 'nbformat_minor' key"

    # Validate cells structure
    cells = notebook["cells"]
    assert isinstance(cells, list), "Cells must be a list"

    for i, cell in enumerate(cells):
        assert "cell_type" in cell, f"Cell {i} missing 'cell_type'"
        assert cell["cell_type"] in [
            "code",
            "markdown",
        ], f"Cell {i} has invalid type: {cell['cell_type']}"
        assert "source" in cell, f"Cell {i} missing 'source'"
        assert "metadata" in cell, f"Cell {i} missing 'metadata'"

    logger.info(f"Validated notebook {notebook_path.name}: {len(cells)} cells")
    return notebook


def validate_course_output_structure(output_dir: Path, lang: str, course_name: str):
    """Validate the basic output directory structure for a language.

    Args:
        output_dir: Root output directory
        lang: Language code (De or En)
        course_name: Expected course name
    """
    public_dir = output_dir / "public" / lang / course_name
    assert public_dir.exists(), f"Public course directory does not exist: {public_dir}"

    # Check for common subdirectories
    expected_dirs = []
    for dir_name in expected_dirs:
        dir_path = public_dir / dir_name
        # Note: Not all courses have all directories, so we just log

    logger.info(f"Validated output structure for {lang}/{course_name}")
    return public_dir


def count_notebooks_in_dir(directory: Path) -> int:
    """Count .ipynb files recursively in a directory.

    Args:
        directory: Directory to search

    Returns:
        int: Number of .ipynb files found
    """
    if not directory.exists():
        return 0
    return len(list(directory.rglob("*.ipynb")))


def count_html_files_in_dir(directory: Path) -> int:
    """Count .html files recursively in a directory.

    Args:
        directory: Directory to search

    Returns:
        int: Number of .html files found
    """
    if not directory.exists():
        return 0
    return len(list(directory.rglob("*.html")))


def validate_html_file_content(
    html_path: Path, expected_content_snippets: list[str] = None
) -> None:
    """Validate that an HTML file exists and contains expected content.

    Args:
        html_path: Path to .html file
        expected_content_snippets: Optional list of strings that should appear in the HTML

    Raises:
        AssertionError: If HTML file is invalid or missing expected content
    """
    assert html_path.exists(), f"HTML file does not exist: {html_path}"
    assert html_path.suffix == ".html", f"Not an HTML file: {html_path}"

    # Read HTML content
    content = html_path.read_text(encoding="utf-8")

    # Basic validation - should be non-empty
    assert len(content) > 0, f"HTML file is empty: {html_path}"

    # Should contain basic HTML structure
    assert "<html" in content.lower() or "<!doctype html" in content.lower(), (
        f"HTML file missing HTML structure: {html_path}"
    )

    # Check for expected content snippets if provided
    if expected_content_snippets:
        for snippet in expected_content_snippets:
            assert snippet in content, (
                f"HTML file missing expected content '{snippet}': {html_path}"
            )

    logger.info(f"Validated HTML file {html_path.name}: {len(content)} bytes")


def validate_notebook_file_content(
    notebook_path: Path, expected_content_snippets: list[str] = None
) -> dict:
    """Validate that a notebook file exists and contains expected content.

    Args:
        notebook_path: Path to .ipynb file
        expected_content_snippets: Optional list of strings that should appear in notebook cells

    Returns:
        dict: The parsed notebook structure

    Raises:
        AssertionError: If notebook is invalid or missing expected content
    """
    # First validate structure
    notebook = validate_notebook_structure(notebook_path)

    # Check for expected content snippets if provided
    if expected_content_snippets:
        # Collect all cell source text
        all_source = []
        for cell in notebook["cells"]:
            source = cell.get("source", [])
            if isinstance(source, list):
                all_source.extend(source)
            else:
                all_source.append(source)

        full_content = "".join(all_source)

        for snippet in expected_content_snippets:
            assert snippet in full_content, (
                f"Notebook missing expected content '{snippet}': {notebook_path}"
            )

    return notebook


# ============================================================================
# Level 1: Structure Validation Tests (Fast, No Workers)
# ============================================================================


@pytest.mark.e2e
async def test_course_1_conversion_structure(e2e_course_1):
    """Validate course 1 creates correct operation structure without processing.

    This test uses DummyBackend to verify the course processing pipeline
    creates the right operations without actually executing them.
    """
    course = e2e_course_1

    # Verify course structure was built correctly
    assert len(course.sections) == 2, "Course 1 should have 2 sections"
    assert len(course.topics) == 3, "Course 1 should have 3 topics"
    assert len(course.dir_groups) == 3, (
        "Course 1 should have 3 dir groups (Code/Solutions, Bonus, root-files)"
    )

    # Verify files were discovered
    files = course.files
    assert len(files) > 0, "Course should have discovered files"

    # Count notebooks
    notebooks = course.notebooks
    assert len(notebooks) == 3, f"Course 1 should have 3 notebooks, found {len(notebooks)}"

    # Verify notebook titles were extracted
    for notebook in notebooks:
        assert notebook.title.de, f"Notebook {notebook.path} missing German title"
        assert notebook.title.en, f"Notebook {notebook.path} missing English title"

    # Process with DummyBackend (no actual execution)
    async with DummyBackend() as backend:
        await course.process_all(backend)

    logger.info("Course 1 structure validation completed successfully")


@pytest.mark.e2e
async def test_course_2_conversion_structure(e2e_course_2):
    """Validate course 2 creates correct operation structure without processing.

    This test uses DummyBackend to verify the course processing pipeline
    creates the right operations without actually executing them.
    """
    course = e2e_course_2

    # Verify course structure was built correctly
    assert len(course.sections) == 1, "Course 2 should have 1 section"
    assert len(course.topics) == 1, "Course 2 should have 1 topic"

    # Verify files were discovered
    files = course.files
    assert len(files) > 0, "Course should have discovered files"

    # Count notebooks
    notebooks = course.notebooks
    assert len(notebooks) == 1, f"Course 2 should have 1 notebook, found {len(notebooks)}"

    # Verify notebook titles were extracted
    for notebook in notebooks:
        assert notebook.title.de, f"Notebook {notebook.path} missing German title"
        assert notebook.title.en, f"Notebook {notebook.path} missing English title"

    # Process with DummyBackend (no actual execution)
    async with DummyBackend() as backend:
        await course.process_all(backend)

    logger.info("Course 2 structure validation completed successfully")


@pytest.mark.e2e
async def test_course_dir_groups_structure(e2e_course_1):
    """Validate directory groups are discovered and structured correctly."""
    course = e2e_course_1

    # Course 1 has 3 dir groups: Code/Solutions (from topic), Bonus, and root-files
    assert len(course.dir_groups) == 3, "Course 1 should have 3 dir groups"

    dir_group_names = [dg.name.en for dg in course.dir_groups]
    assert "Code/Solutions" in dir_group_names, "Should have 'Code/Solutions' dir group"
    assert "Bonus" in dir_group_names, "Should have 'Bonus' dir group"
    assert "" in dir_group_names, "Should have empty name dir group for root files"

    # Verify dir group paths are correct
    bonus_group = next(dg for dg in course.dir_groups if dg.name.en == "Bonus")
    assert len(bonus_group.source_dirs) > 0, "Bonus group should have source directories"

    logger.info("Directory groups structure validation completed successfully")


# ============================================================================
# Level 2: Native Worker E2E Tests (Medium, Requires Workers)
# ============================================================================


@pytest.fixture
async def db_path_fixture():
    """Create a temporary database for E2E tests."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        path = Path(f.name)

    init_database(path)
    yield path

    # Cleanup - the caller is responsible for closing any JobQueue/connections
    # before this fixture tears down
    try:
        path.unlink(missing_ok=True)
        for suffix in ["-wal", "-shm"]:
            wal_file = Path(str(path) + suffix)
            wal_file.unlink(missing_ok=True)
    except Exception:
        pass


@pytest.fixture
async def workspace_path_fixture(tmp_path):
    """Create a temporary workspace directory."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


@pytest.fixture
async def sqlite_backend_with_notebook_workers(db_path_fixture, workspace_path_fixture, request):
    """Create SqliteBackend with notebook worker pool for E2E testing.

    This fixture starts actual worker processes that can convert notebooks.

    Environment variables:
    - CLX_E2E_TIMEOUT: Timeout in seconds (default: 120). Set to 0 or negative to use default backend timeout (1200s).
    """
    import os

    from clx.infrastructure.backends.sqlite_backend import SqliteBackend

    # Register paths for diagnostic output on test failure
    if hasattr(request.node, "set_diagnostic_db_path"):
        request.node.set_diagnostic_db_path(db_path_fixture)
    if hasattr(request.node, "set_diagnostic_workspace_path"):
        request.node.set_diagnostic_workspace_path(workspace_path_fixture)

    # Get timeout from environment variable, default to 120 seconds (2 minutes) for tests
    # Set to 0 or negative to disable timeout
    timeout = float(os.environ.get("CLX_E2E_TIMEOUT", "120"))
    if timeout <= 0:
        timeout = 1200.0  # Default backend timeout (20 minutes)

    logger.info(f"Creating SqliteBackend with timeout={timeout}s, db={db_path_fixture}")

    # Create backend
    backend = SqliteBackend(
        db_path=db_path_fixture,
        workspace_path=workspace_path_fixture,
        ignore_db=True,  # Don't use cache for E2E tests
        max_wait_for_completion_duration=timeout,
    )

    # Create worker pool manager
    config = WorkerConfig(
        worker_type="notebook",
        count=2,  # Use 2 workers for parallel processing
        execution_mode="direct",
    )

    logger.info("Starting notebook worker pool (2 workers)")
    manager = WorkerPoolManager(
        db_path=db_path_fixture, workspace_path=workspace_path_fixture, worker_configs=[config]
    )

    # Start workers
    manager.start_pools()

    # Give workers time to start up and register
    import asyncio

    await asyncio.sleep(2)
    logger.info("Workers started and registered")

    async with backend:
        yield backend

    # Cleanup - stop workers and close database connections
    logger.info("Stopping worker pool")
    manager.stop_pools()
    manager.close()


@pytest.fixture
async def sqlite_backend_without_workers(db_path_fixture, workspace_path_fixture, request):
    """Create SqliteBackend without workers for E2E testing.

    This fixture is used for courses that don't require any processing
    (e.g., for testing backend behavior with zero jobs). Since no workers are
    needed, this avoids the overhead and potential hanging issues with worker
    initialization and cleanup.

    Environment variables:
    - CLX_E2E_TIMEOUT: Timeout in seconds (default: 30). Set to 0 or negative to use default backend timeout (1200s).
    """
    import os

    from clx.infrastructure.backends.sqlite_backend import SqliteBackend

    # Register paths for diagnostic output on test failure
    if hasattr(request.node, "set_diagnostic_db_path"):
        request.node.set_diagnostic_db_path(db_path_fixture)
    if hasattr(request.node, "set_diagnostic_workspace_path"):
        request.node.set_diagnostic_workspace_path(workspace_path_fixture)

    # Get timeout from environment variable, default to 30 seconds for non-worker tests
    # Set to 0 or negative to disable timeout
    timeout = float(os.environ.get("CLX_E2E_TIMEOUT", "30"))
    if timeout <= 0:
        timeout = 1200.0  # Default backend timeout (20 minutes)

    logger.info(f"Creating SqliteBackend (no workers) with timeout={timeout}s")

    # Create backend without starting any workers
    backend = SqliteBackend(
        db_path=db_path_fixture,
        workspace_path=workspace_path_fixture,
        ignore_db=True,  # Don't use cache for E2E tests
        max_wait_for_completion_duration=timeout,
    )

    async with backend:
        yield backend

    # No worker cleanup needed since we didn't start any


@pytest.fixture
async def sqlite_backend_with_plantuml_workers(db_path_fixture, workspace_path_fixture, request):
    """Create SqliteBackend with plantuml worker pool for E2E testing.

    This fixture starts actual worker processes that can convert plantuml files.

    Environment variables:
    - CLX_E2E_TIMEOUT: Timeout in seconds (default: 120). Set to 0 or negative to use default backend timeout (1200s).
    """
    import os

    from clx.infrastructure.backends.sqlite_backend import SqliteBackend

    # Register paths for diagnostic output on test failure
    if hasattr(request.node, "set_diagnostic_db_path"):
        request.node.set_diagnostic_db_path(db_path_fixture)
    if hasattr(request.node, "set_diagnostic_workspace_path"):
        request.node.set_diagnostic_workspace_path(workspace_path_fixture)

    # Get timeout from environment variable, default to 120 seconds (2 minutes) for tests
    timeout = float(os.environ.get("CLX_E2E_TIMEOUT", "120"))
    if timeout <= 0:
        timeout = 1200.0  # Default backend timeout (20 minutes)

    logger.info(f"Creating SqliteBackend with timeout={timeout}s, db={db_path_fixture}")

    # Create backend
    backend = SqliteBackend(
        db_path=db_path_fixture,
        workspace_path=workspace_path_fixture,
        ignore_db=True,  # Don't use cache for E2E tests
        max_wait_for_completion_duration=timeout,
    )

    # Create worker pool manager for plantuml workers
    config = WorkerConfig(
        worker_type="plantuml",
        count=2,  # Use 2 workers for parallel processing
        execution_mode="direct",
    )

    logger.info("Starting plantuml worker pool (2 workers)")
    manager = WorkerPoolManager(
        db_path=db_path_fixture, workspace_path=workspace_path_fixture, worker_configs=[config]
    )

    # Start workers
    manager.start_pools()

    # Give workers time to start up and register
    import asyncio

    await asyncio.sleep(2)
    logger.info("Workers started and registered")

    async with backend:
        yield backend

    # Cleanup - stop workers and close database connections
    logger.info("Stopping worker pool")
    manager.stop_pools()
    manager.close()


@pytest.fixture
async def sqlite_backend_with_drawio_workers(db_path_fixture, workspace_path_fixture, request):
    """Create SqliteBackend with draw.io worker pool for E2E testing.

    This fixture starts actual worker processes that can convert draw.io files.

    Environment variables:
    - CLX_E2E_TIMEOUT: Timeout in seconds (default: 120). Set to 0 or negative to use default backend timeout (1200s).
    """
    import os

    from clx.infrastructure.backends.sqlite_backend import SqliteBackend

    # Register paths for diagnostic output on test failure
    if hasattr(request.node, "set_diagnostic_db_path"):
        request.node.set_diagnostic_db_path(db_path_fixture)
    if hasattr(request.node, "set_diagnostic_workspace_path"):
        request.node.set_diagnostic_workspace_path(workspace_path_fixture)

    # Get timeout from environment variable, default to 120 seconds (2 minutes) for tests
    timeout = float(os.environ.get("CLX_E2E_TIMEOUT", "120"))
    if timeout <= 0:
        timeout = 1200.0  # Default backend timeout (20 minutes)

    logger.info(f"Creating SqliteBackend with timeout={timeout}s, db={db_path_fixture}")

    # Create backend
    backend = SqliteBackend(
        db_path=db_path_fixture,
        workspace_path=workspace_path_fixture,
        ignore_db=True,  # Don't use cache for E2E tests
        max_wait_for_completion_duration=timeout,
    )

    # Create worker pool manager for drawio workers
    config = WorkerConfig(
        worker_type="drawio",
        count=2,  # Use 2 workers for parallel processing
        execution_mode="direct",
    )

    logger.info("Starting drawio worker pool (2 workers)")
    manager = WorkerPoolManager(
        db_path=db_path_fixture, workspace_path=workspace_path_fixture, worker_configs=[config]
    )

    # Start workers
    manager.start_pools()

    # Give workers time to start up and register
    import asyncio

    await asyncio.sleep(2)
    logger.info("Workers started and registered")

    async with backend:
        yield backend

    # Cleanup - stop workers and close database connections
    logger.info("Stopping worker pool")
    manager.stop_pools()
    manager.close()


@pytest.fixture
async def sqlite_backend_with_all_workers(db_path_fixture, workspace_path_fixture, request):
    """Create SqliteBackend with notebook, plantuml, and drawio worker pools for E2E testing.

    This fixture starts actual worker processes that can convert all file types.

    Environment variables:
    - CLX_E2E_TIMEOUT: Timeout in seconds (default: 120). Set to 0 or negative to use default backend timeout (1200s).
    """
    import os

    from clx.infrastructure.backends.sqlite_backend import SqliteBackend

    # Register paths for diagnostic output on test failure
    if hasattr(request.node, "set_diagnostic_db_path"):
        request.node.set_diagnostic_db_path(db_path_fixture)
    if hasattr(request.node, "set_diagnostic_workspace_path"):
        request.node.set_diagnostic_workspace_path(workspace_path_fixture)

    # Get timeout from environment variable, default to 120 seconds (2 minutes) for tests
    timeout = float(os.environ.get("CLX_E2E_TIMEOUT", "120"))
    if timeout <= 0:
        timeout = 1200.0  # Default backend timeout (20 minutes)

    logger.info(f"Creating SqliteBackend with timeout={timeout}s, db={db_path_fixture}")

    # Create backend
    backend = SqliteBackend(
        db_path=db_path_fixture,
        workspace_path=workspace_path_fixture,
        ignore_db=True,  # Don't use cache for E2E tests
        max_wait_for_completion_duration=timeout,
    )

    # Create worker pool manager with all worker types
    configs = [
        WorkerConfig(
            worker_type="notebook",
            count=8,  # Use 8 workers for faster parallel processing of multiple notebooks
            execution_mode="direct",
        ),
        WorkerConfig(worker_type="plantuml", count=2, execution_mode="direct"),
        WorkerConfig(worker_type="drawio", count=2, execution_mode="direct"),
    ]

    logger.info("Starting all worker pools (8 notebook, 2 plantuml, 2 drawio)")
    manager = WorkerPoolManager(
        db_path=db_path_fixture, workspace_path=workspace_path_fixture, worker_configs=configs
    )

    # Start workers
    manager.start_pools()

    # Give workers time to start up and register
    import asyncio

    await asyncio.sleep(2)
    logger.info("Workers started and registered (12 total)")

    async with backend:
        yield backend

    # Cleanup - stop workers and close database connections
    logger.info("Stopping worker pools")
    manager.stop_pools()
    manager.close()


@pytest.mark.e2e
@pytest.mark.skipif(not NOTEBOOK_WORKER_AVAILABLE, reason="Notebook worker module not available")
async def test_course_1_notebooks_native_workers(e2e_course_1, sqlite_backend_with_all_workers):
    """Full E2E test: Convert course 1 notebooks using native workers.

    This test:
    1. Copies test-data to temp directory
    2. Processes course with SqliteBackend and worker pool
    3. Validates generated notebooks exist and have correct format
    4. Validates multilingual outputs (de/en)
    """
    course = e2e_course_1
    backend = sqlite_backend_with_all_workers

    # Verify we have notebooks to process
    notebooks = course.notebooks
    assert len(notebooks) == 3, f"Should have 3 notebooks, found {len(notebooks)}"

    # Process all course files
    logger.info("Starting course processing with native workers...")
    await course.process_all(backend)

    # Wait for all jobs to complete
    logger.info("Waiting for job completion...")
    completed = await backend.wait_for_completion()
    assert completed, "Not all jobs completed successfully"

    # Validate output structure for both languages
    output_dir = course.output_root

    # German output - Course 1 has 3 notebooks, each generates participant + speaker variants
    de_dir = validate_course_output_structure(output_dir, "De", "Mein Kurs")
    de_notebook_count = count_notebooks_in_dir(de_dir)
    de_html_count = count_html_files_in_dir(de_dir)
    # 3 source notebooks * 2 variants (participant + speaker) = 6 minimum expected
    assert de_notebook_count >= 6, (
        f"Expected at least 6 German notebooks (3 sources * 2 variants), got {de_notebook_count}"
    )
    assert de_html_count >= 6, f"Expected at least 6 German HTML files, got {de_html_count}"
    logger.info(f"Found {de_notebook_count} German notebooks and {de_html_count} HTML files")

    # English output - same expectations as German
    en_dir = validate_course_output_structure(output_dir, "En", "My Course")
    en_notebook_count = count_notebooks_in_dir(en_dir)
    en_html_count = count_html_files_in_dir(en_dir)
    assert en_notebook_count >= 6, (
        f"Expected at least 6 English notebooks (3 sources * 2 variants), got {en_notebook_count}"
    )
    assert en_html_count >= 6, f"Expected at least 6 English HTML files, got {en_html_count}"
    logger.info(f"Found {en_notebook_count} English notebooks and {en_html_count} HTML files")

    # Validate at least one notebook has correct Jupyter structure and content
    de_notebooks = list(de_dir.rglob("*.ipynb"))
    if de_notebooks:
        first_notebook = de_notebooks[0]
        notebook_data = validate_notebook_file_content(
            first_notebook,
            expected_content_snippets=["Folien von Test 1"],  # German title from test data
        )
        assert len(notebook_data["cells"]) > 0, "Notebook should have cells"

    # Validate at least one HTML file exists and has content
    de_html_files = list(de_dir.rglob("*.html"))
    if de_html_files:
        first_html = de_html_files[0]
        validate_html_file_content(
            first_html,
            expected_content_snippets=["Folien von Test 1"],  # German title from test data
        )

    logger.info("Course 1 native worker E2E test completed successfully")


@pytest.mark.e2e
@pytest.mark.skipif(not NOTEBOOK_WORKER_AVAILABLE, reason="Notebook worker module not available")
async def test_course_2_notebooks_native_workers(
    e2e_course_2, sqlite_backend_with_notebook_workers
):
    """Full E2E test: Convert course 2 notebooks using native workers.

    This test validates a simpler course with fewer topics.
    """
    course = e2e_course_2
    backend = sqlite_backend_with_notebook_workers

    # Verify we have notebooks to process
    notebooks = course.notebooks
    assert len(notebooks) == 1, f"Should have 1 notebook, found {len(notebooks)}"

    # Process all course files
    logger.info("Starting course 2 processing with native workers...")
    await course.process_all(backend)

    # Wait for all jobs to complete
    logger.info("Waiting for job completion...")
    completed = await backend.wait_for_completion()
    assert completed, "Not all jobs completed successfully"

    # Validate output structure for both languages
    output_dir = course.output_root

    # German output - Course 2 has 1 notebook, generates participant + speaker variants
    de_dir = validate_course_output_structure(output_dir, "De", "Kurs 2")
    de_notebook_count = count_notebooks_in_dir(de_dir)
    de_html_count = count_html_files_in_dir(de_dir)
    # 1 source notebook * 2 variants (participant + speaker) = 2 minimum expected
    assert de_notebook_count >= 2, (
        f"Expected at least 2 German notebooks (1 source * 2 variants), got {de_notebook_count}"
    )
    assert de_html_count >= 2, f"Expected at least 2 German HTML files, got {de_html_count}"
    logger.info(f"Found {de_notebook_count} German notebooks and {de_html_count} HTML files")

    # English output - same expectations as German
    en_dir = validate_course_output_structure(output_dir, "En", "Kurs 2")
    en_notebook_count = count_notebooks_in_dir(en_dir)
    en_html_count = count_html_files_in_dir(en_dir)
    assert en_notebook_count >= 2, (
        f"Expected at least 2 English notebooks (1 source * 2 variants), got {en_notebook_count}"
    )
    assert en_html_count >= 2, f"Expected at least 2 English HTML files, got {en_html_count}"
    logger.info(f"Found {en_notebook_count} English notebooks and {en_html_count} HTML files")

    logger.info("Course 2 native worker E2E test completed successfully")


@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.skipif(not NOTEBOOK_WORKER_AVAILABLE, reason="Notebook worker module not available")
async def test_course_dir_groups_copy_e2e(e2e_course_1, sqlite_backend_with_all_workers):
    """Test that directory groups are copied correctly in full E2E scenario.

    This validates that bonus materials and root files are copied to the
    correct locations in the output directory.
    """
    course = e2e_course_1
    backend = sqlite_backend_with_all_workers

    # Process all course files (including dir groups)
    await course.process_all(backend)

    # Wait for completion
    completed = await backend.wait_for_completion()
    assert completed, "Not all jobs completed successfully"

    output_dir = course.output_root

    # Validate German outputs
    de_course_dir = output_dir / "public" / "De" / "Mein Kurs"

    # Check Bonus directory group
    bonus_dir = de_course_dir / "Bonus"
    assert bonus_dir.exists(), "Bonus directory should exist"
    assert (bonus_dir / "workshops-toplevel.txt").exists(), (
        "workshops-toplevel.txt should be copied"
    )
    assert (bonus_dir / "Workshop-1" / "workshop-1.txt").exists(), (
        "Workshop subdirectory should be copied"
    )

    # Check root files directory group (empty name)
    assert (de_course_dir / "root-file-1.txt").exists(), "root-file-1.txt should be in course root"
    assert (de_course_dir / "root-file-2").exists(), "root-file-2 should be in course root"

    # Validate English outputs
    en_course_dir = output_dir / "public" / "En" / "My Course"
    assert en_course_dir.exists(), "English course directory should exist"
    assert (en_course_dir / "Bonus").exists(), "English Bonus directory should exist"
    assert (en_course_dir / "root-file-1.txt").exists(), "English root files should be copied"

    logger.info("Directory groups E2E test completed successfully")


# ============================================================================
# Edge Case Tests: Single File Courses
# ============================================================================


@pytest.mark.e2e
async def test_course_3_single_notebook_structure(e2e_course_3):
    """Validate course 3 (single notebook only) creates correct structure.

    This test validates that a course with just one notebook works correctly.
    """
    course = e2e_course_3

    # Verify course structure
    assert len(course.sections) == 1, "Course 3 should have 1 section"
    assert len(course.topics) == 1, "Course 3 should have 1 topic"

    # Verify files were discovered
    files = course.files
    assert len(files) > 0, "Course should have discovered files"

    # Count notebooks - should have exactly 1
    notebooks = course.notebooks
    assert len(notebooks) == 1, f"Course 3 should have 1 notebook, found {len(notebooks)}"

    # Verify notebook titles were extracted
    notebook = notebooks[0]
    assert notebook.title.de, f"Notebook {notebook.path} missing German title"
    assert notebook.title.en, f"Notebook {notebook.path} missing English title"

    # Process with DummyBackend (no actual execution)
    async with DummyBackend() as backend:
        await course.process_all(backend)

    logger.info("Course 3 (single notebook) structure validation completed successfully")


@pytest.mark.e2e
async def test_course_4_single_plantuml_structure(e2e_course_4):
    """Validate course 4 (single plantuml only) creates correct structure.

    This test validates that a course with just one plantuml file works correctly.
    Edge case: Topic with no notebooks, only a plantuml file.
    """
    course = e2e_course_4

    # Verify course structure
    assert len(course.sections) == 1, "Course 4 should have 1 section"
    assert len(course.topics) == 1, "Course 4 should have 1 topic"

    # Verify files were discovered
    files = course.files
    assert len(files) > 0, "Course should have discovered files"

    # Count notebooks - should have 0 (only plantuml file)
    notebooks = course.notebooks
    assert len(notebooks) == 0, f"Course 4 should have 0 notebooks, found {len(notebooks)}"

    # Verify we have plantuml files
    from clx.core.course_files.plantuml_file import PlantUmlFile

    plantuml_files = [file for file in files if isinstance(file, PlantUmlFile)]
    assert len(plantuml_files) == 1, (
        f"Course 4 should have 1 plantuml file, found {len(plantuml_files)}"
    )

    # Process with DummyBackend (no actual execution)
    async with DummyBackend() as backend:
        await course.process_all(backend)

    logger.info("Course 4 (single plantuml) structure validation completed successfully")


@pytest.mark.e2e
async def test_course_5_single_drawio_structure(e2e_course_5):
    """Validate course 5 (single draw.io only) creates correct structure.

    This test validates that a course with just one draw.io file works correctly.
    Edge case: Topic with no notebooks, only a draw.io file.
    """
    course = e2e_course_5

    # Verify course structure
    assert len(course.sections) == 1, "Course 5 should have 1 section"
    assert len(course.topics) == 1, "Course 5 should have 1 topic"

    # Verify files were discovered
    files = course.files
    assert len(files) > 0, "Course should have discovered files"

    # Count notebooks - should have 0 (only draw.io file)
    notebooks = course.notebooks
    assert len(notebooks) == 0, f"Course 5 should have 0 notebooks, found {len(notebooks)}"

    # Verify we have draw.io files
    from clx.core.course_files.drawio_file import DrawIoFile

    drawio_files = [file for file in files if isinstance(file, DrawIoFile)]
    assert len(drawio_files) == 1, f"Course 5 should have 1 draw.io file, found {len(drawio_files)}"

    # Process with DummyBackend (no actual execution)
    async with DummyBackend() as backend:
        await course.process_all(backend)

    logger.info("Course 5 (single draw.io) structure validation completed successfully")


@pytest.mark.e2e
@pytest.mark.skipif(not NOTEBOOK_WORKER_AVAILABLE, reason="Notebook worker module not available")
async def test_course_3_single_notebook_e2e(e2e_course_3, sqlite_backend_with_notebook_workers):
    """Full E2E test: Convert course 3 (single notebook) using native workers.

    This test validates that a minimal course with just one notebook
    converts successfully without hanging or failing.
    """
    course = e2e_course_3
    backend = sqlite_backend_with_notebook_workers

    # Verify we have notebooks to process
    notebooks = course.notebooks
    assert len(notebooks) == 1, f"Should have 1 notebook, found {len(notebooks)}"

    # Process all course files
    logger.info("Starting course 3 (single notebook) processing with native workers...")
    await course.process_all(backend)

    # Wait for all jobs to complete
    logger.info("Waiting for job completion...")
    completed = await backend.wait_for_completion()
    assert completed, "Not all jobs completed successfully"

    # Validate output structure for both languages
    output_dir = course.output_root

    # German output - Course 3 has 1 notebook, generates participant + speaker variants
    de_dir = validate_course_output_structure(output_dir, "De", "Einfaches Notebook")
    de_notebook_count = count_notebooks_in_dir(de_dir)
    de_html_count = count_html_files_in_dir(de_dir)
    # 1 source notebook * 2 variants (participant + speaker) = 2 minimum expected
    assert de_notebook_count >= 2, (
        f"Expected at least 2 German notebooks (1 source * 2 variants), got {de_notebook_count}"
    )
    assert de_html_count >= 2, f"Expected at least 2 German HTML files, got {de_html_count}"
    logger.info(f"Found {de_notebook_count} German notebooks and {de_html_count} HTML files")

    # English output - same expectations as German
    en_dir = validate_course_output_structure(output_dir, "En", "Simple Notebook")
    en_notebook_count = count_notebooks_in_dir(en_dir)
    en_html_count = count_html_files_in_dir(en_dir)
    assert en_notebook_count >= 2, (
        f"Expected at least 2 English notebooks (1 source * 2 variants), got {en_notebook_count}"
    )
    assert en_html_count >= 2, f"Expected at least 2 English HTML files, got {en_html_count}"
    logger.info(f"Found {en_notebook_count} English notebooks and {en_html_count} HTML files")

    # Validate notebook has correct Jupyter structure
    de_notebooks = list(de_dir.rglob("*.ipynb"))
    assert len(de_notebooks) >= 2, f"Expected at least 2 German notebooks, got {len(de_notebooks)}"
    first_notebook = de_notebooks[0]
    notebook_data = validate_notebook_structure(first_notebook)
    assert len(notebook_data["cells"]) > 0, "Notebook should have cells"

    # Validate HTML files exist and have content
    de_html_files = list(de_dir.rglob("*.html"))
    assert len(de_html_files) >= 2, (
        f"Expected at least 2 German HTML files, got {len(de_html_files)}"
    )
    first_html = de_html_files[0]
    validate_html_file_content(first_html)

    logger.info("Course 3 (single notebook) E2E test completed successfully")


@pytest.mark.e2e
@pytest.mark.skipif(
    not PLANTUML_AVAILABLE,
    reason="PlantUML JAR file or Java not available. Set PLANTUML_JAR environment variable or install Java.",
)
async def test_course_4_single_plantuml_e2e(e2e_course_4, sqlite_backend_with_plantuml_workers):
    """Full E2E test: Convert course 4 (single plantuml) with plantuml workers.

    This test validates that a course with only a plantuml file (no notebooks)
    processes correctly without hanging. PlantUML files require workers for
    conversion, so we use a backend with plantuml workers.
    """
    course = e2e_course_4
    backend = sqlite_backend_with_plantuml_workers

    # Verify we have no notebooks to process
    notebooks = course.notebooks
    assert len(notebooks) == 0, f"Should have 0 notebooks, found {len(notebooks)}"

    # Process all course files
    logger.info("Starting course 4 (single plantuml) processing with plantuml workers...")
    await course.process_all(backend)

    # Wait for all jobs to complete
    logger.info("Waiting for job completion...")
    completed = await backend.wait_for_completion()
    assert completed, "Not all jobs completed successfully"

    # Validate output structure exists
    output_dir = course.output_root
    de_dir = validate_course_output_structure(output_dir, "De", "Einfaches PlantUML")
    en_dir = validate_course_output_structure(output_dir, "En", "Simple PlantUML")

    # Check that plantuml images were generated
    de_images = list(de_dir.rglob("*.png"))
    logger.info(f"Found {len(de_images)} German images (from plantuml)")

    en_images = list(en_dir.rglob("*.png"))
    logger.info(f"Found {len(en_images)} English images (from plantuml)")

    logger.info("Course 4 (single plantuml) E2E test completed successfully")


@pytest.mark.e2e
@pytest.mark.skipif(
    not DRAWIO_AVAILABLE,
    reason="Drawio executable not available. Set DRAWIO_EXECUTABLE environment variable or install drawio.",
)
async def test_course_5_single_drawio_e2e(e2e_course_5, sqlite_backend_with_drawio_workers):
    """Full E2E test: Convert course 5 (single draw.io) with drawio workers.

    This test validates that a course with only a draw.io file (no notebooks)
    processes correctly without hanging. Draw.io files require workers for
    conversion, so we use a backend with drawio workers.
    """
    course = e2e_course_5
    backend = sqlite_backend_with_drawio_workers

    # Verify we have no notebooks to process
    notebooks = course.notebooks
    assert len(notebooks) == 0, f"Should have 0 notebooks, found {len(notebooks)}"

    # Process all course files
    logger.info("Starting course 5 (single draw.io) processing with drawio workers...")
    await course.process_all(backend)

    # Wait for all jobs to complete
    logger.info("Waiting for job completion...")
    completed = await backend.wait_for_completion()
    assert completed, "Not all jobs completed successfully"

    # Validate output structure exists
    output_dir = course.output_root
    de_dir = validate_course_output_structure(output_dir, "De", "Einfaches Drawio")
    en_dir = validate_course_output_structure(output_dir, "En", "Simple Drawio")

    # Check that draw.io images were generated
    de_images = list(de_dir.rglob("*.png"))
    logger.info(f"Found {len(de_images)} German images (from draw.io)")

    en_images = list(en_dir.rglob("*.png"))
    logger.info(f"Found {len(en_images)} English images (from draw.io)")

    logger.info("Course 5 (single draw.io) E2E test completed successfully")


# ============================================================================
# Level 3: Docker Worker E2E Tests (Slow, Future Implementation)
# ============================================================================

# TODO: Add docker worker tests when docker support is ready
# @pytest.mark.e2e
# @pytest.mark.integration
# @pytest.mark.slow
# async def test_course_conversion_docker_workers(...):
#     """Full E2E test with docker containers for workers."""
#     pass
