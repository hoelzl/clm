from pathlib import Path

import pytest

from clx_common.backends.dummy_backend import DummyBackend
from clx.course_file import CourseFile
from clx.course_files.drawio_file import DrawIoFile
from clx.operations.convert_drawio_file import ConvertDrawIoFileOperation

DRAWIO_FILE = "drawio/my_drawing.drawio"


def test_file_from_path_drawio(course_1, section_1, topic_1):
    file_path = topic_1.path / DRAWIO_FILE
    expected_output = file_path.parents[1] / "img/my_drawing.png"

    unit = CourseFile.from_path(course_1, file_path, topic_1)

    assert isinstance(unit, DrawIoFile)
    assert unit.path == file_path
    assert unit.topic == topic_1
    assert unit.section == section_1
    assert unit.relative_path == Path(DRAWIO_FILE)
    assert unit.generated_outputs == set()
    assert unit.source_outputs == frozenset({expected_output})


async def test_file_from_path_drawio_operations(course_1, topic_1):
    file_path = topic_1.path / DRAWIO_FILE

    unit = CourseFile.from_path(course_1, file_path, topic_1)

    process_op = await unit.get_processing_operation(course_1.output_root)
    assert isinstance(process_op, ConvertDrawIoFileOperation)
    assert process_op.input_file == unit
    assert process_op.output_file == topic_1.path / "img/my_drawing.png"


@pytest.fixture
def drawio_file_and_output_dir(course_1, topic_1):
    file_path = topic_1.path / DRAWIO_FILE
    drawio_file = course_1.find_file(file_path)
    output_dir = course_1.output_root
    return drawio_file, output_dir


async def test_drawio_file_execute_calls_backend(drawio_file_and_output_dir, mocker):
    spy = mocker.spy(DummyBackend, "execute_operation")
    backend = DummyBackend()
    drawio_file, output_dir = drawio_file_and_output_dir

    unit = await drawio_file.get_processing_operation(output_dir)
    await unit.execute(backend)

    assert spy.call_count == 1


async def test_drawio_file_source_outputs(drawio_file_and_output_dir):
    backend = DummyBackend()
    drawio_file, output_dir = drawio_file_and_output_dir

    unit = await drawio_file.get_processing_operation(output_dir)
    await unit.execute(backend)

    assert drawio_file.source_outputs == frozenset(
        [drawio_file.path.parents[1] / f"img/{drawio_file.path.stem}.png"]
    )


async def test_drawio_file_generated_outputs(drawio_file_and_output_dir):
    backend = DummyBackend()
    drawio_file, output_dir = drawio_file_and_output_dir

    unit = await drawio_file.get_processing_operation(output_dir)
    await unit.execute(backend)

    assert drawio_file.generated_outputs == frozenset()
