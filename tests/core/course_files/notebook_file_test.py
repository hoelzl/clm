from pathlib import Path
from typing import cast

import pytest

from clm.core.course_file import CourseFile
from clm.core.course_files.notebook_file import NotebookFile
from clm.core.course_spec import TopicSpec
from clm.core.operations.process_notebook import ProcessNotebookOperation
from clm.core.section import Section
from clm.core.topic import Topic
from clm.core.utils.text_utils import Text
from clm.infrastructure.backends.dummy_backend import DummyBackend
from clm.infrastructure.operation import Concurrently
from clm.infrastructure.utils.path_utils import output_specs

NOTEBOOK_FILE = "slides_some_topic_from_test_1.py"


def test_file_from_path_notebook(course_1, section_1, topic_1):
    file_path = topic_1.path / NOTEBOOK_FILE

    unit = CourseFile.from_path(course_1, file_path, topic_1)

    assert isinstance(unit, NotebookFile)
    assert unit.path == file_path
    assert unit.topic == topic_1
    assert unit.section == section_1
    assert unit.relative_path == Path(NOTEBOOK_FILE)
    assert unit.generated_outputs == set()
    assert unit.source_outputs == frozenset()
    assert unit.prog_lang == "python"


async def test_file_from_path_notebook_operations(course_1, topic_1):
    file_path = topic_1.path / NOTEBOOK_FILE

    unit = CourseFile.from_path(course_1, file_path, topic_1)

    process_op = await unit.get_processing_operation(course_1.output_root)
    assert isinstance(process_op, Concurrently)

    ops = cast(list[ProcessNotebookOperation], list(process_op.operations))
    op = ops[0]
    assert op.output_file == course_1.output_root / (
        "public/Mein Kurs-de/Folien/Html/Code-Along/Woche 1/00 Folien von Test 1.html"
    )

    assert len(ops) == len(list(output_specs(course_1, course_1.output_root)))
    assert all(isinstance(op, ProcessNotebookOperation) for op in ops)
    assert all(op.input_file == unit for op in ops)
    assert all(op.output_file.stem == "00 Folien von Test 1" for op in ops if op.language == "de")
    assert all(
        op.output_file.stem == "00 Some Topic from Test 1" for op in ops if op.language == "en"
    )


@pytest.fixture
def notebook_file_and_output_dir(course_1, topic_1):
    file_path = topic_1.path / NOTEBOOK_FILE
    notebook_file = course_1.find_file(file_path)
    output_dir = course_1.output_root
    return notebook_file, output_dir


async def test_notebook_file_executes_calls_backend(notebook_file_and_output_dir, mocker):
    spy = mocker.spy(DummyBackend, "execute_operation")
    backend = DummyBackend()
    notebook_file, output_dir = notebook_file_and_output_dir

    unit = await notebook_file.get_processing_operation(output_dir)
    await unit.execute(backend)

    # The backend is called once for each output spec
    assert spy.call_count == len(list(output_specs(notebook_file.course, Path())))


async def test_notebook_file_source_outputs(notebook_file_and_output_dir):
    backend = DummyBackend()
    notebook_file, output_dir = notebook_file_and_output_dir

    unit = await notebook_file.get_processing_operation(output_dir)
    await unit.execute(backend)

    assert notebook_file.source_outputs == frozenset()


async def test_notebook_file_generated_outputs(notebook_file_and_output_dir):
    backend = DummyBackend()
    notebook_file, output_dir = notebook_file_and_output_dir

    unit = await notebook_file.get_processing_operation(output_dir)
    await unit.execute(backend)

    public_de = "public/Mein Kurs-de/Folien"
    public_en = "public/My Course-en/Slides"
    speaker_de = "speaker/Mein Kurs-de/Folien"
    speaker_en = "speaker/My Course-en/Slides"

    name_de = "01 Folien von Test 1"
    name_en = "01 Some Topic from Test 1"

    assert notebook_file.generated_outputs == {
        # Public/DE
        output_dir / f"{public_de}/Html/Code-Along/Woche 1/{name_de}.html",
        output_dir / f"{public_de}/Html/Completed/Woche 1/{name_de}.html",
        output_dir / f"{public_de}/Notebooks/Code-Along/Woche 1/{name_de}.ipynb",
        output_dir / f"{public_de}/Notebooks/Completed/Woche 1/{name_de}.ipynb",
        output_dir / f"{public_de}/Python/Code-Along/Woche 1/{name_de}.py",
        output_dir / f"{public_de}/Python/Completed/Woche 1/{name_de}.py",
        # Public/EN
        output_dir / f"{public_en}/Html/Code-Along/Week 1/{name_en}.html",
        output_dir / f"{public_en}/Html/Completed/Week 1/{name_en}.html",
        output_dir / f"{public_en}/Notebooks/Code-Along/Week 1/{name_en}.ipynb",
        output_dir / f"{public_en}/Notebooks/Completed/Week 1/{name_en}.ipynb",
        output_dir / f"{public_en}/Python/Code-Along/Week 1/{name_en}.py",
        output_dir / f"{public_en}/Python/Completed/Week 1/{name_en}.py",
        # Speaker/DE
        output_dir / f"{speaker_de}/Html/Woche 1/{name_de}.html",
        output_dir / f"{speaker_de}/Notebooks/Woche 1/{name_de}.ipynb",
        output_dir / f"{speaker_de}/Python/Woche 1/{name_de}.py",
        # Speaker/EN
        output_dir / f"{speaker_en}/Html/Week 1/{name_en}.html",
        output_dir / f"{speaker_en}/Notebooks/Week 1/{name_en}.ipynb",
        output_dir / f"{speaker_en}/Python/Week 1/{name_en}.py",
    }


