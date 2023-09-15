from pathlib import PurePosixPath

from networkx import DiGraph

from clm.core.course import Course
from clm.core.data_source import DataSource
from clm.data_sources.notebook_data_source import NotebookDataSource
from clm.utils.test_utils import TestExecutor, TestNotifier


def test_course_from_spec_basic_data(python_course_spec):
    course = Course.from_spec(python_course_spec)
    assert course.source_loc.as_posix() == "/course_dir/python_courses"
    assert course.target_loc.as_posix() == "/out/target"
    assert course.template_loc.as_posix() == "/course_dir/python_courses/templates"
    assert course.lang == "de"
    assert course.prog_lang == "python"
    assert len(course.data_sources) == len(python_course_spec.data_source_specs)


def test_python_course_from_spec_with_defaults(python_course_spec_with_defaults):
    course = Course.from_spec(python_course_spec_with_defaults)
    assert course.source_loc.as_posix() == "/course_dir/python_courses"
    assert course.target_loc.as_posix() == "/out/default"
    assert course.template_loc.as_posix() == "/course_dir/python_courses/templates"
    assert course.lang == "en"
    assert course.prog_lang == "python"
    assert len(course.data_sources) == 0


class TestPythonCourse:
    def test_get_data_source_for_existing_source(self, python_course):
        rel = "slides/module_100_intro/topic_100_intro.py"
        loc = python_course.source_loc / rel
        rel_path = PurePosixPath(rel)
        expected = NotebookDataSource(
            source_loc=loc, target_dir_fragment="Intro", prog_lang="python", file_num=1
        )
        assert python_course.get_data_source(loc) == expected
        assert python_course.get_data_source_by_relative_path(rel) == expected
        assert python_course.get_data_source_by_relative_path(rel_path) == expected

    def test_get_data_source_for_non_existing_source(self, python_course):
        rel = "slides/module_100_intro/topic_100_non_existing.py"
        rel_path = PurePosixPath(rel)
        loc = python_course.source_loc / rel
        assert python_course.get_data_source(loc) is None
        assert python_course.get_data_source_by_relative_path(rel) is None
        assert python_course.get_data_source_by_relative_path(rel_path) is None

    def test_get_data_source_with_default(self, python_course):
        rel = "slides/module_100_intro/topic_100_non_existing.py"
        rel_path = PurePosixPath(rel)
        loc = python_course.source_loc / rel
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
        assert python_course.get_data_source_by_relative_path(rel, default) == default
        assert (
            python_course.get_data_source_by_relative_path(rel_path, default) == default
        )

    @staticmethod
    def img_edge(loc, img_name, file_name):
        return loc / "img" / img_name, loc / file_name

    def test_dependency_graph(self, python_course):
        graph: DiGraph = python_course.dependency_graph
        loc = python_course.source_loc / "slides/module_100_intro"

        assert graph.has_edge(*self.img_edge(loc, "my_img.drawio", "img/my_img.svg"))
        assert graph.has_edge(*self.img_edge(loc, "my_img_a.pu", "img/my_img_a.svg"))
        assert graph.has_edge(*self.img_edge(loc, "my_img.svg", "topic_100_intro.py"))
        assert graph.has_edge(*self.img_edge(loc, "my_img_a.svg", "topic_100_intro.py"))
        assert graph.has_edge(*self.img_edge(loc, "my_img_b.png", "topic_100_intro.py"))
        assert graph.has_edge(*self.img_edge(loc, "my_img_c.svg", "topic_100_intro.py"))

    def test_data_sources(self, python_course, python_course_spec):
        expected_num_ds = len(python_course_spec.data_source_specs)
        assert len(python_course.data_sources) == expected_num_ds
        assert all(isinstance(ds, DataSource) for ds in python_course.data_sources)

    def test_process_for_output_spec(
        self, python_course, python_course_spec, completed_output_spec
    ):
        num_ds = len(python_course_spec.data_source_specs)
        notifier = TestNotifier()
        executor = TestExecutor()
        python_course.process_for_output_spec(executor, completed_output_spec, notifier)
        assert notifier.processed_data_source_count == num_ds
        assert notifier.wrote_to_target_count == num_ds
