import pytest

from clm.core.course import Course
from clm.core.output_spec import CompletedOutput


@pytest.fixture
def completed_output_spec():
    return CompletedOutput("de", "public", "Notebooks/Folien")


@pytest.fixture
def python_course(python_course_spec):
    return Course.from_spec(python_course_spec)


@pytest.fixture
def employee_sk_data_source(python_course, completed_output_spec):
    course: Course = python_course
    employee_sk = course.get_data_source_by_relative_path("examples/EmployeeStarterKit")
    assert employee_sk is not None
    return employee_sk


@pytest.fixture
def notebook_data_source(python_course):
    course: Course = python_course
    notebook_data_source = course.get_data_source_by_relative_path(
        "slides/module_100_intro/topic_100_intro.py"
    )
    assert notebook_data_source is not None
    return notebook_data_source


@pytest.fixture
def plain_file_data_source(python_course):
    course: Course = python_course
    plain_file_data_source = course.get_data_source_by_relative_path(
        "slides/module_100_intro/python_file.py"
    )
    assert plain_file_data_source is not None
    return plain_file_data_source


@pytest.fixture
def pu_file_data_source(python_course):
    course: Course = python_course
    pu_file_data_source = course.get_data_source_by_relative_path(
        "slides/module_100_intro/img/my_img_a.pu"
    )
    assert pu_file_data_source is not None
    return pu_file_data_source


@pytest.fixture
def drawio_file_data_source(python_course):
    course: Course = python_course
    drawio_file_data_source = course.get_data_source_by_relative_path(
        "slides/module_100_intro/img/my_img.drawio"
    )
    assert drawio_file_data_source is not None
    return drawio_file_data_source
