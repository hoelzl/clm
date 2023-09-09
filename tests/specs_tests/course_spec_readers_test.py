from io import StringIO
from pathlib import PurePosixPath, PureWindowsPath

from clm.specs.course_spec_readers import CourseSpecCsvReader
from clm.utils.location import InMemoryLocation
from filesystem_fixtures import python_course_file_system

_CSV_SOURCE = """\
Base Dir:,course/
Target Dir:,output/
Template Dir:,other-course/templates/
Language:,de

/tmp/course/slides/module_10_intro/topic_10_python.py,my_dir,Notebook
/tmp/course/slides/module_10_intro/ws_10_python.py,my_dir,Notebook
/tmp/course/slides/module_10_intro/python_file.py,my_dir,Notebook
/tmp/course/slides/module_10_intro/img/my_img.png,my_dir,DataFile
/tmp/course/examples/non_affine_file.py,my_dir,DataFile
/tmp/course/slides/module_20_data_types/topic_10_ints.py,my_dir,Notebook
/tmp/course/slides/module_20_data_types/ws_10_ints.py,my_dir,Notebook
/tmp/course/slides/module_20_data_types/topic_20_floats.py,my_dir,Notebook
/tmp/course/slides/module_20_data_types/ws_20_floats.py,my_dir,Notebook
/tmp/course/slides/module_20_data_types/topic_30_lists.py,my_dir,Notebook
/tmp/course/slides/module_20_data_types/ws_30_lists.py,my_dir,Notebook
"""


def test_read_csv_from_stream_for_posix_path(python_course_file_system):
    csv_stream = StringIO(_CSV_SOURCE)
    unit = CourseSpecCsvReader.read_csv_from_stream(
        csv_stream,
        PurePosixPath("/tmp/"),
        lambda root_path, relative_path: InMemoryLocation(
            root_path, relative_path, python_course_file_system
        ),
    )

    assert unit.source_loc.as_posix() == "/tmp/course"
    assert unit.target_loc.as_posix() == "/tmp/output"
    assert unit.template_loc.as_posix() == "/tmp/other-course/templates"
    assert unit.lang == "de"
    assert unit.prog_lang == "python"


def test_read_csv_from_stream_for_windows_path(python_course_file_system):
    csv_stream = StringIO(_CSV_SOURCE)
    unit = CourseSpecCsvReader.read_csv_from_stream(
        csv_stream,
        PureWindowsPath("C:/tmp/"),
        lambda root_path, relative_path: InMemoryLocation(
            PureWindowsPath(root_path), relative_path, python_course_file_system
        ),
    )

    assert unit.source_loc.as_posix() == "C:/tmp/course"
    assert unit.target_loc.as_posix() == "C:/tmp/output"
    assert unit.template_loc.as_posix() == "C:/tmp/other-course/templates"
    assert unit.lang == "de"
    assert unit.prog_lang == "python"
