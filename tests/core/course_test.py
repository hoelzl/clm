import logging
from asyncio import TaskGroup
from pathlib import Path

from clx.core.course import Course
from clx.core.course_files.notebook_file import NotebookFile
from clx.core.utils.execution_utils import (
    COPY_GENERATED_IMAGES_STAGE,
    FIRST_EXECUTION_STAGE,
    HTML_COMPLETED_STAGE,
    HTML_SPEAKER_STAGE,
    get_stage_name,
)
from clx.core.utils.text_utils import Text
from clx.infrastructure.backends.local_ops_backend import LocalOpsBackend
from clx.infrastructure.messaging.base_classes import Payload
from clx.infrastructure.operation import Operation

# DATA_DIR is defined in tests/conftest.py and available as a fixture
# For direct use, we compute it here
DATA_DIR = Path(__file__).parent.parent / "test-data"


# PytestLocalOpsBackend is defined here (copied from conftest.py)
class PytestLocalOpsBackend(LocalOpsBackend):
    async def execute_operation(self, operation: Operation, payload: Payload) -> None:
        pass

    async def wait_for_completion(self) -> bool:
        return True


def test_build_topic_map(course_1_spec, tmp_path):
    course = Course(course_1_spec, DATA_DIR, tmp_path)

    course._build_topic_map()
    assert len(course._topic_path_map) == 7

    id1 = course._topic_path_map["some_topic_from_test_1"]
    assert id1.parent.name == "module_000_test_1"
    assert id1.name == "topic_100_some_topic_from_test_1"

    id2 = course._topic_path_map["another_topic_from_test_1"]
    assert id2.parent.name == "module_000_test_1"
    assert id2.name == "topic_110_another_topic_from_test_1.py"

    id3 = course._topic_path_map["a_topic_from_test_2"]
    assert id3.parent.name == "module_010_test_2"
    assert id3.name == "topic_100_a_topic_from_test_2"


def test_course_from_spec_sections(course_1_spec, tmp_path):
    course = Course.from_spec(course_1_spec, DATA_DIR, tmp_path)
    assert len(course.sections) == 2

    section_1 = course.sections[0]
    assert len(section_1.topics) == 2
    assert section_1.name == Text(de="Woche 1", en="Week 1")

    topic_11 = section_1.topics[0]
    assert topic_11.id == "some_topic_from_test_1"
    assert topic_11.section == section_1
    assert topic_11.path.name == "topic_100_some_topic_from_test_1"

    nb1 = topic_11.notebooks[0]
    assert nb1.path.name == "slides_some_topic_from_test_1.py"
    assert isinstance(nb1, NotebookFile)
    assert nb1.title == Text(de="Folien von Test 1", en="Some Topic from Test 1")
    assert nb1.number_in_section == 1

    topic_12 = section_1.topics[1]
    assert topic_12.id == "a_topic_from_test_2"
    assert topic_12.section == section_1
    assert topic_12.path.name == "topic_100_a_topic_from_test_2"

    nb2 = topic_12.notebooks[0]
    assert nb2.path.name == "slides_a_topic_from_test_2.py"
    assert isinstance(nb2, NotebookFile)
    assert nb2.title == Text(de="Folien aus Test 2", en="A Topic from Test 2")
    assert nb2.number_in_section == 2

    section_2 = course.sections[1]
    assert len(section_2.topics) == 1

    topic_21 = section_2.topics[0]
    assert topic_21.id == "another_topic_from_test_1"
    assert topic_21.section == section_2
    assert topic_21.path.name == "topic_110_another_topic_from_test_1.py"

    nb3 = topic_21.notebooks[0]
    assert nb3.path.name == "topic_110_another_topic_from_test_1.py"
    assert isinstance(nb3, NotebookFile)
    assert nb3.title == Text(de="Mehr Folien von Test 1", en="Another Topic from Test 1")
    assert nb3.number_in_section == 1


