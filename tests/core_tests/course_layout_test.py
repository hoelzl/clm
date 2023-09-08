from pathlib import Path
from unittest import mock

from clm.core.course_layout import (
    PathClassifier,
)
from clm.core.course_layout import (
    get_course_layout,
    course_layout_to_dict,
    SKIP_DIRS,
    course_layout_from_dict,
)
from clm.core.directory_kind import IGNORED_LABEL
from clm.specs.directory_kinds import ExampleDirectory
from spec_fixtures import *
from filesystem_fixtures import small_python_course_file_system


def test_get_course_layout_returns_existing_layout(mock_layout):
    layout = get_course_layout("mock_layout")
    assert isinstance(layout, CourseLayout)
    assert layout.name == "mock_layout"


def test_get_course_layout_raises_error_for_non_existing_layout():
    with pytest.raises(ValueError, match="Unknown course layout: non_existing_layout"):
        get_course_layout("non_existing_layout")


def test_course_layout_to_dict(mock_layout):
    layout = get_course_layout("mock_layout")
    assert course_layout_to_dict(layout) == {
        "name": "mock_layout",
        "directory_patterns": [["data", "GeneralDirectory"]],
        "kept_files": ["__init__.py", "__main__.py"],
        "ignored_files": [".gitignore"],
        "ignored_files_regex": "^[_.](.*)(\\.*)?",
        "ignored_directories": list(SKIP_DIRS),
        "ignored_directories_regex": "(.*\\.egg-info.*|.*cmake-build-.*)",
        "default_directory_kind": "GeneralDirectory",
    }


def test_course_layout_from_dict(mock_layout):
    layout = get_course_layout("mock_layout")
    layout_dict = course_layout_to_dict(layout)
    assert course_layout_from_dict(layout_dict) == layout


def test_course_layout_from_dict_with_defaults(mock_layout):
    base_dir = Path("/foo/bar")
    layout = get_course_layout("mock_layout")
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
        default_directory_kind=GeneralDirectory(),
        directory_patterns=(("examples", ExampleDirectory),),
    )


def test_classifier_for_general_directory(course_layout):
    dir_path = mock.Mock()
    dir_path.is_dir.return_value = True
    dir_path.parent = Path("path")
    classifier = PathClassifier(course_layout)
    assert classifier.classify(dir_path) == "DataFile"


def test_classifier_for_examples_directory(
    course_layout, small_python_course_file_system
):
    classifier = PathClassifier(course_layout)
    assert (
        classifier.classify(
            InMemoryLocation("/course", "examples", small_python_course_file_system)
        )
        == IGNORED_LABEL
    )


def test_classifier_for_example_solution(course_layout):
    subdir_path = mock.Mock()
    subdir_path.is_dir.return_value = True
    subdir_path.name = "my_example"
    subdir_path.parent = Path("examples")
    classifier = PathClassifier(course_layout)
    assert classifier.classify(subdir_path) == "ExampleSolution"


@pytest.mark.parametrize(
    "name",
    [
        "my_example_starter_kit",
        "my_example_sk",
        "MyExampleStarterKit",
        "MyExampleSK",
    ],
)
def test_classifier_for_example_starter_kit(course_layout, name):
    subdir_path = mock.Mock()
    subdir_path.is_dir.return_value = True
    subdir_path.name = name
    subdir_path.parent = Path("examples")
    classifier = PathClassifier(course_layout)
    assert classifier.classify(subdir_path) == "ExampleStarterKit"
