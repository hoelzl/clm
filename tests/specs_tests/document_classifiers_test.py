from pathlib import Path
from unittest import mock

import pytest

from clm.specs.document_classifiers import LEGACY_PYTHON_CLASSIFIER


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

    assert LEGACY_PYTHON_CLASSIFIER.classify(file_path) == 'DataFile'


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

    assert LEGACY_PYTHON_CLASSIFIER.classify(dir_path) == 'Folder'