# --- Tests for prog_lang override chain ---


class TestProgLangOverrideChain:
    """Test the prog_lang priority: topic attr > course prog_lang > extension default."""

    def test_py_file_uses_extension_mapping(self, course_1, topic_1):
        """A .py file always resolves to 'python' from extension."""
        file_path = topic_1.path / NOTEBOOK_FILE
        nb = CourseFile.from_path(course_1, file_path, topic_1)
        assert nb.prog_lang == "python"

    def test_md_file_defaults_to_python(self, course_1, tmp_path):
        """A .md file with no course prog_lang defaults to 'python'."""
        # course_1 has prog_lang="python", so create a minimal course with empty prog_lang
        from clm.core.course import Course
        from clm.core.course_spec import CourseSpec

        spec = CourseSpec(
            name=Text(de="Test", en="Test"),
            prog_lang="",
            description=Text(de="", en=""),
            certificate=Text(de="", en=""),
            sections=[],
        )
        course = Course(spec=spec, course_root=tmp_path, output_root=tmp_path)
        section = Section(name=Text(de="S", en="S"), course=course)
        topic_spec = TopicSpec(id="t")
        md_file = tmp_path / "slides_test.md"
        md_file.write_text("# Title\nSome content\n", encoding="utf-8")
        topic = Topic.from_spec(topic_spec, section=section, path=tmp_path)

        nb = CourseFile.from_path(course, md_file, topic)
        assert isinstance(nb, NotebookFile)
        assert nb.prog_lang == "python"

    def test_md_file_uses_course_prog_lang(self, course_1, tmp_path):
        """A .md file picks up the course-level prog_lang."""
        from clm.core.course import Course
        from clm.core.course_spec import CourseSpec

        spec = CourseSpec(
            name=Text(de="Test", en="Test"),
            prog_lang="cpp",
            description=Text(de="", en=""),
            certificate=Text(de="", en=""),
            sections=[],
        )
        course = Course(spec=spec, course_root=tmp_path, output_root=tmp_path)
        section = Section(name=Text(de="S", en="S"), course=course)
        topic_spec = TopicSpec(id="t")
        md_file = tmp_path / "slides_test.md"
        md_file.write_text("# Title\nSome content\n", encoding="utf-8")
        topic = Topic.from_spec(topic_spec, section=section, path=tmp_path)

        nb = CourseFile.from_path(course, md_file, topic)
        assert isinstance(nb, NotebookFile)
        assert nb.prog_lang == "cpp"

    def test_topic_prog_lang_overrides_course(self, course_1, tmp_path):
        """Topic-level prog_lang attribute overrides course-level."""
        from clm.core.course import Course
        from clm.core.course_spec import CourseSpec

        spec = CourseSpec(
            name=Text(de="Test", en="Test"),
            prog_lang="python",
            description=Text(de="", en=""),
            certificate=Text(de="", en=""),
            sections=[],
        )
        course = Course(spec=spec, course_root=tmp_path, output_root=tmp_path)
        section = Section(name=Text(de="S", en="S"), course=course)
        topic_spec = TopicSpec(id="t", prog_lang="java")
        md_file = tmp_path / "slides_test.md"
        md_file.write_text("# Title\nSome content\n", encoding="utf-8")
        topic = Topic.from_spec(topic_spec, section=section, path=tmp_path)

        nb = CourseFile.from_path(course, md_file, topic)
        assert isinstance(nb, NotebookFile)
        assert nb.prog_lang == "java"

    def test_topic_prog_lang_overrides_extension_for_py(self, course_1, tmp_path):
        """Topic-level prog_lang even overrides extension-based detection for .py files."""
        from clm.core.course import Course
        from clm.core.course_spec import CourseSpec

        spec = CourseSpec(
            name=Text(de="Test", en="Test"),
            prog_lang="python",
            description=Text(de="", en=""),
            certificate=Text(de="", en=""),
            sections=[],
        )
        course = Course(spec=spec, course_root=tmp_path, output_root=tmp_path)
        section = Section(name=Text(de="S", en="S"), course=course)
        topic_spec = TopicSpec(id="t", prog_lang="typescript")
        py_file = tmp_path / "slides_test.py"
        py_file.write_text("# %% [markdown]\n# Title\n", encoding="utf-8")
        topic = Topic.from_spec(topic_spec, section=section, path=tmp_path)

        nb = CourseFile.from_path(course, py_file, topic)
        assert isinstance(nb, NotebookFile)
        assert nb.prog_lang == "typescript"
