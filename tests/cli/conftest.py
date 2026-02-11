"""
Shared fixtures and configuration for CLI tests.
"""

import logging
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def test_data_dir():
    """Fixture providing path to test data directory"""
    data_dir = Path("test-data")
    if not data_dir.exists():
        pytest.skip("Test data directory not available")
    return data_dir


@pytest.fixture(scope="session")
def test_spec_files(test_data_dir):
    """Fixture providing paths to test specification files"""
    spec_dir = test_data_dir / "course-specs"
    if not spec_dir.exists():
        pytest.skip("Test spec directory not available")

    specs = {
        "simple": spec_dir / "test-spec-2.xml",
        "complex": spec_dir / "test-spec-1.xml",
        "single_notebook": spec_dir / "test-spec-3.xml",
        "single_plantuml": spec_dir / "test-spec-4.xml",
        "single_drawio": spec_dir / "test-spec-5.xml",
    }

    # Verify files exist
    for name, path in specs.items():
        if not path.exists():
            pytest.skip(f"Test spec '{name}' not available at {path}")

    return specs


@pytest.fixture
def temp_workspace(tmp_path):
    """Fixture providing a temporary workspace with standard subdirectories"""
    workspace = {
        "root": tmp_path,
        "data": tmp_path / "data",
        "output": tmp_path / "output",
        "db": tmp_path / "test.db",
    }

    # Create data directory
    workspace["data"].mkdir(exist_ok=True)

    return workspace


@pytest.fixture
def cli_test_db_path(tmp_path):
    """Fixture providing a temporary database path for CLI tests"""
    return tmp_path / "cli_test.db"


@pytest.fixture(autouse=True)
def configure_test_logging():
    """Configure logging for tests to reduce noise"""
    # Reduce logging noise during tests
    logging.getLogger("clm").setLevel(logging.WARNING)
    logging.getLogger("clm_cli").setLevel(logging.WARNING)
    logging.getLogger("watchdog").setLevel(logging.ERROR)
    yield
    # Reset after test
    logging.getLogger("clm").setLevel(logging.INFO)
    logging.getLogger("clm_cli").setLevel(logging.INFO)


@pytest.fixture
def sample_course_spec_xml():
    """Fixture providing a minimal valid course specification XML"""
    return """<?xml version="1.0" encoding="UTF-8"?>
<course xmlns="https://github.com/hoelzl/clm">
    <github>
        <de>https://github.com/test/test-de</de>
        <en>https://github.com/test/test-en</en>
    </github>
    <name>
        <de>Test Kurs</de>
        <en>Test Course</en>
    </name>
    <prog-lang>python</prog-lang>
    <description>
        <de>Ein Test Kurs</de>
        <en>A test course</en>
    </description>
    <certificate>
        <de>Test Zertifikat</de>
        <en>Test Certificate</en>
    </certificate>
    <sections>
        <section>
            <name>
                <de>Woche 1</de>
                <en>Week 1</en>
            </name>
            <topics>
                <topic>test_topic</topic>
            </topics>
        </section>
    </sections>
</course>
"""


@pytest.fixture
def create_test_spec_file(tmp_path, sample_course_spec_xml):
    """Factory fixture for creating test spec files"""

    def _create_spec(name="test-spec.xml", content=None):
        """Create a test specification file

        Args:
            name: Filename for the spec
            content: XML content (uses default if None)

        Returns:
            Path to created spec file
        """
        spec_path = tmp_path / name
        spec_path.write_text(content or sample_course_spec_xml)
        return spec_path

    return _create_spec


# Markers for test organization
def pytest_configure(config):
    """Configure custom pytest markers"""
    config.addinivalue_line(
        "markers", "integration: mark test as integration test requiring backend"
    )
    config.addinivalue_line("markers", "slow: mark test as slow-running E2E test")
    config.addinivalue_line("markers", "e2e: mark test as end-to-end subprocess test")


# Hook to add test information
def pytest_collection_modifyitems(config, items):
    """Modify test items to add markers based on test location/name"""
    for item in items:
        # Add integration marker based on test file name
        if "test_cli_integration" in str(item.fspath):
            if "integration" not in [marker.name for marker in item.iter_markers()]:
                item.add_marker(pytest.mark.integration)

        # Add slow/e2e markers for subprocess tests
        if "test_cli_subprocess" in str(item.fspath):
            if "slow" not in [marker.name for marker in item.iter_markers()]:
                item.add_marker(pytest.mark.slow)
            if "e2e" not in [marker.name for marker in item.iter_markers()]:
                item.add_marker(pytest.mark.e2e)
