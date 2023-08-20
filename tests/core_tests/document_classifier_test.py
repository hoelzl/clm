from pathlib import Path
from unittest import mock

import pytest

from clm.core.course_layout import (
    CourseLayout,
)
from clm.core.directory_kind import GeneralDirectory, IGNORED_LABEL
from clm.specs.directory_kinds import ExampleDirectory


@pytest.fixture
def course_layout():
    return CourseLayout(
        base_path=Path("course_path").absolute(),
        default_directory_kind=GeneralDirectory(),
        directory_patterns=[("examples", ExampleDirectory)],
    )


def test_document_classifier_for_general_directory(course_layout):
    dir_path = mock.Mock()
    dir_path.is_dir.return_value = True
    dir_path.parent = Path("path")
    assert course_layout.classify(dir_path) == "DataFile"


def test_document_classifier_for_examples_directory(course_layout):
    assert course_layout.classify(Path("examples")) == IGNORED_LABEL


def test_document_classifier_for_example_solution(course_layout):
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
def test_document_classifier_for_example_starter_kit(course_layout, name):
    subdir_path = mock.Mock()
    subdir_path.is_dir.return_value = True
    subdir_path.name = name
    subdir_path.parent = Path("examples")
    assert course_layout.classify(subdir_path) == "ExampleStarterKit"
