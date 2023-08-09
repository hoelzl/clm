from pathlib import Path
from unittest.mock import Mock

import pytest

from clm.specs.course_spec_factory import CourseSpecFactory


@pytest.mark.parametrize(
    "name",
    [
        ".foo.md",
        "_my_file.py",
        "__hello__.py",
    ],
)
def test_is_ignored_file_true_cases(name):
    base_dir = Mock(is_absolute=lambda: True, is_dir=lambda: True)
    factory = CourseSpecFactory(base_dir, Mock(), Mock())
    assert factory._is_ignored_file(Path(name))


@pytest.mark.parametrize(
    "name",
    [
        "foo.md",
        "__init__.py",
        "__main__.py",
    ],
)
def test_is_ignored_file_false_cases(name):
    base_dir = Mock(is_absolute=lambda: True, is_dir=lambda: True)
    factory = CourseSpecFactory(base_dir, Mock(), Mock())
    assert not factory._is_ignored_file(Path(name))


@pytest.mark.parametrize(
    "name",
    [
        ".git",
        ".venv",
        "__pycache__",
        "my_dir/__pycache__",
        "my_dir/build",
        "foo/target/bar",
        "foo/.egg-info.123",
        "foo/cmake-build-debug",
        "foo/my-cmake-build-release",
    ],
)
def test_is_ignored_dir_true_cases(name):
    base_dir = Mock(is_absolute=lambda: True, is_dir=lambda: True)
    factory = CourseSpecFactory(base_dir, Mock(), Mock())
    assert factory._is_ignored_dir(Path(name))
