from pathlib import PurePosixPath

import pytest

from clm.core.course_layout import CourseLayout
from clm.core.course_spec import CourseSpec
from clm.core.data_source_spec import DataSourceSpec
from clm.core.directory_kind import GeneralDirectory
from clm.specs.directory_kinds import LegacyExampleDirectory, NotebookDirectory
from clm.utils.in_memory_filesystem import InMemoryFilesystem
from clm.utils.location import InMemoryLocation


@pytest.fixture
def mock_layout(mocker):
    mock_layout = CourseLayout("mock_layout", (("data", GeneralDirectory),))
    mocker.patch(
        "clm.core.course_layout.course_layout_registry",
        {"mock_layout": mock_layout},
    )

    return mock_layout


@pytest.fixture
def python_course_mock_layout(mocker):
    python_layout = CourseLayout(
        name="python",
        directory_patterns=(
            ("examples/*", LegacyExampleDirectory),
            ("slides/*", NotebookDirectory),
            ("slides/*/img/**", GeneralDirectory),
        ),
    )
    mocker.patch(
        "clm.core.course_layout.course_layout_registry",
        {"python": python_layout},
    )
    return python_layout


@pytest.fixture
def python_course_spec_csv():
    return (
        "python_courses/examples/EmployeeStarterKit, $keep, Directory, 1\n"
        "python_courses/examples/Employee, $keep, Directory, 2\n"
        "python_courses/slides/module_100_intro/topic_100_intro.py, Intro, Notebook, 1\n"
        "python_courses/slides/module_100_intro/topic_110_python_intro.py, Intro, Notebook, 2\n"
        "python_courses/slides/module_290_grasp/img/adv-design-01.png, Arch/Grasp/img, DataFile, 1\n"
        "python_courses/slides/module_290_grasp/topic_100_grasp.py, Arch/Grasp, Notebook, 1\n"
    )


@pytest.fixture
def python_course_data_source_spec_dir(python_course_file_system):
    base_dir = PurePosixPath("/course_dir/python_courses")

    def loc(file_name: str) -> InMemoryLocation:
        return InMemoryLocation(
            base_dir=base_dir,
            relative_path=file_name,
            file_system=python_course_file_system,
        )

    return {
        "topic_100_intro.py": DataSourceSpec(
            source_loc=loc("slides/module_100_intro/topic_100_intro.py"),
            target_dir_fragment="Intro",
            label="Notebook",
            file_num=1,
        ),
        "topic_110_python_intro.py": DataSourceSpec(
            source_loc=loc("slides/module_100_intro/topic_110_python.py"),
            target_dir_fragment="Intro",
            label="Notebook",
            file_num=2,
        ),
        "adv-design-01.png": DataSourceSpec(
            source_loc=loc("slides/module_290_grasp/img/adv-design-01.png"),
            target_dir_fragment="Arch/Grasp/img",
            label="DataFile",
            file_num=1,
        ),
        "topic_100_grasp.py": DataSourceSpec(
            source_loc=loc("slides/module_290_grasp/topic_100_grasp.py"),
            target_dir_fragment="Arch/Grasp",
            label="Notebook",
            file_num=1,
        ),
        "EmployeeStarterKit": DataSourceSpec(
            source_loc=loc("examples/EmployeeStarterKit"),
            target_dir_fragment="$keep",
            label="Folder",
            file_num=1,
        ),
        "Employee": DataSourceSpec(
            source_loc=loc("examples/Employee"),
            target_dir_fragment="$keep",
            label="Folder",
            file_num=2,
        ),
    }


@pytest.fixture
def python_course_data_source_specs(python_course_data_source_spec_dir):
    return list(python_course_data_source_spec_dir.values())


class InMemoryFileSystem:
    pass


@pytest.fixture
def python_course_spec_with_defaults(
    python_course_mock_layout, python_course_data_source_specs
):
    random_spec = python_course_data_source_specs[0]
    loc = random_spec.source_loc
    base_loc = loc.update(relative_path="")
    target_loc = InMemoryLocation(
        base_dir="/out/default", relative_path="", file_system=InMemoryFilesystem()
    )
    return CourseSpec(
        source_loc=base_loc,
        target_loc=target_loc,
        layout=python_course_mock_layout,
    )


@pytest.fixture
def python_course_spec(python_course_mock_layout, python_course_data_source_specs):
    random_spec = python_course_data_source_specs[0]
    loc = random_spec.source_loc
    base_loc = loc.update(relative_path="")
    target_loc = InMemoryLocation(
        base_dir="/out/target", relative_path="", file_system=InMemoryFilesystem()
    )
    return CourseSpec(
        source_loc=base_loc,
        target_loc=target_loc,
        layout=python_course_mock_layout,
        template_loc=base_loc / "templates",
        lang="de",
        prog_lang="python",
        data_source_specs=python_course_data_source_specs,
    )
