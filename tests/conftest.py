"""Pytest configuration and fixtures.

Logging Configuration:
---------------------
Tests with 'e2e' or 'integration' markers automatically get live logging enabled.

To enable logging for any test:
1. Use the marker: @pytest.mark.e2e or @pytest.mark.integration
2. Explicitly use the fixture: def test_something(configure_test_logging): ...
3. Set environment variable: CLX_ENABLE_TEST_LOGGING=1
4. Use pytest option: pytest --log-cli

Environment variables:
- CLX_LOG_LEVEL: Set log level (DEBUG, INFO, WARNING, ERROR) - default: INFO
- CLX_ENABLE_TEST_LOGGING: Enable logging for all tests (set to any value)
- CLX_E2E_PROGRESS_INTERVAL: Seconds between progress updates (default: 5)
- CLX_E2E_LONG_JOB_THRESHOLD: Seconds before warning about long jobs (default: 30)
"""

import io
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ETree

import pytest

from clx.core.course_spec import TopicSpec
from clx.core.utils.text_utils import Text
from clx.infrastructure.backends.local_ops_backend import LocalOpsBackend
from clx.infrastructure.messaging.base_classes import Payload
from clx.infrastructure.operation import Operation

if TYPE_CHECKING:
    from clx.core.course import Course
    from clx.core.section import Section
    from clx.core.topic import Topic


# ====================================================================
# Tool Availability Detection
# ====================================================================


def _is_plantuml_available() -> bool:
    """Check if PlantUML is available and functional."""
    plantuml_jar = os.environ.get("PLANTUML_JAR")
    if not plantuml_jar or not Path(plantuml_jar).exists():
        return False

    # Check if file is a Git LFS pointer (not the actual JAR)
    try:
        with open(plantuml_jar, "rb") as f:
            header = f.read(100)
            if b"git-lfs.github.com" in header:
                return False
    except Exception:
        return False

    # Check if Java is available (1 second timeout for faster startup)
    try:
        result = subprocess.run(["java", "-version"], capture_output=True, timeout=1)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _is_drawio_available() -> bool:
    """Check if DrawIO is available and can render.

    DrawIO requires:
    1. DrawIO executable to be available
    2. On Unix/Linux: DISPLAY environment variable (real display or Xvfb)
    3. On Windows: No DISPLAY needed (native GUI)

    This works correctly in:
    - Windows desktop: DrawIO.exe available (no DISPLAY needed)
    - Linux/Mac desktop: DrawIO available + DISPLAY=:0 (real display)
    - Linux/Mac headless: DrawIO available + DISPLAY=:99 (Xvfb)
    """
    # Check if DrawIO executable exists
    drawio_exec = os.environ.get("DRAWIO_EXECUTABLE")

    # Try to find drawio in PATH if not set
    if not drawio_exec:
        drawio_exec = shutil.which("drawio")

    if not drawio_exec or not Path(drawio_exec).exists():
        return False

    # Check if file is a Git LFS pointer
    try:
        with open(drawio_exec, "rb") as f:
            header = f.read(100)
            if b"git-lfs.github.com" in header:
                return False
    except Exception:
        pass

    # On Unix/Linux, DISPLAY is required (X11)
    # On Windows, DISPLAY is not needed (native GUI)
    if sys.platform != "win32":
        if not os.environ.get("DISPLAY"):
            return False

    return True


def _is_xvfb_running() -> bool:
    """Check if Xvfb is running.

    Note: This is for informational/diagnostic purposes only.
    Tests should use requires_drawio marker, which checks for DISPLAY
    (works with both real displays and Xvfb).
    """
    if not os.environ.get("DISPLAY"):
        return False

    try:
        result = subprocess.run(["pgrep", "-x", "Xvfb"], capture_output=True, timeout=1)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


# Store tool availability at module level (cached for performance)
_PLANTUML_AVAILABLE = None
_DRAWIO_AVAILABLE = None
_XVFB_RUNNING = None


