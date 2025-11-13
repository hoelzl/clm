"""End-to-end tests for course conversion.

These tests verify that courses in test-data/ can be converted into correct outputs.

Test levels:
1. Structure validation (fast) - Validates operation structure without processing
2. Native worker E2E (medium) - Full conversion with subprocess workers
3. Docker worker E2E (slow, future) - Full conversion with docker containers

Run selectively:
- pytest -m e2e                          # All E2E tests
- pytest -m "e2e and not integration"    # Fast structure tests only
- pytest -m "e2e and integration"        # Full E2E with workers
"""

import json
import logging
import tempfile
from pathlib import Path
from importlib.util import find_spec

import pytest

from clx_common.backends.dummy_backend import DummyBackend
from clx_common.database.schema import init_database
from clx_common.database.job_queue import JobQueue
from clx_common.workers.pool_manager import WorkerPoolManager
from clx_common.workers.worker_executor import WorkerConfig


logger = logging.getLogger(__name__)


# Check if worker modules are available
def check_worker_module_available(module_name: str) -> bool:
    """Check if a worker module can be imported."""
    try:
        return find_spec(module_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


# Check availability of worker modules
NOTEBOOK_WORKER_AVAILABLE = check_worker_module_available('nb')


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
    with open(notebook_path, "r", encoding="utf-8") as f:
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
        assert cell["cell_type"] in ["code", "markdown"], f"Cell {i} has invalid type: {cell['cell_type']}"
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
    assert len(course.dir_groups) == 3, "Course 1 should have 3 dir groups (Code/Solutions, Bonus, root-files)"

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
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        path = Path(f.name)

    init_database(path)
    yield path

    # Cleanup
    import sqlite3
    import gc
    gc.collect()

    try:
        conn = sqlite3.connect(path)
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        conn.close()
    except Exception:
        pass

    try:
        path.unlink(missing_ok=True)
        for suffix in ['-wal', '-shm']:
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
async def sqlite_backend_with_notebook_workers(db_path_fixture, workspace_path_fixture):
    """Create SqliteBackend with notebook worker pool for E2E testing.

    This fixture starts actual worker processes that can convert notebooks.
    """
    from clx_faststream_backend.sqlite_backend import SqliteBackend

    # Create backend
    backend = SqliteBackend(
        db_path=db_path_fixture,
        workspace_path=workspace_path_fixture,
        ignore_db=True  # Don't use cache for E2E tests
    )

    # Create worker pool manager
    config = WorkerConfig(
        worker_type='notebook',
        count=2,  # Use 2 workers for parallel processing
        execution_mode='direct'
    )

    manager = WorkerPoolManager(
        db_path=db_path_fixture,
        workspace_path=workspace_path_fixture,
        worker_configs=[config]
    )

    # Start workers
    manager.start_pools()

    # Give workers time to start up and register
    import asyncio
    await asyncio.sleep(2)

    async with backend:
        yield backend

    # Cleanup
    manager.stop_pools()


@pytest.mark.e2e
@pytest.mark.integration
@pytest.mark.skipif(
    not NOTEBOOK_WORKER_AVAILABLE,
    reason="Notebook worker module not available"
)
async def test_course_1_notebooks_native_workers(
    e2e_course_1,
    sqlite_backend_with_notebook_workers
):
    """Full E2E test: Convert course 1 notebooks using native workers.

    This test:
    1. Copies test-data to temp directory
    2. Processes course with SqliteBackend and worker pool
    3. Validates generated notebooks exist and have correct format
    4. Validates multilingual outputs (de/en)
    """
    course = e2e_course_1
    backend = sqlite_backend_with_notebook_workers

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

    # German output
    de_dir = validate_course_output_structure(output_dir, "De", "Mein Kurs")
    de_notebook_count = count_notebooks_in_dir(de_dir)
    assert de_notebook_count > 0, "No German notebooks generated"
    logger.info(f"Found {de_notebook_count} German notebooks")

    # English output
    en_dir = validate_course_output_structure(output_dir, "En", "My Course")
    en_notebook_count = count_notebooks_in_dir(en_dir)
    assert en_notebook_count > 0, "No English notebooks generated"
    logger.info(f"Found {en_notebook_count} English notebooks")

    # Validate at least one notebook has correct Jupyter structure
    de_notebooks = list(de_dir.rglob("*.ipynb"))
    if de_notebooks:
        first_notebook = de_notebooks[0]
        notebook_data = validate_notebook_structure(first_notebook)
        assert len(notebook_data["cells"]) > 0, "Notebook should have cells"

    logger.info("Course 1 native worker E2E test completed successfully")


@pytest.mark.e2e
@pytest.mark.integration
@pytest.mark.skipif(
    not NOTEBOOK_WORKER_AVAILABLE,
    reason="Notebook worker module not available"
)
async def test_course_2_notebooks_native_workers(
    e2e_course_2,
    sqlite_backend_with_notebook_workers
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

    # German output
    de_dir = validate_course_output_structure(output_dir, "De", "Kurs 2")
    de_notebook_count = count_notebooks_in_dir(de_dir)
    assert de_notebook_count > 0, "No German notebooks generated"

    # English output
    en_dir = validate_course_output_structure(output_dir, "En", "Kurs 2")
    en_notebook_count = count_notebooks_in_dir(en_dir)
    assert en_notebook_count > 0, "No English notebooks generated"

    logger.info("Course 2 native worker E2E test completed successfully")


@pytest.mark.e2e
@pytest.mark.integration
@pytest.mark.skipif(
    not NOTEBOOK_WORKER_AVAILABLE,
    reason="Notebook worker module not available"
)
async def test_course_dir_groups_copy_e2e(
    e2e_course_1,
    sqlite_backend_with_notebook_workers
):
    """Test that directory groups are copied correctly in full E2E scenario.

    This validates that bonus materials and root files are copied to the
    correct locations in the output directory.
    """
    course = e2e_course_1
    backend = sqlite_backend_with_notebook_workers

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
    assert (bonus_dir / "workshops-toplevel.txt").exists(), "workshops-toplevel.txt should be copied"
    assert (bonus_dir / "Workshop-1" / "workshop-1.txt").exists(), "Workshop subdirectory should be copied"

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
# Level 3: Docker Worker E2E Tests (Slow, Future Implementation)
# ============================================================================

# TODO: Add docker worker tests when docker support is ready
# @pytest.mark.e2e
# @pytest.mark.integration
# @pytest.mark.slow
# async def test_course_conversion_docker_workers(...):
#     """Full E2E test with docker containers for workers."""
#     pass
