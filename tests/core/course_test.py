import logging
from asyncio import TaskGroup
from pathlib import Path

import pytest

from clm.core.course import Course
from clm.core.course_files.notebook_file import NotebookFile
from clm.core.utils.execution_utils import (
    COPY_GENERATED_IMAGES_STAGE,
    FIRST_EXECUTION_STAGE,
    HTML_COMPLETED_STAGE,
    HTML_SPEAKER_STAGE,
    get_stage_name,
)
from clm.core.utils.text_utils import Text
from clm.infrastructure.backends.local_ops_backend import LocalOpsBackend
from clm.infrastructure.messaging.base_classes import Payload
from clm.infrastructure.operation import Operation

# DATA_DIR is defined in tests/conftest.py and available as a fixture
# For direct use, we compute it here
DATA_DIR = Path(__file__).parent.parent / "test-data"


# PytestLocalOpsBackend is defined here (copied from conftest.py)
class PytestLocalOpsBackend(LocalOpsBackend):
    async def execute_operation(self, operation: Operation, payload: Payload) -> None:
        pass

    async def wait_for_completion(self, all_submitted=None) -> bool:
        return True


def test_build_topic_map(course_1_spec, tmp_path):
    course = Course(course_1_spec, DATA_DIR, tmp_path)

    course._build_topic_map()
    assert len(course._topic_path_map) == 8

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
        out_path("speaker/Mein Kurs-de/Code/Solutions/Example_1"),
        out_path("speaker/Mein Kurs-de/Code/Solutions/Example_3"),
    )
    assert group1.output_dirs(False, "en") == (
        out_path("public/My Course-en/Code/Solutions/Example_1"),
        out_path("public/My Course-en/Code/Solutions/Example_3"),
    )

    group2 = course.dir_groups[1]
    assert group2.name == Text(de="Bonus", en="Bonus")
    assert group2.source_dirs == (src_path("div/workshops"),)
    assert group2.output_dirs(False, "de") == (out_path("public/Mein Kurs-de/Bonus"),)
    assert group2.output_dirs(True, "en") == (out_path("speaker/My Course-en/Bonus"),)

    group3 = course.dir_groups[2]
    assert group3.name == Text(de="", en="")
    assert group3.source_dirs == (src_path("root-files"),)
    assert group3.output_dirs(True, "de") == (out_path("speaker/Mein Kurs-de"),)
    assert group3.output_dirs(False, "en") == (out_path("public/My Course-en"),)


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
    """Test that dir groups are copied to both public and speaker directories."""
    course = Course.from_spec(course_1_spec, DATA_DIR, tmp_path)
    async with PytestLocalOpsBackend() as backend:
        async with TaskGroup() as tg:
            for dir_group in course.dir_groups:
                op = await dir_group.get_processing_operation()
                tg.create_task(op.execute(backend))

    # Build expected paths for both public and speaker directories
    expected = set()
    for toplevel in ["public", "speaker"]:
        expected.add(tmp_path / toplevel)
        for dir_name in ["Mein Kurs-de", "My Course-en"]:
            base = tmp_path / toplevel / dir_name
            expected.update(
                {
                    base,
                    base / "Bonus",
                    base / "Bonus/Workshop-1",
                    base / "Bonus/Workshop-1/workshop-1.txt",
                    base / "Bonus/workshops-toplevel.txt",
                    base / "Code",
                    base / "Code/Solutions",
                    base / "Code/Solutions/Example_1",
                    base / "Code/Solutions/Example_1/example-1.txt",
                    base / "Code/Solutions/Example_3",
                    base / "Code/Solutions/Example_3/example-3.txt",
                    base / "root-file-1.txt",
                    base / "root-file-2",
                }
            )

    assert set(tmp_path.glob("**/*")) == expected


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
            super().__init__()
            self.worker_job_count = 0

        async def execute_operation(self, operation: Operation, payload: Payload) -> None:
            # This is called for operations that submit to workers
            self.worker_job_count += 1

        async def wait_for_completion(self, all_submitted=None) -> bool:
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
    assert get_stage_name(HTML_COMPLETED_STAGE) == "HTML Completed/Partial"


def test_get_stage_name_returns_fallback_for_unknown_stage():
    """Test that get_stage_name returns a fallback for unknown stages."""
    assert get_stage_name(99) == "Stage 99"
    assert get_stage_name(0) == "Stage 0"


# ---------------------------------------------------------------------------
# Module-bound topic resolution
# ---------------------------------------------------------------------------


