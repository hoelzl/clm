import pytest

from clm.core.course import Course
from clm.core.data_source import DataSource
from clm.core.output_spec import create_output_spec
from clm.data_sources.notebook_data_source import NotebookDataSource
from clm.utils.test_utils import TestExecutor, TestNotifier


def test_course_from_spec_basic_data(python_course_spec):
    course = Course.from_spec(python_course_spec)
    assert course.source_loc.as_posix() == "/course_dir/python_courses"
    assert course.target_loc.as_posix() == "/out/target"
    assert course.template_loc.as_posix() == "/course_dir/python_courses/templates"
    assert course.lang == "de"
    assert course.prog_lang == "python"
    assert len(course.data_sources) == 6


def test_python_course_from_spec_with_defaults(python_course_spec_with_defaults):
    course = Course.from_spec(python_course_spec_with_defaults)
    assert course.source_loc.as_posix() == "/course_dir/python_courses"
    assert course.target_loc.as_posix() == "/out/default"
    assert course.template_loc.as_posix() == "/course_dir/python_courses/templates"
    assert course.lang == "en"
    assert course.prog_lang == "python"
    assert len(course.data_sources) == 0


class TestPythonCourse:
    @pytest.fixture
    def python_course(self, python_course_spec):
        return Course.from_spec(python_course_spec)

    def test_get_data_source_for_existing_source(self, python_course):
        loc = python_course.source_loc / "slides/module_100_intro/topic_100_intro.py"
        expected = NotebookDataSource(
            source_loc=loc, target_dir_fragment="Intro", prog_lang="python", file_num=1
        )
        assert python_course.get_data_source(loc) == expected

    def test_get_data_source_for_non_existing_source(self, python_course):
        loc = (
            python_course.source_loc
            / "slides/module_100_intro/topic_100_non_existing.py"
        )
        assert python_course.get_data_source(loc) is None

    def test_get_data_source_with_default(self, python_course):
        loc = (
            python_course.source_loc
            / "slides/module_100_intro/topic_100_non_existing.py"
        )
        default_loc = (
            python_course.source_loc / "slides/module_100_intro/topic_100_intro.py"
        )
        default = NotebookDataSource(
            source_loc=default_loc,
            target_dir_fragment="Intro",
            prog_lang="python",
            file_num=1,
        )
        assert python_course.get_data_source(loc, default) == default

    def test_data_sources(self, python_course):
        assert len(python_course.data_sources) == 6
        assert all(isinstance(ds, DataSource) for ds in python_course.data_sources)

    def test_process_for_output_spec(self, python_course_spec):
        course = Course.from_spec(python_course_spec)
        output_spec = create_output_spec("completed", "de", "public", "De", "py")
        notifier = TestNotifier()
        executor = TestExecutor()
        course.process_for_output_spec(executor, output_spec, notifier)
        assert notifier.processed_data_source_count == 6
        assert notifier.wrote_to_target_count == 6
