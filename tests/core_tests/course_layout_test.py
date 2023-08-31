from pathlib import Path
from unittest import mock

import pytest

from clm.core.course_layout import (
    CourseLayout,
)
from clm.core.course_layout import (
    get_course_layout_from_string,
    course_layout_to_dict,
    SKIP_DIRS,
    course_layout_from_dict,
)
from clm.core.directory_kind import GeneralDirectory, IGNORED_LABEL
from clm.specs.directory_kinds import ExampleDirectory


@pytest.fixture
def mock_layout(mocker):
    def mock_course_layout(base_path: Path):
        return CourseLayout("mock_layout", base_path, (("data", GeneralDirectory),))

    mocker.patch(
        "clm.core.course_layout.course_layout_registry",
        {"mock_layout": mock_course_layout},
    )

    return mock_course_layout


def test_get_course_layout_returns_existing_layout(mock_layout):
    layout = get_course_layout_from_string("mock_layout", Path("/foo/bar"))
    assert isinstance(layout, CourseLayout)
    assert layout.name == "mock_layout"
    assert layout.base_path == Path("/foo/bar")


def test_get_course_layout_raises_error_for_non_existing_layout():
    with pytest.raises(ValueError, match="Unknown course layout: non_existing_layout"):
        get_course_layout_from_string("non_existing_layout", Path("/foo/bar"))


def test_course_layout_to_dict(mock_layout):
    base_dir = Path("/foo/bar")
    layout = get_course_layout_from_string("mock_layout", base_dir)
    assert course_layout_to_dict(layout) == {
        "name": "mock_layout",
        "base_path": str(base_dir),
        "directory_patterns": [["data", "GeneralDirectory"]],
        "kept_files": ["__init__.py", "__main__.py"],
        "ignored_files": [".gitignore"],
        "ignored_files_regex": "^[_.](.*)(\\.*)?",
        "ignored_directories": list(SKIP_DIRS),
        "ignored_directories_regex": "(.*\\.egg-info.*|.*cmake-build-.*)",
        "default_directory_kind": "GeneralDirectory",
        "_resolved_directory_paths": {},
    }


def test_course_layout_from_dict(mock_layout):
    base_dir = Path("/foo/bar")
    layout = get_course_layout_from_string("mock_layout", base_dir)
    layout_dict = course_layout_to_dict(layout)
    assert course_layout_from_dict(layout_dict) == layout


def test_course_layout_from_dict_with_defaults(mock_layout):
    base_dir = Path("/foo/bar")
    layout = get_course_layout_from_string("mock_layout", base_dir)
    layout_dict = {
        "name": "mock_layout",
        "base_path": str(base_dir),
        "directory_patterns": [["data", "GeneralDirectory"]],
    }
    assert course_layout_from_dict(layout_dict) == layout


@pytest.fixture
def course_layout():
    return CourseLayout(
        name="test_layout",
        base_path=Path("course_path").absolute(),
        default_directory_kind=GeneralDirectory(),
        directory_patterns=(("examples", ExampleDirectory),),
    )


def test_classification_for_general_directory(course_layout):
    dir_path = mock.Mock()
    dir_path.is_dir.return_value = True
    dir_path.parent = Path("path")
    assert course_layout.classify(dir_path) == "DataFile"


def test_classification_for_examples_directory(course_layout):
    assert course_layout.classify(Path("examples")) == IGNORED_LABEL


def test_classification_for_example_solution(course_layout):
    subdir_path = mock.Mock()
    subdir_path.is_dir.return_value = True
    subdir_path.name = "my_example"
    subdir_path.parent = Path("examples")
    assert course_layout.classify(subdir_path) == "ExampleSolution"


@pytest.mark.parametrize(
    "name",
    [
        "my_example_starter_kit",
        "my_example_sk",
        "MyExampleStarterKit",
        "MyExampleSK",
    ],
)
def test_classification_for_example_starter_kit(course_layout, name):
    subdir_path = mock.Mock()
    subdir_path.is_dir.return_value = True
    subdir_path.name = name
    subdir_path.parent = Path("examples")
    assert course_layout.classify(subdir_path) == "ExampleStarterKit"