def _make_topic_dir(course_root: Path, module: str, topic: str) -> Path:
    topic_dir = course_root / "slides" / module / topic
    topic_dir.mkdir(parents=True, exist_ok=True)
    (topic_dir / "slides_intro.py").write_text("# %% [markdown]\n# Hello\n", encoding="utf-8")
    return topic_dir


def _build_course(course_root: Path, sections_xml: str):
    """Construct a Course from a tmp_path course root and an inline sections XML."""
    import io

    from clm.core.course import Course
    from clm.core.course_spec import CourseSpec

    spec_xml = f"""
    <course>
      <name><de>Test</de><en>Test</en></name>
      <prog-lang>python</prog-lang>
      <description><de></de><en></en></description>
      <certificate><de></de><en></en></certificate>
      <project-slug>test-course</project-slug>
      {sections_xml}
    </course>
    """
    spec = CourseSpec.from_file(io.StringIO(spec_xml))
    return Course.from_spec(spec, course_root, course_root / "_out")


def test_module_bound_section_resolves_to_named_module(tmp_path):
    """A section with module= picks the topic from that specific module
    even when another module has the same topic ID."""
    _make_topic_dir(tmp_path, "module_100_live", "topic_010_intro")
    _make_topic_dir(tmp_path, "module_545_frozen", "topic_010_intro")

    sections_xml = """
    <sections>
      <section module="module_545_frozen">
        <name><de>Frozen</de><en>Frozen</en></name>
        <topics><topic>intro</topic></topics>
      </section>
    </sections>
    """
    course = _build_course(tmp_path, sections_xml)

    assert len(course.sections) == 1
    topic = course.sections[0].topics[0]
    assert "module_545_frozen" in str(topic.path)
    # No loading errors — the topic was resolved.
    assert not [e for e in course.loading_errors if e.get("category") == "topic_not_found"]


def test_module_bound_two_sections_resolve_independently(tmp_path):
    """Two enabled sections binding the same topic ID to different modules
    each resolve to their own copy."""
    _make_topic_dir(tmp_path, "module_100_live", "topic_010_intro")
    _make_topic_dir(tmp_path, "module_545_frozen", "topic_010_intro")

    sections_xml = """
    <sections>
      <section module="module_100_live">
        <name><de>Live</de><en>Live</en></name>
        <topics><topic>intro</topic></topics>
      </section>
      <section module="module_545_frozen">
        <name><de>Frozen</de><en>Frozen</en></name>
        <topics><topic>intro</topic></topics>
      </section>
    </sections>
    """
    course = _build_course(tmp_path, sections_xml)
    assert len(course.sections) == 2
    assert "module_100_live" in str(course.sections[0].topics[0].path)
    assert "module_545_frozen" in str(course.sections[1].topics[0].path)


def test_topic_module_override_wins_over_section_default(tmp_path):
    """Per-topic ``module=`` overrides the section default."""
    _make_topic_dir(tmp_path, "module_100_live", "topic_010_intro")
    _make_topic_dir(tmp_path, "module_545_frozen", "topic_010_intro")

    sections_xml = """
    <sections>
      <section module="module_545_frozen">
        <name><de>Mixed</de><en>Mixed</en></name>
        <topics>
          <topic>intro</topic>
          <topic module="module_100_live">intro</topic>
        </topics>
      </section>
    </sections>
    """
    course = _build_course(tmp_path, sections_xml)
    topics = course.sections[0].topics
    assert "module_545_frozen" in str(topics[0].path)
    assert "module_100_live" in str(topics[1].path)


def test_module_bound_resolution_silences_duplicate_warning(tmp_path):
    """When all references to a duplicate topic ID are module-bound, no
    duplicate-id warning is emitted (the duplicate is unambiguous in
    practice)."""
    _make_topic_dir(tmp_path, "module_100_live", "topic_010_intro")
    _make_topic_dir(tmp_path, "module_545_frozen", "topic_010_intro")

    sections_xml = """
    <sections>
      <section module="module_545_frozen">
        <name><de>Frozen</de><en>Frozen</en></name>
        <topics><topic>intro</topic></topics>
      </section>
    </sections>
    """
    course = _build_course(tmp_path, sections_xml)
    dup_warnings = [w for w in course.loading_warnings if w.get("category") == "duplicate_topic_id"]
    assert dup_warnings == []


