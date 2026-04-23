from pathlib import Path
from typing import cast

import pytest

from clm.core.course_file import CourseFile
from clm.core.course_files.data_file import DataFile
from clm.core.operations.copy_file import CopyFileOperation
from clm.infrastructure.backends.dummy_backend import DummyBackend
from clm.infrastructure.operation import Concurrently, NoOperation
from clm.infrastructure.utils.path_utils import output_specs

DATA_FILE = "data/test.data"


def test_file_from_path_data_file(course_1, section_1, topic_1):
    file_path = topic_1.path / DATA_FILE

    unit = CourseFile.from_path(course_1, file_path, topic_1)

    assert isinstance(unit, DataFile)
    assert unit.path == file_path
    assert unit.topic == topic_1
    assert unit.section == section_1
    assert unit.relative_path == Path("data/test.data")
    assert unit.generated_outputs == set()
    assert unit.source_outputs == frozenset()


async def test_file_from_path_data_file_operations(course_1, topic_1):
    file_path = topic_1.path / DATA_FILE

    unit = CourseFile.from_path(course_1, file_path, topic_1)

    process_op = await unit.get_processing_operation(course_1.output_root)
    assert isinstance(process_op, Concurrently)

    ops = cast(list[CopyFileOperation], list(process_op.operations))
    op = ops[0]
    assert op.output_file == course_1.output_root / (
        f"public/Mein Kurs-de/Folien/Html/Code-Along/Woche 1/{DATA_FILE}"
    )

    assert len(ops) == len(list(output_specs(course_1, course_1.output_root)))
    assert all(isinstance(op, CopyFileOperation) for op in ops)
    assert all(op.input_file == unit for op in ops)
    assert all(op.output_file.name == "test.data" for op in ops)
    assert all(op.output_file.parent.name == "data" for op in ops)


@pytest.fixture
def data_file_and_output_dir(course_1, topic_1):
    file_path = topic_1.path / DATA_FILE
    data_file = course_1.find_file(file_path)
    output_dir = course_1.output_root
    return data_file, output_dir


async def test_data_file_source_outputs(data_file_and_output_dir):
    backend = DummyBackend()
    data_file, output_dir = data_file_and_output_dir

    unit = await data_file.get_processing_operation(output_dir)
    await unit.execute(backend)

    assert data_file.source_outputs == frozenset()


async def test_data_file_execute_does_not_call_backend(data_file_and_output_dir, mocker):
    spy = mocker.spy(DummyBackend, "execute_operation")
    backend = DummyBackend()
    data_file, output_dir = data_file_and_output_dir

    unit = await data_file.get_processing_operation(output_dir)
    await unit.execute(backend)

    assert spy.call_count == 0


async def test_data_file_skips_output_for_http_cassette(course_1, topic_1, tmp_path):
    """Cassette files are kept visible to scanning but must not reach output."""
    cassette = topic_1.path / "slides_010v.http-cassette.yaml"
    try:
        cassette.write_text("interactions: []")
        unit = CourseFile.from_path(course_1, cassette, topic_1)
        assert isinstance(unit, DataFile)

        op = await unit.get_processing_operation(course_1.output_root)
        assert isinstance(op, NoOperation)
    finally:
        if cassette.exists():
            cassette.unlink()


async def test_data_file_generated_outputs(data_file_and_output_dir):
    backend = DummyBackend()
    data_file, output_dir = data_file_and_output_dir

    unit = await data_file.get_processing_operation(output_dir)
    await unit.execute(backend)

    public_de = "public/Mein Kurs-de/Folien"
    public_en = "public/My Course-en/Slides"
    speaker_de = "speaker/Mein Kurs-de/Folien"
    speaker_en = "speaker/My Course-en/Slides"

    assert data_file.generated_outputs == {
        # Public/DE
        output_dir / f"{public_de}/Html/Code-Along/Woche 1/{DATA_FILE}",
        output_dir / f"{public_de}/Html/Completed/Woche 1/{DATA_FILE}",
        output_dir / f"{public_de}/Html/Partial/Woche 1/{DATA_FILE}",
        output_dir / f"{public_de}/Notebooks/Code-Along/Woche 1/{DATA_FILE}",
        output_dir / f"{public_de}/Notebooks/Completed/Woche 1/{DATA_FILE}",
        output_dir / f"{public_de}/Notebooks/Partial/Woche 1/{DATA_FILE}",
        output_dir / f"{public_de}/Python/Code-Along/Woche 1/{DATA_FILE}",
        output_dir / f"{public_de}/Python/Completed/Woche 1/{DATA_FILE}",
        output_dir / f"{public_de}/Python/Partial/Woche 1/{DATA_FILE}",
        # Public/EN
        output_dir / f"{public_en}/Html/Code-Along/Week 1/{DATA_FILE}",
        output_dir / f"{public_en}/Html/Completed/Week 1/{DATA_FILE}",
        output_dir / f"{public_en}/Html/Partial/Week 1/{DATA_FILE}",
        output_dir / f"{public_en}/Notebooks/Code-Along/Week 1/{DATA_FILE}",
        output_dir / f"{public_en}/Notebooks/Completed/Week 1/{DATA_FILE}",
        output_dir / f"{public_en}/Notebooks/Partial/Week 1/{DATA_FILE}",
        output_dir / f"{public_en}/Python/Code-Along/Week 1/{DATA_FILE}",
        output_dir / f"{public_en}/Python/Completed/Week 1/{DATA_FILE}",
        output_dir / f"{public_en}/Python/Partial/Week 1/{DATA_FILE}",
        # Speaker/DE
        output_dir / f"{speaker_de}/Html/Woche 1/{DATA_FILE}",
        output_dir / f"{speaker_de}/Notebooks/Woche 1/{DATA_FILE}",
        output_dir / f"{speaker_de}/Python/Woche 1/{DATA_FILE}",
        # Speaker/EN
        output_dir / f"{speaker_en}/Html/Week 1/{DATA_FILE}",
        output_dir / f"{speaker_en}/Notebooks/Week 1/{DATA_FILE}",
        output_dir / f"{speaker_en}/Python/Week 1/{DATA_FILE}",
    }
