import io
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ETree

import pytest

from clx.course_spec import TopicSpec
from clx.utils.text_utils import Text
from clx_common.backends.local_ops_backend import LocalOpsBackend
from clx_common.messaging.base_classes import Payload
from clx_common.operation import Operation

if TYPE_CHECKING:
    from clx.course import Course
    from clx.section import Section
    from clx.topic import Topic

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


@pytest.fixture
def course_1_xml():
    return ETree.fromstring(COURSE_1_XML)


@pytest.fixture
def course_2_xml():
    return ETree.fromstring(COURSE_2_XML)


@pytest.fixture
def course_1_spec():
    from clx.course_spec import CourseSpec

    xml_stream = io.StringIO(COURSE_1_XML)

    return CourseSpec.from_file(xml_stream)


@pytest.fixture
def course_2_spec():
    from clx.course_spec import CourseSpec

    xml_stream = io.StringIO(COURSE_2_XML)

    return CourseSpec.from_file(xml_stream)


@pytest.fixture
def course_1(course_1_spec, tmp_path):
    from clx.course import Course

    course = Course.from_spec(course_1_spec, DATA_DIR, tmp_path)
    return course


@pytest.fixture
def course_2(course_2_spec, tmp_path):
    from clx.course import Course

    course = Course.from_spec(course_2_spec, DATA_DIR, tmp_path)
    return course


@pytest.fixture
def section_1(course_1):
    from clx.course import Section

    return Section(name=Text(en="Week 1", de="Woche 1"), course=course_1)


@pytest.fixture
def topic_1(section_1):
    from clx.course import Topic

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

@pytest.fixture(scope="session", autouse=True)
def configure_e2e_logging():
    """Configure logging for E2E tests.

    This fixture is automatically used for all e2e tests and configures
    logging based on environment variables:
    - CLX_E2E_LOG_LEVEL: Log level (DEBUG, INFO, WARNING, ERROR)
    - CLX_E2E_SHOW_WORKER_DETAILS: Show per-worker activity (default: true)
    - CLX_E2E_PROGRESS_INTERVAL: Seconds between progress updates (default: 5)
    - CLX_E2E_LONG_JOB_THRESHOLD: Seconds before warning about long jobs (default: 30)
    """
    # Get log level from environment, default to INFO
    log_level_name = os.environ.get("CLX_E2E_LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Create console handler if not already present
    if not root_logger.handlers:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)

        # Create formatter with timestamp and module info
        formatter = logging.Formatter(
            fmt='[%(asctime)s] %(levelname)-8s %(name)s - %(message)s',
            datefmt='%H:%M:%S'
        )
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    # Set specific logger levels for key components
    logging.getLogger('clx_common.database.job_queue').setLevel(log_level)
    logging.getLogger('clx_common.workers').setLevel(log_level)
    logging.getLogger('clx_faststream_backend').setLevel(log_level)
    logging.getLogger('clx.course').setLevel(log_level)

    # Log configuration
    logging.info(
        f"E2E test logging configured: level={log_level_name}, "
        f"progress_interval={os.environ.get('CLX_E2E_PROGRESS_INTERVAL', '5.0')}s, "
        f"long_job_threshold={os.environ.get('CLX_E2E_LONG_JOB_THRESHOLD', '30.0')}s"
    )

    yield

    # Cleanup: No need to restore since this is session-scoped


@pytest.fixture
def e2e_test_data_copy(tmp_path):
    """Copy test-data to temp directory for E2E testing.

    Returns:
        tuple: (data_dir, output_dir) where data_dir is the copied test-data
               and output_dir is a separate directory for output files.
    """
    data_dir = tmp_path / "test-data"
    output_dir = tmp_path / "output"

    # Copy test-data directory to temp location
    shutil.copytree(DATA_DIR, data_dir)

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    return data_dir, output_dir


@pytest.fixture
def e2e_course_1(course_1_spec, e2e_test_data_copy):
    """Course 1 instance for E2E testing with temp directories."""
    from clx.course import Course

    data_dir, output_dir = e2e_test_data_copy
    course = Course.from_spec(course_1_spec, data_dir, output_dir)
    return course


@pytest.fixture
def e2e_course_2(course_2_spec, e2e_test_data_copy):
    """Course 2 instance for E2E testing with temp directories."""
    from clx.course import Course

    data_dir, output_dir = e2e_test_data_copy
    course = Course.from_spec(course_2_spec, data_dir, output_dir)
    return course
