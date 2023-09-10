from clm.core.course import Course
from clm.core.data_source import DataSource
from clm.core.output_spec import create_output_spec
from clm.utils.test_utils import TestExecutor, TestNotifier


def test_python_course_from_spec(python_course_spec):
    course = Course.from_spec(python_course_spec)
    assert course.source_loc.as_posix() == "/course_dir/python_courses"
    assert course.target_loc.as_posix() == "/out/target"
    assert course.template_loc.as_posix() == "/course_dir/python_courses/templates"
    assert course.lang == "de"
    assert course.prog_lang == "python"
    assert len(course.data_sources) == 6
    assert all(lambda ds: isinstance(ds, DataSource) for ds in course.data_sources)


def test_python_course_from_spec_with_defaults(python_course_spec_with_defaults):
    course = Course.from_spec(python_course_spec_with_defaults)
    assert course.source_loc.as_posix() == "/course_dir/python_courses"
    assert course.target_loc.as_posix() == "/out/default"
    assert course.template_loc.as_posix() == "/course_dir/python_courses/templates"
    assert course.lang == "en"
    assert course.prog_lang == "python"
    assert len(course.data_sources) == 0


def test_python_course_process_for_output_spec(python_course_spec):
    course = Course.from_spec(python_course_spec)
    output_spec = create_output_spec("completed", "de", "public", "De", "py")
    notifier = TestNotifier()
    executor = TestExecutor()
    course.process_for_output_spec(executor, output_spec, notifier)
    assert notifier.processed_data_source_count == 6
    assert notifier.wrote_to_target_count == 6
