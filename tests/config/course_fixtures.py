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
    employee_sk_data_sources = course.get_data_sources_by_relative_path(
        "examples/EmployeeStarterKit"
    )
    return employee_sk_data_sources[0]


@pytest.fixture
def notebook_data_source(python_course):
    course: Course = python_course
    notebook_data_sources = course.get_data_sources_by_relative_path(
        "slides/module_100_intro/topic_100_intro.py"
    )
    return notebook_data_sources[0]


@pytest.fixture
def plain_file_data_source(python_course):
    course: Course = python_course
    plain_file_data_sources = course.get_data_sources_by_relative_path(
        "slides/module_100_intro/python_file.py"
    )
    return plain_file_data_sources[0]


@pytest.fixture
def pu_file_data_source(python_course):
    course: Course = python_course
    pu_file_data_sources = course.get_data_sources_by_relative_path(
        "slides/module_100_intro/img/my_img_a.pu"
    )
    return pu_file_data_sources[0]


@pytest.fixture
def drawio_file_data_source(python_course):
    course: Course = python_course
    drawio_file_data_sources = course.get_data_sources_by_relative_path(
        "slides/module_100_intro/img/my_img.drawio"
    )
    return drawio_file_data_sources[0]