def get_tool_availability():
    """Get cached tool availability status."""
    global _PLANTUML_AVAILABLE, _DRAWIO_AVAILABLE, _XVFB_RUNNING

    if _PLANTUML_AVAILABLE is None:
        _PLANTUML_AVAILABLE = _is_plantuml_available()
    if _DRAWIO_AVAILABLE is None:
        _DRAWIO_AVAILABLE = _is_drawio_available()
    if _XVFB_RUNNING is None:
        _XVFB_RUNNING = _is_xvfb_running()

    return {
        "plantuml": _PLANTUML_AVAILABLE,
        "drawio": _DRAWIO_AVAILABLE,
        "xvfb": _XVFB_RUNNING,  # For diagnostic purposes only
    }


COURSE_1_XML = """
<course>
    <github>
        <de>https://github.com/hoelzl/my-course-de</de>
        <en>https://github.com/hoelzl/my-course-en</en>
    </github>
    <name>
        <de>Mein Kurs</de>
        <en>My Course</en>
    </name>
    <prog-lang>python</prog-lang>
    <description>
        <de>Ein Kurs über ein Thema</de>
        <en>A course about a topic</en>
    </description>
    <certificate>
        <de>...</de>
        <en>...</en>
    </certificate>
    <sections>
        <section>
            <name>
                <de>Woche 1</de>
                <en>Week 1</en>
            </name>
            <topics>
                <topic>
                    some_topic_from_test_1
                    <dir-group>
                        <name>Code/Solutions</name>
                        <path>code/solutions</path>
                        <subdirs>
                            <subdir>Example_1</subdir>
                            <subdir>Example_3</subdir>
                        </subdirs>
                    </dir-group>
                </topic>
                <topic>a_topic_from_test_2</topic>
            </topics>
        </section>
        <section>
            <name>
                <de>Woche 2</de>
                <en>Week 2</en>
            </name>
            <topics>
                <topic>another_topic_from_test_1</topic>
            </topics>
        </section>
    </sections>
    <dir-groups>
        <dir-group>
            <name>Bonus</name>
            <path>div/workshops</path>
        </dir-group>
        <!-- We can have an empty name to copy files into the course root -->
        <dir-group>
            <name/>
            <path>root-files</path>
        </dir-group>
    </dir-groups>
</course>
"""

COURSE_2_XML = """
<course>
    <github>
        <de>https://github.com/hoelzl/my-course-de</de>
        <en>https://github.com/hoelzl/my-course-en</en>
    </github>
    <name>
        <de>Kurs 2</de>
        <en>Kurs 2</en>
    </name>
    <prog-lang>python</prog-lang>
    <description>
        <de>Der zweite Kurs</de>
        <en>The second course</en>
    </description>
    <certificate>
        <de>...</de>
        <en>...</en>
    </certificate>
    <sections>
        <section>
            <name>
                <de>Woche 1</de>
                <en>Week 1</en>
            </name>
            <topics>
                <topic>slides_in_test_3</topic>
            </topics>
        </section>
    </sections>
</course>
"""


DATA_DIR = Path(__file__).parent / "test-data"


# Configure external tool paths at module load time
# This ensures they're available before test collection
def _setup_external_tools():
    """Set up environment variables for external tools if not already set."""
    # PlantUML JAR path
    if "PLANTUML_JAR" not in os.environ:
        repo_root = Path(__file__).parent
        plantuml_jar = repo_root / "services" / "plantuml-converter" / "plantuml-1.2024.6.jar"
        if plantuml_jar.exists():
            os.environ["PLANTUML_JAR"] = str(plantuml_jar)
            logging.info(f"PLANTUML_JAR set to: {plantuml_jar}")

    # Draw.io executable path
    if "DRAWIO_EXECUTABLE" not in os.environ:
        common_drawio_paths = [
            r"C:\Program Files\draw.io\draw.io.exe",
            r"C:\Program Files (x86)\draw.io\draw.io.exe",
        ]
        for drawio_path in common_drawio_paths:
            if Path(drawio_path).exists():
                os.environ["DRAWIO_EXECUTABLE"] = drawio_path
                logging.info(f"DRAWIO_EXECUTABLE set to: {drawio_path}")
                break


