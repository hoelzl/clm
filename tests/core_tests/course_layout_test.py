from pathlib import Path

import pytest

from clm.core.course_layout import (
    get_course_layout_from_string,
    CourseLayout,
    course_layout_to_dict,
    SKIP_DIRS,
    course_layout_from_dict,
)
from clm.core.directory_kind import GeneralDirectory


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
