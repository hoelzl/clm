from pathlib import Path
from unittest import mock

import pytest

from clm.specs.document_classifiers import legacy_python_classifier


@pytest.mark.parametrize(
    'name',
    [
        'LICENSE',
        'README.md',
        'examples/CMakeLists.txt',
        'metadata/README.md',
        'python_courses/__init__.py',
        'python_courses/copy_data.py',
        'python_courses/slides/module_290_grasp/img/adv-design-01.png',
        'python_courses/slides/module_700_ml_basics/fragment_env.py',
    ],
)
def test_legacy_python_classifier_for_data_files(name):
    file_path = mock.Mock(is_dir=lambda: False, is_file=lambda: True)
    # Generate a real path object, so that we don't have to manually
    # figure out the values of the name and parent attributes
    name_path = Path(name)
    file_path.name = name_path.name
    file_path.parent = name_path.parent

    classifier = legacy_python_classifier(file_path.parent)
    assert classifier.classify(file_path) == 'DataFile'


@pytest.mark.parametrize(
    'name',
    [
        'examples/Employee',
        'examples/EmployeeStarterKit',
        'examples/EmployeeSK',
    ],
)
def test_legacy_python_classifier_for_folders(name):
    dir_path = mock.Mock(is_dir=lambda: True, is_file=lambda: False)
    # Generate a real path object, so that we don't have to manually
    # figure out the values of the name and parent attributes
    name_path = Path(name)
    dir_path.name = name_path.name
    dir_path.parent = name_path.parent

    classifier = legacy_python_classifier(dir_path.parent)
    assert classifier.classify(dir_path) == 'Folder'


@pytest.mark.parametrize(
    'name',
    [
        'python_courses/workshops/workshop_600_california_housing.py',
        'python_courses/slides/module_700_ml/ws_400_analyze_salaries.py',
    ],
)
def test_legacy_python_classifier_for_notebooks(name):
    file_path = mock.Mock(is_dir=lambda: False, is_file=lambda: True)
    # Generate a real path object, so that we don't have to manually
    # figure out the values of the name and parent attributes
    name_path = Path(name)
    file_path.name = name_path.name
    file_path.parent = name_path.parent

    classifier = legacy_python_classifier(file_path.parent)
    assert classifier.classify(file_path) == 'Notebook'
