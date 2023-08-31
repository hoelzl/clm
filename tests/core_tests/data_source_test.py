from pathlib import Path

import pytest

from clm.core.course_spec import CourseSpec
from clm.core.data_source_spec import DataSourceSpec
from clm.data_sources.factory import data_source_from_spec
from clm.specs.course_layouts import legacy_python_course_layout


@pytest.fixture
def course_spec():
    return CourseSpec(
        Path("/course").absolute(),
        Path("/out/").absolute(),
        legacy_python_course_layout(Path("/course").absolute()),
    )


def test_data_source_from_spec_for_relative_path(course_spec):
    ds = DataSourceSpec("my_doc.py", "nb", "Notebook", 1)
    data_source = data_source_from_spec(course_spec, ds)

    assert data_source.source_file.as_posix().endswith("/course/my_doc.py")
    assert data_source.target_dir_fragment == "nb"
    assert data_source.prog_lang == "python"
    assert data_source.file_num == 1


def test_data_source_from_spec_for_absolute_path(course_spec):
    ds = DataSourceSpec("/foo/my_doc.py", "nb", "Notebook", 1)
    data_source = data_source_from_spec(course_spec, ds)

    assert data_source.source_file.as_posix().endswith("/foo/my_doc.py")
    assert data_source.target_dir_fragment == "nb"
    assert data_source.prog_lang == "python"
    assert data_source.file_num == 1


def test_data_source_from_spec_for_image_file(course_spec):
    ds = DataSourceSpec("foo.png", "img", "DataFile", 1)
    data_source = data_source_from_spec(course_spec, ds)

    assert data_source.source_file.as_posix().endswith("/course/foo.png")
    assert data_source.target_dir_fragment == "img"
    assert data_source.prog_lang == "python"
    assert data_source.file_num == 1


def test_data_source_from_spec_for_folder(course_spec):
    ds = DataSourceSpec("my-folder", "data", "Folder", 1)
    data_source = data_source_from_spec(course_spec, ds)

    assert data_source.source_file.as_posix().endswith("/course/my-folder")
    assert data_source.target_dir_fragment == "data"
    assert data_source.prog_lang == "python"
    assert data_source.file_num == 1