def test_course_dir_groups(course_1_spec, tmp_path):
    def src_path(dir_: str):
        return DATA_DIR / dir_

    def out_path(dir_: str):
        return tmp_path / dir_

    course = Course.from_spec(course_1_spec, DATA_DIR, tmp_path)

    assert len(course.dir_groups) == 3

    group1 = course.dir_groups[0]
    assert group1.name == Text(de="Code/Solutions", en="Code/Solutions")
    assert group1.source_dirs == (
        src_path("code/solutions/Example_1"),
        src_path("code/solutions/Example_3"),
    )
    assert group1.output_dirs(True, "de") == (
        out_path("speaker/De/Mein Kurs/Code/Solutions/Example_1"),
        out_path("speaker/De/Mein Kurs/Code/Solutions/Example_3"),
    )
    assert group1.output_dirs(False, "en") == (
        out_path("public/En/My Course/Code/Solutions/Example_1"),
        out_path("public/En/My Course/Code/Solutions/Example_3"),
    )

    group2 = course.dir_groups[1]
    assert group2.name == Text(de="Bonus", en="Bonus")
    assert group2.source_dirs == (src_path("div/workshops"),)
    assert group2.output_dirs(False, "de") == (out_path("public/De/Mein Kurs/Bonus"),)
    assert group2.output_dirs(True, "en") == (out_path("speaker/En/My Course/Bonus"),)

    group3 = course.dir_groups[2]
    assert group3.name == Text(de="", en="")
    assert group3.source_dirs == (src_path("root-files"),)
    assert group3.output_dirs(True, "de") == (out_path("speaker/De/Mein Kurs"),)
    assert group3.output_dirs(False, "en") == (out_path("public/En/My Course"),)


def test_course_files(course_1_spec, tmp_path):
    course = Course.from_spec(course_1_spec, DATA_DIR, tmp_path)

    assert len(course.files) == 9
    assert {file.path.name for file in course.files} == {
        "my_diag.png",
        "my_diag.pu",
        "my_drawing.drawio",
        "my_drawing.png",
        "my_image.png",
        "slides_a_topic_from_test_2.py",
        "slides_some_topic_from_test_1.py",
        "test.data",
        "topic_110_another_topic_from_test_1.py",
    }


def test_course_notebooks(course_1_spec, tmp_path):
    course = Course.from_spec(course_1_spec, DATA_DIR, tmp_path)

    assert len(course.notebooks) == 3

    nb1 = course.notebooks[0]
    assert nb1.path.name == "slides_some_topic_from_test_1.py"
    assert nb1.title == Text(de="Folien von Test 1", en="Some Topic from Test 1")
    assert nb1.number_in_section == 1

    nb2 = course.notebooks[1]
    assert nb2.path.name == "slides_a_topic_from_test_2.py"
    assert nb2.title == Text(de="Folien aus Test 2", en="A Topic from Test 2")
    assert nb2.number_in_section == 2

    nb3 = course.notebooks[2]
    assert nb3.path.name == "topic_110_another_topic_from_test_1.py"
    assert nb3.title == Text(de="Mehr Folien von Test 1", en="Another Topic from Test 1")
    assert nb3.number_in_section == 1


def test_find_file_does_not_find_non_existing_files(course_1_spec, tmp_path):
    unit = Course.from_spec(course_1_spec, DATA_DIR, tmp_path)
    assert len(unit.files) == 9
    topic_1 = unit.topics[0]
    topic_2 = unit.topics[1]

    # Note that we cannot easily add Notebooks, since notebooks need to actually
    # exist on disk to be added to the course, since we need information from the
    # notebook to fill out its properties.
    file_1 = topic_1.path / "python_file.py"
    assert unit.find_file(file_1) is None

    file_2 = topic_2.path / "img/my_new_image.png"
    assert unit.find_file(file_2) is None

    file_3 = topic_2.path.parent / "test-data/my_new_data.csv"
    assert unit.find_file(file_3) is None

    file_4 = topic_1.path / "slides_a_notebook.py"
    assert unit.find_file(file_4) is None


