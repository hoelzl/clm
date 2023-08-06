from unittest import mock

import pytest

from clm.specs.directory_roles import (
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
    unit = NotebookDirectory()
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
    unit = NotebookDirectory()
    assert unit.classify(file_path) is None


def test_notebook_directory_for_non_file():
    dir_path = mock.Mock(is_file=lambda: False)
    unit = NotebookDirectory()
    assert unit.classify(dir_path) is None


def test_example_directory_for_completed_example():
    dir_path = mock.Mock(is_dir=lambda: True)
    dir_path.name = 'my_example'
    unit = ExampleDirectory()
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
    unit = ExampleDirectory()
    assert unit.classify(dir_path) == 'ExampleStarterKit'


def test_example_directory_for_non_dir():
    dir_path = mock.Mock(is_dir=lambda: False)
    unit = ExampleDirectory()
    assert unit.classify(dir_path) is None


def test_legacy_example_directory_for_completed_example():
    dir_path = mock.Mock(is_dir=lambda: True)
    dir_path.name = 'my_example'
    unit = LegacyExampleDirectory()
    assert unit.classify(dir_path) == 'Example'


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
    unit = LegacyExampleDirectory()
    assert unit.classify(dir_path) == 'Example'
