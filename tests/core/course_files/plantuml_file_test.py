from pathlib import Path

import pytest

from clx.infrastructure.backends.dummy_backend import DummyBackend
from clx.core.course_file import CourseFile
from clx.core.course_files.plantuml_file import PlantUmlFile
from clx.core.operations.convert_plantuml_file import ConvertPlantUmlFileOperation


PLANTUML_FILE = "pu/my_diag.pu"

def test_file_from_path_plant_uml(course_1, section_1, topic_1, caplog):
    file_path = topic_1.path / PLANTUML_FILE
    expected_output = file_path.parents[1] / "img/my_diag.png"

    unit = CourseFile.from_path(course_1, file_path, topic_1)

    assert isinstance(unit, PlantUmlFile)
    assert unit.path == file_path
    assert unit.topic == topic_1
    assert unit.section == section_1
    assert unit.relative_path == Path(PLANTUML_FILE)
    assert unit.generated_outputs == set()
    assert unit.source_outputs == frozenset({expected_output})


async def test_file_from_path_plant_uml_operations(course_1, topic_1):
    file_path = topic_1.path / PLANTUML_FILE

    unit = CourseFile.from_path(course_1, file_path, topic_1)

    process_op = await unit.get_processing_operation(course_1.output_root)
    assert isinstance(process_op, ConvertPlantUmlFileOperation)
    assert process_op.input_file == unit
    assert process_op.output_file == topic_1.path / "img/my_diag.png"



@pytest.fixture
def plantuml_file_and_output_dir(course_1, topic_1):
    file_path = topic_1.path / PLANTUML_FILE
    plantuml_file = course_1.find_file(file_path)
    output_dir = course_1.output_root
    return plantuml_file, output_dir


async def test_drawio_file_execute_calls_backend(plantuml_file_and_output_dir, mocker):
    spy = mocker.spy(DummyBackend, "execute_operation")
    backend = DummyBackend()
    plantuml_file, output_dir = plantuml_file_and_output_dir

    unit = await plantuml_file.get_processing_operation(output_dir)
    await unit.execute(backend)

    assert spy.call_count == 1


async def test_drawio_file_source_outputs(plantuml_file_and_output_dir):
    backend = DummyBackend()
    plantuml_file, output_dir = plantuml_file_and_output_dir

    unit = await plantuml_file.get_processing_operation(output_dir)
    await unit.execute(backend)

    assert plantuml_file.source_outputs == frozenset(
        [plantuml_file.path.parents[1] / f"img/{plantuml_file.path.stem}.png"]
    )


async def test_drawio_file_generated_outputs(plantuml_file_and_output_dir):
    backend = DummyBackend()
    plantuml_file, output_dir = plantuml_file_and_output_dir

    unit = await plantuml_file.get_processing_operation(output_dir)
    await unit.execute(backend)

    assert plantuml_file.generated_outputs == frozenset()

