from clm.core.data_source import DataSource

# noinspection PyUnresolvedReferences
from spec_fixtures import *


def test_data_source_from_spec_for_notebook(
    python_course_spec, python_course_data_source_spec_dir
):
    data_source_spec = python_course_data_source_spec_dir["topic_100_intro.py"]
    data_source = DataSource.from_spec(python_course_spec, data_source_spec)

    assert (
        data_source.source_loc.as_posix()
        == "/course_dir/python_courses/slides/module_100_intro/topic_100_intro.py"
    )
    assert data_source.target_dir_fragment == "Intro"
    assert data_source.prog_lang == "python"
    assert data_source.file_num == 1


def test_data_source_from_spec_for_data_file(
    python_course_spec, python_course_data_source_spec_dir
):
    data_source_spec = python_course_data_source_spec_dir["adv-design-01.png"]
    data_source = DataSource.from_spec(python_course_spec, data_source_spec)

    assert (
        data_source.source_loc.as_posix()
        == "/course_dir/python_courses/slides/module_290_grasp/img/adv-design-01.png"
    )
    assert data_source.target_dir_fragment == "Arch/Grasp/img"
    assert data_source.prog_lang == "python"
    assert data_source.file_num == 1


def test_data_source_from_spec_for_folder(
    python_course_spec, python_course_data_source_spec_dir
):
    data_source_spec = python_course_data_source_spec_dir["Employee"]
    data_source = DataSource.from_spec(python_course_spec, data_source_spec)

    assert (
        data_source.source_loc.as_posix()
        == "/course_dir/python_courses/examples/Employee"
    )
    assert data_source.target_dir_fragment == "$keep"
    assert data_source.prog_lang == "python"
    assert data_source.file_num == 2