# Run setup at module import time
_setup_external_tools()


@pytest.fixture
def course_1_xml():
    return ETree.fromstring(COURSE_1_XML)


@pytest.fixture
def course_2_xml():
    return ETree.fromstring(COURSE_2_XML)


@pytest.fixture(scope="session")
def course_1_spec():
    """Session-scoped CourseSpec for COURSE_1_XML.

    Session-scoped because CourseSpec is immutable and parsing is deterministic.
    This avoids re-parsing the same XML for every test.
    """
    from clx.core.course_spec import CourseSpec

    xml_stream = io.StringIO(COURSE_1_XML)

    return CourseSpec.from_file(xml_stream)


@pytest.fixture(scope="session")
def course_2_spec():
    """Session-scoped CourseSpec for COURSE_2_XML.

    Session-scoped because CourseSpec is immutable and parsing is deterministic.
    """
    from clx.core.course_spec import CourseSpec

    xml_stream = io.StringIO(COURSE_2_XML)

    return CourseSpec.from_file(xml_stream)


@pytest.fixture
def course_1(course_1_spec, tmp_path):
    from clx.core.course import Course

    course = Course.from_spec(course_1_spec, DATA_DIR, tmp_path)
    return course


@pytest.fixture
def course_2(course_2_spec, tmp_path):
    from clx.core.course import Course

    course = Course.from_spec(course_2_spec, DATA_DIR, tmp_path)
    return course


@pytest.fixture
def section_1(course_1):
    from clx.core.course import Section

    return Section(name=Text(en="Week 1", de="Woche 1"), course=course_1)


@pytest.fixture
def topic_1(section_1):
    from clx.core.course import Topic

    path = DATA_DIR / "slides/module_000_test_1/topic_100_some_topic_from_test_1"
    spec = TopicSpec(id="some_topic")
    return Topic.from_spec(spec, section=section_1, path=path)


class PytestLocalOpsBackend(LocalOpsBackend):
    async def execute_operation(self, operation: "Operation", payload: Payload) -> None:
        pass

    async def wait_for_completion(self) -> bool:
        return True

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


# E2E Test Fixtures


def pytest_configure(config):
    """Configure pytest and set default log levels.

    By default, suppress application logs during tests unless explicitly enabled.
    Note: External tool paths are configured at module import via _setup_external_tools()
    to avoid duplicate initialization.
    """
    # Register custom markers
    config.addinivalue_line(
        "markers", "requires_plantuml: mark test as requiring PlantUML JAR and Java"
    )
    config.addinivalue_line(
        "markers",
        "requires_drawio: mark test as requiring DrawIO executable "
        "(Unix/Linux: also needs DISPLAY; Windows: no DISPLAY needed)",
    )
    config.addinivalue_line(
        "markers",
        "requires_xvfb: [DEPRECATED] use requires_drawio instead - "
        "it works with both real displays and Xvfb",
    )

    # External tool paths are already configured by _setup_external_tools() at module import
    # This avoids duplicate initialization and speeds up startup

    # Enable live logging if explicitly requested
    if os.environ.get("CLX_ENABLE_TEST_LOGGING"):
        config.option.log_cli = True
        config.option.log_cli_level = os.environ.get("CLX_LOG_LEVEL", "INFO")
        config.option.log_cli_format = "[%(asctime)s] %(levelname)-8s %(name)s - %(message)s"
        config.option.log_cli_date_format = "%H:%M:%S"
    else:
        # Disable live logging by default
        config.option.log_cli = False

    # Set all application loggers to WARNING by default to suppress INFO logs during tests
    # This prevents log spam in test output
    loggers_to_quiet = [
        "clx",
        "clx_common",
        "clx_faststream_backend",
    ]

    for logger_name in loggers_to_quiet:
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def pytest_collection_modifyitems(config, items):
    """Auto-skip tests based on tool availability."""
    tool_status = get_tool_availability()

    # Report tool availability once at the start
    if items:  # Only report if there are tests to run
        print("\n" + "=" * 70)
        print("External Tool Availability:")
        print(f"  PlantUML: {'✓ Available' if tool_status['plantuml'] else '✗ Not available'}")
        print(f"  DrawIO:   {'✓ Available' if tool_status['drawio'] else '✗ Not available'}")

        # Show display status (platform-aware)
        if sys.platform == "win32":
            print("  Display:  ✓ Windows (native GUI, no DISPLAY needed)")
        else:
            # Unix/Linux - show DISPLAY status
            display = os.environ.get("DISPLAY", "not set")
            if tool_status["xvfb"]:
                print(f"  Display:  ✓ {display} (Xvfb)")
            elif display != "not set":
                print(f"  Display:  ✓ {display} (real display)")
            else:
                print("  Display:  ✗ not set (DrawIO needs DISPLAY on Unix/Linux)")
        print("=" * 70 + "\n")

    skip_plantuml = pytest.mark.skip(
        reason="PlantUML not available - set PLANTUML_JAR and ensure Java is installed"
    )

    # Platform-specific skip message for DrawIO
    if sys.platform == "win32":
        skip_drawio = pytest.mark.skip(reason="DrawIO not available - install DrawIO on Windows")
    else:
        skip_drawio = pytest.mark.skip(
            reason="DrawIO not available - install DrawIO and set DISPLAY environment variable (Unix/Linux)"
        )

    for item in items:
        # Check for requires_plantuml marker
        if "requires_plantuml" in [marker.name for marker in item.iter_markers()]:
            if not tool_status["plantuml"]:
                item.add_marker(skip_plantuml)

        # Check for requires_drawio marker (or deprecated requires_xvfb)
        markers = [marker.name for marker in item.iter_markers()]
        if "requires_drawio" in markers or "requires_xvfb" in markers:
            if not tool_status["drawio"]:
                item.add_marker(skip_drawio)