def test_unbound_resolution_still_warns_about_duplicates(tmp_path):
    """An unbound section that hits a duplicate ID still triggers the
    duplicate-id warning (existing behavior preserved)."""
    _make_topic_dir(tmp_path, "module_100_live", "topic_010_intro")
    _make_topic_dir(tmp_path, "module_545_frozen", "topic_010_intro")

    sections_xml = """
    <sections>
      <section>
        <name><de>Live</de><en>Live</en></name>
        <topics><topic>intro</topic></topics>
      </section>
    </sections>
    """
    course = _build_course(tmp_path, sections_xml)
    dup_warnings = [w for w in course.loading_warnings if w.get("category") == "duplicate_topic_id"]
    assert len(dup_warnings) >= 1


def test_module_bound_unknown_module_is_topic_not_found(tmp_path):
    """A section binding to a non-existent module yields a topic_not_found
    error (with the module name in the message)."""
    _make_topic_dir(tmp_path, "module_100_live", "topic_010_intro")

    sections_xml = """
    <sections>
      <section module="module_999_nope">
        <name><de>X</de><en>X</en></name>
        <topics><topic>intro</topic></topics>
      </section>
    </sections>
    """
    course = _build_course(tmp_path, sections_xml)
    nf = [e for e in course.loading_errors if e.get("category") == "topic_not_found"]
    assert len(nf) == 1
    assert "module_999_nope" in nf[0]["message"]


# ---------------------------------------------------------------------------
# <include> resolution (Feature 1: shared-source includes)
#
# These tests cover PR1.3 — wiring `IncludeSpec` from the parsed spec
# through `Course._build_topics` into `ResolvedInclude` (course-root
# resolved) and onward to `Topic.from_spec`. The downstream splice
# behavior is covered in tests/core/topic_test.py.
# ---------------------------------------------------------------------------


def _make_include_source_dir(course_root: Path) -> Path:
    """Lay down examples/SimpleChatbot/src/simple_chatbot/ with two files."""
    src = course_root / "examples" / "SimpleChatbot" / "src" / "simple_chatbot"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("VERSION = '0.1.0'\n", encoding="utf-8")
    (src / "main.py").write_text("def run():\n    pass\n", encoding="utf-8")
    return src


def test_topic_level_include_is_resolved_against_course_root(tmp_path):
    """A `<include>` on a `<topic>` splices files in under the topic dir."""
    _make_topic_dir(tmp_path, "module_100_live", "topic_010_intro")
    src = _make_include_source_dir(tmp_path)

    sections_xml = """
    <sections>
      <section>
        <name><de>S</de><en>S</en></name>
        <topics>
          <topic>
            intro
            <include source="examples/SimpleChatbot/src/simple_chatbot"
                     as="simple_chatbot"/>
          </topic>
        </topics>
      </section>
    </sections>
    """
    course = _build_course(tmp_path, sections_xml)
    topic = course.sections[0].topics[0]

    spliced = {f.path: f for f in topic.files}
    init_path = topic.path / "simple_chatbot" / "__init__.py"
    main_path = topic.path / "simple_chatbot" / "main.py"
    assert init_path in spliced
    assert main_path in spliced
    # source_path resolves to the canonical on-disk source — i.e., the
    # course-root-relative `source` was joined onto course_root.
    assert spliced[init_path].source_path == src / "__init__.py"
    assert spliced[main_path].source_path == src / "main.py"


def test_section_level_include_propagates_to_all_child_topics(tmp_path):
    """A `<include>` on a `<section>` is inherited by every contained topic."""
    _make_topic_dir(tmp_path, "module_100_live", "topic_010_intro")
    _make_topic_dir(tmp_path, "module_100_live", "topic_020_outro")
    src = _make_include_source_dir(tmp_path)

    sections_xml = """
    <sections>
      <section>
        <name><de>S</de><en>S</en></name>
        <include source="examples/SimpleChatbot/src/simple_chatbot"
                 as="simple_chatbot"/>
        <topics>
          <topic>intro</topic>
          <topic>outro</topic>
        </topics>
      </section>
    </sections>
    """
    course = _build_course(tmp_path, sections_xml)
    topics = course.sections[0].topics
    assert len(topics) == 2

    for topic in topics:
        spliced_paths = {f.path for f in topic.files}
        assert topic.path / "simple_chatbot" / "__init__.py" in spliced_paths
        assert topic.path / "simple_chatbot" / "main.py" in spliced_paths

    # Sanity: both topics see the same canonical source bytes.
    intro_init = next(
        f for f in topics[0].files if f.path.name == "__init__.py" and f.source_origin
    )
    outro_init = next(
        f for f in topics[1].files if f.path.name == "__init__.py" and f.source_origin
    )
    assert intro_init.source_path == src / "__init__.py"
    assert outro_init.source_path == src / "__init__.py"


