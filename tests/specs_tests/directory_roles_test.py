from pathlib import PurePosixPath
from unittest import mock

import pytest

from clm.core.directory_kind import IGNORED_KIND
from clm.specs.directory_kinds import (
    ExampleDirectory,
    LegacyExampleDirectory,
    NotebookDirectory,
)


@pytest.mark.parametrize(
    'name',
    [
        'topic_123.py',
        'nb_123.ru',
        'lecture_123.java',
        'ws_123.py',
        'workshop_123.cpp',
    ],
)
def test_notebook_directory_for_notebook_file(name):
    file_path = mock.Mock(is_file=lambda: True)
    file_path.name = name
    unit = NotebookDirectory(PurePosixPath('/a'))
    assert unit.classify(file_path) == 'Notebook'


@pytest.mark.parametrize(
    'name',
    [
        'random_file.py',
        'topic_123.txt',
    ],
)
def test_notebook_directory_for_non_notebook_file(name):
    file_path = mock.Mock(is_file=lambda: True)
    file_path.name = name
    unit = NotebookDirectory(PurePosixPath('/a'))
    assert unit.classify(file_path) == 'DataFile'


def test_notebook_directory_for_non_file():
    dir_path = mock.Mock(is_file=lambda: False)
    unit = NotebookDirectory(PurePosixPath('/a'))
    assert unit.classify(dir_path) == IGNORED_KIND


def test_example_directory_for_completed_example():
    dir_path = mock.Mock(is_dir=lambda: True)
    dir_path.name = 'my_example'
    unit = ExampleDirectory(PurePosixPath('/a/examples'))
    assert unit.classify(dir_path) == 'ExampleSolution'


@pytest.mark.parametrize(
    'name',
    [
        'foo_starter_kit',
        'foo_sk',
        'FooStarterKit',
        'FooSK',
    ],
)
def test_example_directory_for_example_starter_kit(name):
    dir_path = mock.Mock(is_dir=lambda: True)
    dir_path.name = name
    unit = ExampleDirectory(PurePosixPath('/a/examples'))
    assert unit.classify(dir_path) == 'ExampleStarterKit'


def test_example_directory_for_file():
    file_path = mock.Mock(is_dir=lambda: False, is_file=lambda: True)
    unit = ExampleDirectory(PurePosixPath('/a/examples'))
    assert unit.classify(file_path) == 'DataFile'


def test_example_directory_for_non_dir_non_file():
    dir_path = mock.Mock(is_dir=lambda: False, is_file=lambda: False)
    unit = ExampleDirectory(PurePosixPath('/a/examples'))
    assert unit.classify(dir_path) == IGNORED_KIND


def test_legacy_example_directory_for_completed_example():
    dir_path = mock.Mock(is_dir=lambda: True)
    dir_path.name = 'my_example'
    unit = LegacyExampleDirectory(PurePosixPath('/a/examples'))
    assert unit.classify(dir_path) == 'Folder'


@pytest.mark.parametrize(
    'name',
    [
        'foo_starter_kit',
        'foo_sk',
        'FooStarterKit',
        'FooSK',
    ],
)
def test_legacy_example_directory_for_example_starter_kit(name):
    dir_path = mock.Mock(is_dir=lambda: True)
    dir_path.name = name
    unit = LegacyExampleDirectory(PurePosixPath('/a/examples'))
    assert unit.classify(dir_path) == 'Folder'


def test_legacy_example_directory_for_file():
    file_path = mock.Mock(is_dir=lambda: False, is_file=lambda: True)
    unit = LegacyExampleDirectory(PurePosixPath('/a/examples'))
    assert unit.classify(file_path) == 'DataFile'


def test_legacy_example_directory_for_non_dir_non_file():
    dir_path = mock.Mock(is_dir=lambda: False, is_file=lambda: False)
    unit = LegacyExampleDirectory(PurePosixPath('/a/examples'))
    assert unit.classify(dir_path) == IGNORED_KIND