@pytest.fixture(scope="function")
def configure_test_logging(request):
    """Configure logging for individual tests.

    This fixture can be used explicitly in tests that need logging,
    and is automatically applied to tests with e2e or integration markers.

    Environment variables:
    - CLX_LOG_LEVEL: Log level (DEBUG, INFO, WARNING, ERROR) - default: INFO
    - CLX_E2E_PROGRESS_INTERVAL: Seconds between progress updates (default: 5)
    - CLX_E2E_LONG_JOB_THRESHOLD: Seconds before warning about long jobs (default: 30)
    """
    # Get log level from environment, default to INFO
    log_level_name = os.environ.get("CLX_LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    # Enable live logging for this test
    request.config.option.log_cli = True
    request.config.option.log_cli_level = log_level_name
    if not request.config.option.log_cli_format:
        request.config.option.log_cli_format = (
            "[%(asctime)s] %(levelname)-8s %(name)s - %(message)s"
        )
    if not request.config.option.log_cli_date_format:
        request.config.option.log_cli_date_format = "%H:%M:%S"

    # Store original log levels to restore after test
    original_levels = {}
    loggers_to_configure = [
        "clx",
        "clx_common",
        "clx_faststream_backend",
    ]

    for logger_name in loggers_to_configure:
        logger = logging.getLogger(logger_name)
        original_levels[logger_name] = logger.level
        logger.setLevel(log_level)

    # Log configuration for this test
    logging.info(f"Test logging configured for {request.node.name}: level={log_level_name}")

    yield

    # Restore original log levels
    for logger_name, original_level in original_levels.items():
        logging.getLogger(logger_name).setLevel(original_level)

    # Disable live logging after test
    if not os.environ.get("CLX_ENABLE_TEST_LOGGING"):
        request.config.option.log_cli = False


@pytest.fixture(scope="function", autouse=True)
def auto_configure_logging_for_marked_tests(request):
    """Automatically configure logging for tests with specific markers."""
    # Check if test has e2e or integration marker
    markers = [marker.name for marker in request.node.iter_markers()]

    if "e2e" in markers or "integration" in markers:
        # Invoke the configure_test_logging fixture
        request.getfixturevalue("configure_test_logging")


@pytest.fixture(scope="session")
def e2e_test_data_template(tmp_path_factory):
    """Session-scoped template of test data (copied once per test session).

    This reduces E2E test overhead by copying DATA_DIR only once per session
    instead of once per test. Individual tests then copy from this template
    using hardlinks when possible for near-instant copies.

    Returns:
        Path: Path to the template directory containing test-data
    """
    template_dir = tmp_path_factory.mktemp("test-data-template")
    shutil.copytree(DATA_DIR, template_dir / "test-data")
    return template_dir / "test-data"


@pytest.fixture
def e2e_test_data_copy(tmp_path, e2e_test_data_template):
    """Copy test-data to temp directory for E2E testing.

    Uses hardlinks from session-scoped template for fast per-test copies.
    Falls back to regular copy on platforms that don't support hardlinks.

    Returns:
        tuple: (data_dir, output_dir) where data_dir is the copied test-data
               and output_dir is a separate directory for output files.
    """
    data_dir = tmp_path / "test-data"
    output_dir = tmp_path / "output"

    # Try to use hardlinks for fast copy from template (O(n) files, not O(n) bytes)
    # Falls back to regular copy on Windows or cross-filesystem scenarios
    try:
        shutil.copytree(e2e_test_data_template, data_dir, copy_function=os.link)
    except OSError:
        # Fallback to regular copy if hardlinks not supported
        shutil.copytree(e2e_test_data_template, data_dir)

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    return data_dir, output_dir


@pytest.fixture
def e2e_course_1(course_1_spec, e2e_test_data_copy):
    """Course 1 instance for E2E testing with temp directories."""
    from clx.core.course import Course

    data_dir, output_dir = e2e_test_data_copy
    course = Course.from_spec(course_1_spec, data_dir, output_dir)
    return course


@pytest.fixture
def e2e_course_2(course_2_spec, e2e_test_data_copy):
    """Course 2 instance for E2E testing with temp directories."""
    from clx.core.course import Course

    data_dir, output_dir = e2e_test_data_copy
    course = Course.from_spec(course_2_spec, data_dir, output_dir)
    return course


# New course specs for testing edge cases

COURSE_3_XML = """
<course>
    <github>
        <de>https://github.com/hoelzl/simple-notebook-de</de>
        <en>https://github.com/hoelzl/simple-notebook-en</en>
    </github>
    <name>
        <de>Einfaches Notebook</de>
        <en>Simple Notebook</en>
    </name>
    <prog-lang>python</prog-lang>
    <description>
        <de>Ein Kurs mit nur einem einfachen Notebook</de>
        <en>A course with just a simple notebook</en>
    </description>
    <certificate>
        <de>Zertifikat für Einfaches Notebook</de>
        <en>Certificate for Simple Notebook</en>
    </certificate>
    <sections>
        <section>
            <name>
                <de>Einziger Abschnitt</de>
                <en>Single Section</en>
            </name>
            <topics>
                <topic>simple_notebook</topic>
            </topics>
        </section>
    </sections>
</course>
"""

COURSE_4_XML = """
<course>
    <github>
        <de>https://github.com/hoelzl/simple-plantuml-de</de>
        <en>https://github.com/hoelzl/simple-plantuml-en</en>
    </github>
    <name>
        <de>Einfaches PlantUML</de>
        <en>Simple PlantUML</en>
    </name>
    <prog-lang>python</prog-lang>
    <description>
        <de>Ein Kurs mit nur einer PlantUML-Datei</de>
        <en>A course with just a PlantUML file</en>
    </description>
    <certificate>
        <de>Zertifikat für Einfaches PlantUML</de>
        <en>Certificate for Simple PlantUML</en>
    </certificate>
    <sections>
        <section>
            <name>
                <de>Einziger Abschnitt</de>
                <en>Single Section</en>
            </name>
            <topics>
                <topic>simple_plantuml</topic>
            </topics>
        </section>
    </sections>
</course>
"""

COURSE_5_XML = """
<course>
    <github>
        <de>https://github.com/hoelzl/simple-drawio-de</de>
        <en>https://github.com/hoelzl/simple-drawio-en</en>
    </github>
    <name>
        <de>Einfaches Drawio</de>
        <en>Simple Drawio</en>
    </name>
    <prog-lang>python</prog-lang>
    <description>
        <de>Ein Kurs mit nur einer Draw.io-Datei</de>
        <en>A course with just a Draw.io file</en>
    </description>
    <certificate>
        <de>Zertifikat für Einfaches Drawio</de>
        <en>Certificate for Simple Drawio</en>
    </certificate>
    <sections>
        <section>
            <name>
                <de>Einziger Abschnitt</de>
                <en>Single Section</en>
            </name>
            <topics>
                <topic>simple_drawio</topic>
            </topics>
        </section>
    </sections>
</course>
"""


@pytest.fixture(scope="session")
def course_3_spec():
    """Session-scoped CourseSpec for COURSE_3_XML (simple notebook)."""
    from clx.core.course_spec import CourseSpec

    xml_stream = io.StringIO(COURSE_3_XML)
    return CourseSpec.from_file(xml_stream)


@pytest.fixture(scope="session")
def course_4_spec():
    """Session-scoped CourseSpec for COURSE_4_XML (simple plantuml)."""
    from clx.core.course_spec import CourseSpec

    xml_stream = io.StringIO(COURSE_4_XML)
    return CourseSpec.from_file(xml_stream)


@pytest.fixture(scope="session")
def course_5_spec():
    """Session-scoped CourseSpec for COURSE_5_XML (simple drawio)."""
    from clx.core.course_spec import CourseSpec

    xml_stream = io.StringIO(COURSE_5_XML)
    return CourseSpec.from_file(xml_stream)


@pytest.fixture
def e2e_course_3(course_3_spec, e2e_test_data_copy):
    """Course 3 instance for E2E testing with temp directories (single notebook only)."""
    from clx.core.course import Course

    data_dir, output_dir = e2e_test_data_copy
    course = Course.from_spec(course_3_spec, data_dir, output_dir)
    return course


@pytest.fixture
def e2e_course_4(course_4_spec, e2e_test_data_copy):
    """Course 4 instance for E2E testing with temp directories (single plantuml only)."""
    from clx.core.course import Course

    data_dir, output_dir = e2e_test_data_copy
    course = Course.from_spec(course_4_spec, data_dir, output_dir)
    return course


@pytest.fixture
def e2e_course_5(course_5_spec, e2e_test_data_copy):
    """Course 5 instance for E2E testing with temp directories (single draw.io only)."""
    from clx.core.course import Course

    data_dir, output_dir = e2e_test_data_copy
    course = Course.from_spec(course_5_spec, data_dir, output_dir)
    return course


# =============================================================================
# Mock Worker Fixtures
# =============================================================================


@pytest.fixture
def mock_db_path(tmp_path):
    """Create a temporary database for mock worker tests."""
    from clx.infrastructure.database.schema import init_database

    db_path = tmp_path / "mock_test.db"
    init_database(db_path)
    return db_path


@pytest.fixture
def mock_workspace_path(tmp_path):
    """Create a temporary workspace directory for mock worker tests."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


@pytest.fixture
def mock_worker_pool(mock_db_path):
    """Create a mock worker pool for testing.

    This fixture provides a MockWorkerPool instance that can be used to
    start mock workers for fast integration testing. The pool is automatically
    cleaned up after the test.

    Example:
        def test_worker_lifecycle(mock_worker_pool):
            workers = mock_worker_pool.start_workers("notebook", count=2)
            assert len(workers) == 2
            # Workers are automatically stopped after the test
    """
    from tests.fixtures.mock_workers import MockWorkerPool

    pool = MockWorkerPool(mock_db_path)
    yield pool
    pool.stop_all()


@pytest.fixture
def mock_notebook_workers(mock_worker_pool):
    """Start 2 mock notebook workers for testing.

    Returns a list of 2 MockWorker instances already started and ready
    to process jobs.
    """
    import time

    workers = mock_worker_pool.start_workers("notebook", count=2)
    time.sleep(0.1)  # Give workers time to register
    return workers