def test_topic_include_overrides_section_default_with_same_as_path(tmp_path):
    """When section + topic both supply `as_path=foo`, the topic wins."""
    _make_topic_dir(tmp_path, "module_100_live", "topic_010_intro")
    # Two distinct sources; the section-default one would contribute
    # `__init__.py` containing VERSION='0.1.0'; the override contributes
    # VERSION='9.9.9'.
    _make_include_source_dir(tmp_path)
    alt_src = tmp_path / "examples" / "AltChatbot" / "src" / "simple_chatbot"
    alt_src.mkdir(parents=True)
    (alt_src / "__init__.py").write_text("VERSION = '9.9.9'\n", encoding="utf-8")

    sections_xml = """
    <sections>
      <section>
        <name><de>S</de><en>S</en></name>
        <include source="examples/SimpleChatbot/src/simple_chatbot"
                 as="simple_chatbot"/>
        <topics>
          <topic>
            intro
            <include source="examples/AltChatbot/src/simple_chatbot"
                     as="simple_chatbot"/>
          </topic>
        </topics>
      </section>
    </sections>
    """
    course = _build_course(tmp_path, sections_xml)
    topic = course.sections[0].topics[0]

    init = next(f for f in topic.files if f.path.name == "__init__.py" and f.source_origin)
    assert init.source_path == alt_src / "__init__.py"
    assert init.source_path.read_text(encoding="utf-8") == "VERSION = '9.9.9'\n"
    # The section-default's `main.py` should NOT have leaked in: the
    # override targets the same `as_path` so it replaces the entry
    # entirely (per SectionSpec.includes_for dedup rule).
    assert not any(f.path.name == "main.py" and f.source_origin for f in topic.files)


def test_topic_include_adds_new_as_path_alongside_section_default(tmp_path):
    """A topic-level include with a *new* `as_path` appends without
    displacing the inherited section default."""
    _make_topic_dir(tmp_path, "module_100_live", "topic_010_intro")
    _make_include_source_dir(tmp_path)
    helper_src = tmp_path / "examples" / "Helper"
    helper_src.mkdir(parents=True)
    (helper_src / "util.py").write_text("X = 1\n", encoding="utf-8")

    sections_xml = """
    <sections>
      <section>
        <name><de>S</de><en>S</en></name>
        <include source="examples/SimpleChatbot/src/simple_chatbot"
                 as="simple_chatbot"/>
        <topics>
          <topic>
            intro
            <include source="examples/Helper" as="helper"/>
          </topic>
        </topics>
      </section>
    </sections>
    """
    course = _build_course(tmp_path, sections_xml)
    topic = course.sections[0].topics[0]
    paths = {f.path for f in topic.files}
    # Inherited section default still applies.
    assert topic.path / "simple_chatbot" / "__init__.py" in paths
    # New topic-only entry was added too.
    assert topic.path / "helper" / "util.py" in paths


def test_optional_include_with_missing_source_records_no_error(tmp_path):
    """Course-level wiring honors `optional=true`: a missing source under
    the course root is silently skipped, not an error."""
    _make_topic_dir(tmp_path, "module_100_live", "topic_010_intro")
    # No `examples/Missing` dir on disk.

    sections_xml = """
    <sections>
      <section>
        <name><de>S</de><en>S</en></name>
        <topics>
          <topic>
            intro
            <include source="examples/Missing" as="missing" optional="true"/>
          </topic>
        </topics>
      </section>
    </sections>
    """
    course = _build_course(tmp_path, sections_xml)
    topic = course.sections[0].topics[0]
    # No virtual files spliced.
    assert not any(f.source_origin for f in topic.files)
    # And no include_source_missing error recorded.
    assert not [e for e in course.loading_errors if e.get("category") == "include_source_missing"]


def test_required_include_with_missing_source_records_loading_error(tmp_path):
    """Required includes whose source is missing record a structured error
    via Topic.apply_includes (PR1.2 behavior — preserved through PR1.3
    wiring)."""
    _make_topic_dir(tmp_path, "module_100_live", "topic_010_intro")

    sections_xml = """
    <sections>
      <section>
        <name><de>S</de><en>S</en></name>
        <topics>
          <topic>
            intro
            <include source="examples/Missing" as="missing"/>
          </topic>
        </topics>
      </section>
    </sections>
    """
    course = _build_course(tmp_path, sections_xml)
    errs = [e for e in course.loading_errors if e.get("category") == "include_source_missing"]
    assert len(errs) == 1
    assert "examples" in errs[0]["message"]


