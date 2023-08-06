from pathlib import Path
from unittest import mock

import pytest

from clm.core.directory_role import GeneralDirectory
from clm.core.document_classifier import (
    DocumentClassifier,
    ExactPathToDirectoryRoleFun,
    PredicateToDirectoryRoleFun,
    SubpathToDirectoryRoleFun,
)
from clm.specs.directory_roles import ExampleDirectory


def test_exact_path_to_directory_role_fun():
    fun = ExactPathToDirectoryRoleFun(GeneralDirectory(), [Path('path')])
    assert fun(Path('path')) == GeneralDirectory()
    assert fun(Path('path2')) is None
    assert fun(Path('path/subpath')) is None
    assert fun(Path('path2/path')) is None


def test_subpath_to_directory_role_fun():
    fun = SubpathToDirectoryRoleFun(GeneralDirectory(), [Path('path')])
    assert fun(Path('path')) == GeneralDirectory()
    assert fun(Path('path2')) is None
    assert fun(Path('path/subpath')) == GeneralDirectory()
    assert fun(Path('path2/path')) is None


def test_predicate_to_directory_role_fun():
    fun = PredicateToDirectoryRoleFun(
        GeneralDirectory(), lambda p: p.name == 'path'
    )
    assert fun(Path('path')) == GeneralDirectory()
    assert fun(Path('path2')) is None
    assert fun(Path('path/subpath')) is None
    assert fun(Path('path2/path')) == GeneralDirectory()


@pytest.fixture
def classifier():
    return DocumentClassifier(
        default_role=GeneralDirectory(),
        path_to_dir_role_funs=[
            ExactPathToDirectoryRoleFun(ExampleDirectory(), [Path('examples')])
        ],
    )


def test_document_classifier_for_general_directory(classifier):
    dir_path = mock.Mock()
    dir_path.is_dir.return_value = True
    dir_path.parent = Path('path')
    assert classifier.classify(dir_path) == 'DataFile'


def test_document_classifier_for_examples_directory(classifier):
    assert classifier.classify(Path('examples')) is None


def test_document_classifier_for_example_solution(classifier):
    subdir_path = mock.Mock()
    subdir_path.is_dir.return_value = True
    subdir_path.name = 'my_example'
    subdir_path.parent = Path('examples')
    assert classifier.classify(subdir_path) == 'ExampleSolution'


@pytest.mark.parametrize(
    'name',
    [
        'my_example_starter_kit',
        'my_example_sk',
        'MyExampleStarterKit',
        'MyExampleSK',
    ],
)
def test_document_classifier_for_example_starter_kit(classifier, name):
    subdir_path = mock.Mock()
    subdir_path.is_dir.return_value = True
    subdir_path.name = name
    subdir_path.parent = Path('examples')
    assert classifier.classify(subdir_path) == 'ExampleStarterKit'
