from clm.core.course import Course

# noinspection PyUnresolvedReferences
from spec_fixtures import *


def test_python_course_from_spec(python_course_spec):
    course = Course.from_spec(python_course_spec)
    assert course.source_loc.as_posix() == "/course_dir/python_courses"
    assert course.target_loc.as_posix() == "/out/target"
    assert course.template_loc.as_posix() == "/course_dir/python_courses/templates"
    assert course.prog_lang == "python"
    assert len(course.data_sources) == 6
