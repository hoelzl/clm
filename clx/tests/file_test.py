from pathlib import Path
from typing import cast

from clx.course_file import (CourseFile, DataFile, DrawIoFile, Notebook, PlantUmlFile)
from clx.operations.process_notebook import ProcessNotebookOperation
from clx.operations.copy_file import CopyFileOperation
from clx.operations.convert_drawio_file import ConvertDrawIoFileOperation
from clx.operations.convert_plantuml_file import ConvertPlantUmlFileOperation
from clx.operation import Concurrently
from clx.utils.path_utils import output_specs

PLANT_UML_FILE = "pu/my_diag.pu"
DRAWIO_FILE = "drawio/my_drawing.drawio"
DATA_FILE = "data/test.data"
NOTEBOOK_FILE = "slides_some_topic_from_test_1.py"


def test_file_from_path_plant_uml(course_1, section_1, topic_1, caplog):
    file_path = topic_1.path / PLANT_UML_FILE
    expected_output = file_path.parents[1] / "img/my_diag.png"

    unit = CourseFile.from_path(course_1, file_path, topic_1)

    assert isinstance(unit, PlantUmlFile)
    assert unit.path == file_path
    assert unit.topic == topic_1
    assert unit.section == section_1
    assert unit.relative_path == Path(PLANT_UML_FILE)
    assert unit.generated_outputs == set()
    assert unit.generated_sources == frozenset({expected_output})


async def test_file_from_path_plant_uml_operations(course_1, topic_1):
    file_path = topic_1.path / PLANT_UML_FILE

    unit = CourseFile.from_path(course_1, file_path, topic_1)

    process_op = await unit.get_processing_operation(course_1.output_root)
    assert isinstance(process_op, ConvertPlantUmlFileOperation)
    assert process_op.input_file == unit
    assert process_op.output_file == topic_1.path / "img/my_diag.png"


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
    assert unit.generated_sources == frozenset({expected_output})


async def test_file_from_path_drawio_operations(course_1, topic_1):
    file_path = topic_1.path / DRAWIO_FILE

    unit = CourseFile.from_path(course_1, file_path, topic_1)

    process_op = await unit.get_processing_operation(course_1.output_root)
    assert isinstance(process_op, ConvertDrawIoFileOperation)
    assert process_op.input_file == unit
    assert process_op.output_file == topic_1.path / "img/my_drawing.png"


def test_file_from_path_data_file(course_1, section_1, topic_1):
    file_path = topic_1.path / DATA_FILE

    unit = CourseFile.from_path(course_1, file_path, topic_1)

    assert isinstance(unit, DataFile)
    assert unit.path == file_path
    assert unit.topic == topic_1
    assert unit.section == section_1
    assert unit.relative_path == Path("data/test.data")
    assert unit.generated_outputs == set()
    assert unit.generated_sources == frozenset()


async def test_file_from_path_data_file_operations(course_1, topic_1):
    file_path = topic_1.path / DATA_FILE

    unit = CourseFile.from_path(course_1, file_path, topic_1)

    process_op = await unit.get_processing_operation(course_1.output_root)
    assert isinstance(process_op, Concurrently)

    ops = cast(list[CopyFileOperation], list(process_op.operations))
    op = ops[0]
    assert op.output_file == course_1.output_root / (
        f"public/De/Mein Kurs/Folien/Html/Code-Along/Woche 1/{DATA_FILE}"
    )

    assert len(ops) == len(list(output_specs(course_1, course_1.output_root)))
    assert all(isinstance(op, CopyFileOperation) for op in ops)
    assert all(op.input_file == unit for op in ops)
    assert all(op.output_file.name == "test.data" for op in ops)
    assert all(op.output_file.parent.name == "data" for op in ops)


def test_file_from_path_notebook(course_1, section_1, topic_1):
    file_path = topic_1.path / NOTEBOOK_FILE

    unit = CourseFile.from_path(course_1, file_path, topic_1)

    assert isinstance(unit, Notebook)
    assert unit.path == file_path
    assert unit.topic == topic_1
    assert unit.section == section_1
    assert unit.relative_path == Path(NOTEBOOK_FILE)
    assert unit.generated_outputs == set()
    assert unit.generated_sources == frozenset()
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
    assert all(
        op.output_file.stem == "00 Folien von Test 1" for op in ops if op.lang == "de"
    )
    assert all(
        op.output_file.stem == f"00 Some Topic from Test 1"
        for op in ops
        if op.lang == "en"
    )


async def test_data_file_generated_outputs(course_1, topic_1):
    file_path = topic_1.path / DATA_FILE
    unit = CourseFile.from_path(course_1, file_path, topic_1)

    output_dir = course_1.output_root
    op = await unit.get_processing_operation(output_dir)
    await op.exec()

    assert unit.generated_sources == frozenset()
    public_de = "public/De/Mein Kurs/Folien"
    public_en = "public/En/My Course/Slides"
    speaker_de = "speaker/De/Mein Kurs/Folien"
    speaker_en = "speaker/En/My Course/Slides"

    assert unit.generated_outputs == {
        output_dir / f"{public_de}/Html/Code-Along/Woche 1/{DATA_FILE}",
        output_dir / f"{public_de}/Html/Completed/Woche 1/{DATA_FILE}",
        output_dir / f"{public_de}/Notebooks/Code-Along/Woche 1/{DATA_FILE}",
        output_dir / f"{public_de}/Notebooks/Completed/Woche 1/{DATA_FILE}",
        output_dir / f"{public_de}/Python/Completed/Woche 1/{DATA_FILE}",
        output_dir / f"{public_en}/Html/Code-Along/Week 1/{DATA_FILE}",
        output_dir / f"{public_en}/Html/Completed/Week 1/{DATA_FILE}",
        output_dir / f"{public_en}/Notebooks/Code-Along/Week 1/{DATA_FILE}",
        output_dir / f"{public_en}/Notebooks/Completed/Week 1/{DATA_FILE}",
        output_dir / f"{public_en}/Python/Completed/Week 1/{DATA_FILE}",
        output_dir / f"{public_de}/Html/Completed/Woche 1/{DATA_FILE}",
        output_dir / f"{public_de}/Notebooks/Completed/Woche 1/{DATA_FILE}",
        output_dir / f"{public_de}/Python/Completed/Woche 1/{DATA_FILE}",
        output_dir / f"{public_en}/Html/Completed/Week 1/{DATA_FILE}",
        output_dir / f"{public_en}/Notebooks/Completed/Week 1/{DATA_FILE}",
        output_dir / f"{public_en}/Python/Completed/Week 1/{DATA_FILE}",

        output_dir / f"{speaker_de}/Html/Speaker/Woche 1/{DATA_FILE}",
        output_dir / f"{speaker_de}/Notebooks/Speaker/Woche 1/{DATA_FILE}",
        output_dir / f"{speaker_en}/Html/Speaker/Week 1/{DATA_FILE}",
        output_dir / f"{speaker_en}/Notebooks/Speaker/Week 1/{DATA_FILE}",
        output_dir / f"{speaker_de}/Html/Speaker/Woche 1/{DATA_FILE}",
        output_dir / f"{speaker_de}/Notebooks/Speaker/Woche 1/{DATA_FILE}",
        output_dir / f"{speaker_en}/Html/Speaker/Week 1/{DATA_FILE}",
        output_dir / f"{speaker_en}/Notebooks/Speaker/Week 1/{DATA_FILE}",
    }
