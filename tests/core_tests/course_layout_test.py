from pathlib import Path

import pytest

from clm.core.course_layout import get_course_layout, CourseLayout


@pytest.fixture
def mock_layout(mocker):
    def mock_course_layout(base_path: Path):
        return CourseLayout("mock_layout", base_path, [])

    mocker.patch(
        "clm.core.course_layout.course_layout_registry",
        {"mock_layout": mock_course_layout},
    )

    return mock_course_layout


def test_get_course_layout_returns_existing_layout(mock_layout):
    layout = get_course_layout("mock_layout", Path("/foo/bar"))
    assert isinstance(layout, CourseLayout)
    assert layout.name == "mock_layout"
    assert layout.base_path == Path("/foo/bar")


def test_get_course_layout_raises_error_for_non_existing_layout():
    with pytest.raises(ValueError, match="Unknown course layout: non_existing_layout"):
        get_course_layout("non_existing_layout", Path("/foo/bar"))