# ---------------------------------------------------------------------------
# Orphan HTTP-replay staging cassette sweep
# ---------------------------------------------------------------------------


_ORPHAN_CASSETTE_YAML = """\
interactions:
- request:
    method: GET
    uri: {uri}
    body: '{body}'
    headers:
      accept: ['*/*']
  response:
    status: {{code: 200, message: OK}}
    headers:
      content-type: [text/plain]
    body: {{string: '{body}'}}
version: 1
"""


def _write_orphan_cassette(path: Path, *, uri: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_ORPHAN_CASSETTE_YAML.format(uri=uri, body=body), encoding="utf-8")


def test_sweep_orphan_staging_files_merges_and_deletes(tmp_path):
    """Pre-build sweep folds orphan ``.staging-*`` cassettes into canonical.

    Regression: a notebook killed mid-build leaves a
    ``slides_x.http-cassette.yaml.staging-<pid>-<uuid>`` file behind. If
    the next build's ``compute_other_files`` reaches it before
    ``merge_staging_into_canonical`` does, payload b64 encoding crashes
    with ``FileNotFoundError`` because a concurrent worker may delete the
    staging file mid-glob. The sweep runs eagerly at ``process_all``
    start so this race is closed before any payload is built.
    """
    import asyncio

    pytest.importorskip("vcr")
    pytest.importorskip("filelock")

    topic_dir = _make_topic_dir(tmp_path, "module_100_live", "topic_010_intro")
    # The fixture writes slides_intro.py; rename to a stem that matches
    # how cassettes are keyed (``<stem>.http-cassette.yaml``).
    canonical = topic_dir / "slides_intro.http-cassette.yaml"
    orphan_one = topic_dir / "slides_intro.http-cassette.yaml.staging-1234-abc"
    orphan_two = topic_dir / "slides_intro.http-cassette.yaml.staging-5678-def"
    _write_orphan_cassette(orphan_one, uri="http://example/orphan1", body="O1")
    _write_orphan_cassette(orphan_two, uri="http://example/orphan2", body="O2")

    sections_xml = """
    <sections>
      <section http-replay="yes">
        <name><de>S</de><en>S</en></name>
        <topics>
          <topic>intro</topic>
        </topics>
      </section>
    </sections>
    """
    course = _build_course(tmp_path, sections_xml)

    # Sanity: the topic opted into http-replay and our orphans are still
    # on disk before the sweep runs.
    nb_files = [f for f in course.files if isinstance(f, NotebookFile)]
    assert any(f.http_replay for f in nb_files), "topic must inherit http-replay='yes'"
    assert orphan_one.exists()
    assert orphan_two.exists()

    backend = PytestLocalOpsBackend()
    asyncio.run(course.process_all(backend))

    # Both orphan staging files are gone after process_all completes.
    assert not orphan_one.exists()
    assert not orphan_two.exists()

    # Their recorded interactions live on in the canonical cassette.
    assert canonical.exists()
    content = canonical.read_text(encoding="utf-8")
    assert "http://example/orphan1" in content
    assert "http://example/orphan2" in content


def test_sweep_orphan_staging_files_no_replay_topics(tmp_path):
    """A course without ``http-replay`` topics doesn't touch any cassettes.

    Defensive — the sweep runs unconditionally inside ``process_all`` so
    we make sure it short-circuits cleanly (and never imports
    ``vcrpy``/``filelock``) when no topic opted in. This also exercises
    that the sweep does not crash on a course with no notebooks at all.
    """
    import asyncio

    _make_topic_dir(tmp_path, "module_100_live", "topic_010_intro")
    sections_xml = """
    <sections>
      <section>
        <name><de>S</de><en>S</en></name>
        <topics><topic>intro</topic></topics>
      </section>
    </sections>
    """
    course = _build_course(tmp_path, sections_xml)

    # Sweep is a method so we can call it directly without spinning up
    # the full process_all pipeline.
    swept = course._sweep_orphan_cassette_staging_files()
    assert swept == 0

    # Full process_all also runs without raising.
    backend = PytestLocalOpsBackend()
    asyncio.run(course.process_all(backend))


