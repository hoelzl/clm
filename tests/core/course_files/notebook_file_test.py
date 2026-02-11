from pathlib import Path
from typing import cast

import pytest

from clm.core.course_file import CourseFile
from clm.core.course_files.notebook_file import NotebookFile
from clm.core.operations.process_notebook import ProcessNotebookOperation
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
        "public/De/Mein Kurs/Folien/Html/Code-Along/Woche 1/00 Folien von Test 1.html"
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

    public_de = "public/De/Mein Kurs/Folien"
    public_en = "public/En/My Course/Slides"
    speaker_de = "speaker/De/Mein Kurs/Folien"
    speaker_en = "speaker/En/My Course/Slides"

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
        output_dir / f"{speaker_de}/Html/Speaker/Woche 1/{name_de}.html",
        output_dir / f"{speaker_de}/Notebooks/Speaker/Woche 1/{name_de}.ipynb",
        output_dir / f"{speaker_de}/Python/Speaker/Woche 1/{name_de}.py",
        # Speaker/EN
        output_dir / f"{speaker_en}/Html/Speaker/Week 1/{name_en}.html",
        output_dir / f"{speaker_en}/Notebooks/Speaker/Week 1/{name_en}.ipynb",
        output_dir / f"{speaker_en}/Python/Speaker/Week 1/{name_en}.py",
    }