def test_add_file_to_course_adds_valid_files(course_1_spec, tmp_path):
    unit = Course.from_spec(course_1_spec, DATA_DIR, tmp_path)
    assert len(unit.files) == 9
    topic_1 = unit.topics[0]
    topic_2 = unit.topics[1]
    file_1 = topic_1.path / "python_file.py"
    file_2 = topic_2.path / "img/my_new_image.png"

    unit.add_file(file_1)
    assert len(unit.files) == 10
    assert unit.find_file(file_1).path == file_1

    unit.add_file(file_2)
    assert len(unit.files) == 11
    assert unit.find_file(file_2).path == file_2


def test_add_file_to_course_does_not_add_invalid_files(course_1_spec, tmp_path, caplog):
    unit = Course.from_spec(course_1_spec, DATA_DIR, tmp_path)
    assert len(unit.files) == 9
    topic_1 = unit.topics[0]
    topic_2 = unit.topics[1]

    file_3 = topic_2.path.parent / "test-data/my_new_data.csv"
    file_4 = topic_1.path / "slides_a_notebook.py"

    with caplog.at_level(logging.CRITICAL):
        unit.add_file(file_3)
    assert len(unit.files) == 9
    assert unit.find_file(file_3) is None

    with caplog.at_level(logging.CRITICAL):
        unit.add_file(file_4)
    assert len(unit.files) == 9
    assert unit.find_file(file_4) is None


async def test_course_dir_groups_copy(course_1_spec, tmp_path):
    course = Course.from_spec(course_1_spec, DATA_DIR, tmp_path)
    async with PytestLocalOpsBackend() as backend:
        async with TaskGroup() as tg:
            for dir_group in course.dir_groups:
                op = await dir_group.get_processing_operation()
                tg.create_task(op.execute(backend))

    assert set(tmp_path.glob("**/*")) == {
        tmp_path / "public",
        tmp_path / "public/De",
        tmp_path / "public/De/Mein Kurs",
        tmp_path / "public/De/Mein Kurs/Bonus",
        tmp_path / "public/De/Mein Kurs/Bonus/Workshop-1",
        tmp_path / "public/De/Mein Kurs/Bonus/Workshop-1/workshop-1.txt",
        tmp_path / "public/De/Mein Kurs/Bonus/workshops-toplevel.txt",
        tmp_path / "public/De/Mein Kurs/Code",
        tmp_path / "public/De/Mein Kurs/Code/Solutions",
        tmp_path / "public/De/Mein Kurs/Code/Solutions/Example_1",
        tmp_path / "public/De/Mein Kurs/Code/Solutions/Example_1/example-1.txt",
        tmp_path / "public/De/Mein Kurs/Code/Solutions/Example_3",
        tmp_path / "public/De/Mein Kurs/Code/Solutions/Example_3/example-3.txt",
        tmp_path / "public/De/Mein Kurs/root-file-1.txt",
        tmp_path / "public/De/Mein Kurs/root-file-2",
        tmp_path / "public/En",
        tmp_path / "public/En/My Course",
        tmp_path / "public/En/My Course/Bonus",
        tmp_path / "public/En/My Course/Bonus/Workshop-1",
        tmp_path / "public/En/My Course/Bonus/Workshop-1/workshop-1.txt",
        tmp_path / "public/En/My Course/Bonus/workshops-toplevel.txt",
        tmp_path / "public/En/My Course/Code",
        tmp_path / "public/En/My Course/Code/Solutions",
        tmp_path / "public/En/My Course/Code/Solutions/Example_1",
        tmp_path / "public/En/My Course/Code/Solutions/Example_1/example-1.txt",
        tmp_path / "public/En/My Course/Code/Solutions/Example_3",
        tmp_path / "public/En/My Course/Code/Solutions/Example_3/example-3.txt",
        tmp_path / "public/En/My Course/root-file-1.txt",
        tmp_path / "public/En/My Course/root-file-2",
    }