def test_sweep_orphan_staging_files_no_orphans_present(tmp_path):
    """http-replay opt-in but no orphans → sweep is a no-op merge."""
    pytest.importorskip("vcr")
    pytest.importorskip("filelock")

    _make_topic_dir(tmp_path, "module_100_live", "topic_010_intro")
    sections_xml = """
    <sections>
      <section http-replay="yes">
        <name><de>S</de><en>S</en></name>
        <topics><topic>intro</topic></topics>
      </section>
    </sections>
    """
    course = _build_course(tmp_path, sections_xml)

    swept = course._sweep_orphan_cassette_staging_files()
    assert swept == 0


# ---------------------------------------------------------------------------
# output_root re-roots spec ``<output-targets>``
#
# Regression for issue #95 (B): ``clm build --snapshot DIR`` and
# ``clm build --output-dir DIR`` both re-root each spec ``<output-target>``
# under ``<DIR>/<target.name>/``. The previous behavior (--output-dir
# collapsed everything into the default target's ``public/speaker``
# layout — silently dropping any target whose toplevel was not
# ``public``/``speaker``, e.g. ``trainer``) caused thousands of bogus
# diffs in verify runs and is no longer supported.
# ---------------------------------------------------------------------------


def _build_course_with_targets(course_root: Path, output_root: Path | None):
    """Construct a Course from a spec with three ``<output-targets>``."""
    import io

    from clm.core.course_spec import CourseSpec

    _make_topic_dir(course_root, "module_100_live", "topic_010_intro")
    spec_xml = """
    <course>
      <name><de>Test</de><en>Test</en></name>
      <prog-lang>python</prog-lang>
      <sections>
        <section>
          <name><de>S</de><en>S</en></name>
          <topics><topic>intro</topic></topics>
        </section>
      </sections>
      <output-targets>
        <output-target name="shared"><path>output/shared</path></output-target>
        <output-target name="trainer"><path>output/trainer</path></output-target>
        <output-target name="speaker"><path>output/speaker</path></output-target>
      </output-targets>
    </course>
    """
    spec = CourseSpec.from_file(io.StringIO(spec_xml))
    return Course.from_spec(spec, course_root, output_root)


def test_output_root_reroots_spec_targets_under_output_dir(tmp_path):
    """Each spec target's ``output_root`` lives at
    ``<output_root>/<target.name>/`` so the build tree mirrors the
    per-target layout instead of collapsing into ``public/speaker``."""
    out = tmp_path / "out"
    course = _build_course_with_targets(tmp_path, out)

    target_by_name = {t.name: t for t in course.output_targets}
    assert set(target_by_name) == {"shared", "trainer", "speaker"}
    assert target_by_name["shared"].output_root == (out / "shared").resolve()
    assert target_by_name["trainer"].output_root == (out / "trainer").resolve()
    assert target_by_name["speaker"].output_root == (out / "speaker").resolve()
    # is_explicit must stay True so the path layout under each target's
    # output_root looks like a regular ``<lang>/...`` tree, not the
    # legacy ``public/<lang>/...`` shape.
    assert all(t.is_explicit for t in course.output_targets)


def test_output_root_does_not_drop_trainer_target(tmp_path):
    """The bug from issue #95 (B): trainer/ silently disappeared because
    the default target only kept the public/speaker toplevels. The
    fix must preserve every spec target."""
    out = tmp_path / "out"
    course = _build_course_with_targets(tmp_path, out)

    names = {t.name for t in course.output_targets}
    assert "trainer" in names, (
        "trainer target was dropped — regression from the old "
        "--output-dir behavior that collapsed to the default target "
        "(public/speaker only)."
    )


def test_output_root_without_spec_targets_collapses_to_dir(tmp_path):
    """When the spec has no ``<output-targets>``, ``output_root``
    produces a single default target rooted at the output dir. There's
    nothing per-target to re-root, so the behavior is the same as
    passing ``--output-dir`` to a minimal spec."""
    import io

    from clm.core.course_spec import CourseSpec

    _make_topic_dir(tmp_path, "module_100_live", "topic_010_intro")
    spec = CourseSpec.from_file(
        io.StringIO(
            """
            <course>
              <name><de>Test</de><en>Test</en></name>
              <prog-lang>python</prog-lang>
              <sections>
                <section>
                  <name><de>S</de><en>S</en></name>
                  <topics><topic>intro</topic></topics>
                </section>
              </sections>
            </course>
            """
        )
    )
    out = tmp_path / "out"
    course = Course.from_spec(spec, tmp_path, out)
    assert len(course.output_targets) == 1
    assert course.output_targets[0].output_root == out.resolve()