async def test_count_stage_operations_returns_correct_counts(course_1_spec, tmp_path):
    """Test that count_stage_operations counts operations, not just files.

    Notebooks produce multiple operations per file (one for each language/format/kind
    combination), so the operation count should be higher than the file count.
    """
    course = Course.from_spec(course_1_spec, DATA_DIR, tmp_path)

    # The test course has 3 notebooks, which should produce operations in
    # stages 1 (non-HTML) and 3/4 (HTML speaker/completed)
    stage_1_count = await course.count_stage_operations(FIRST_EXECUTION_STAGE)
    stage_3_count = await course.count_stage_operations(HTML_SPEAKER_STAGE)
    stage_4_count = await course.count_stage_operations(HTML_COMPLETED_STAGE)

    # Stage 1 should have operations: notebooks produce notebook/code ops,
    # plus other files like PlantUML, DrawIO, etc.
    assert stage_1_count > 0, "Stage 1 should have operations for notebooks and other files"

    # Stage 3 (HTML speaker) should have operations if course produces HTML
    # Stage 4 (HTML completed) should have operations if course produces HTML
    # The exact counts depend on the course configuration (languages, kinds, etc.)
    # but there should be a reasonable number of operations

    # Each notebook produces multiple operations (2 languages x 3 formats x 2 kinds = 12)
    # but filtered by stage, so each stage should have a subset
    total_operations = stage_1_count + stage_3_count + stage_4_count
    num_notebooks = len(course.notebooks)

    # With 3 notebooks and multiple output specs, we expect more operations than notebooks
    assert total_operations > num_notebooks, (
        f"Expected more operations ({total_operations}) than notebooks ({num_notebooks}) "
        f"since each notebook produces multiple outputs"
    )


async def test_count_stage_operations_matches_worker_jobs(course_1_spec, tmp_path):
    """Test that count_stage_operations matches actual worker job submissions.

    count_stage_operations only counts operations that have a service_name
    (i.e., operations that submit jobs to workers like notebook processing),
    not local operations like file copies.
    """

    class WorkerJobCountingBackend(LocalOpsBackend):
        def __init__(self):
            self.worker_job_count = 0

        async def execute_operation(self, operation: Operation, payload: Payload) -> None:
            # This is called for operations that submit to workers
            self.worker_job_count += 1

        async def wait_for_completion(self) -> bool:
            return True

    course = Course.from_spec(course_1_spec, DATA_DIR, tmp_path)

    for stage in [FIRST_EXECUTION_STAGE, HTML_SPEAKER_STAGE, HTML_COMPLETED_STAGE]:
        # Count expected worker jobs
        expected_count = await course.count_stage_operations(stage)

        # Actually process the stage and count worker job submissions
        backend = WorkerJobCountingBackend()
        async with backend:
            await course.process_stage(stage, backend)

        # The counts should match
        assert backend.worker_job_count == expected_count, (
            f"Stage {stage}: count_stage_operations returned {expected_count}, "
            f"but process_stage submitted {backend.worker_job_count} worker jobs"
        )


def test_get_stage_name_returns_correct_names():
    """Test that get_stage_name returns proper names for all stages."""
    assert get_stage_name(FIRST_EXECUTION_STAGE) == "Processing"
    assert get_stage_name(COPY_GENERATED_IMAGES_STAGE) == "Images"
    assert get_stage_name(HTML_SPEAKER_STAGE) == "HTML Speaker"
    assert get_stage_name(HTML_COMPLETED_STAGE) == "HTML Completed"


def test_get_stage_name_returns_fallback_for_unknown_stage():
    """Test that get_stage_name returns a fallback for unknown stages."""
    assert get_stage_name(99) == "Stage 99"
    assert get_stage_name(0) == "Stage 0"
