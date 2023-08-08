from pathlib import Path
from unittest import mock

import pytest

from clm.specs.course_layouts import legacy_python_course_layout


@pytest.mark.parametrize(
    'name',
    [
        # file without extension
        'LICENSE',
        # file with notebook extension outside notebook dir
        'README.md',
        # file in examples dir
        'examples/CMakeLists.txt',
        # file in metadata dir
        'metadata/README.md',
        # Python file outside notebook dir
        'python_courses/copy_data.py',
        # __init__.py file outside notebook dir
        'python_courses/__init__.py',
        # Non-Python file in notebook dir
        'python_courses/slides/module_290_grasp/img/adv-design-01.png',
        # Python file with name not matching notebook pattern
        'python_courses/slides/module_700_ml_basics/fragment_env.py',
    ],
)
def test_legacy_python_classifier_for_data_files(name):
    file_path = mock.Mock(is_dir=lambda: False, is_file=lambda: True)
    # Generate a real path object, so that we don't have to manually
    # figure out the values of the name and parent attributes
    name_path = Path().absolute() / Path(name)
    file_path.name = name_path.name
    file_path.parent = name_path.parent

    classifier = legacy_python_course_layout(file_path.parent)
    assert classifier.classify(file_path) == 'DataFile'


@pytest.mark.parametrize(
    'name',
    [
        'Employee',
        'EmployeeStarterKit',
        'EmployeeSK',
    ],
)
def test_legacy_python_classifier_for_folders(name):
    base_path = Path().absolute()
    example_root_path = base_path / 'examples'
    dir_path = mock.Mock(is_dir=lambda: True, is_file=lambda: False)
    dir_path.name = example_root_path / name
    dir_path.parent = example_root_path

    classifier = legacy_python_course_layout(base_path)
    assert classifier.classify(dir_path) == 'Folder'


@pytest.mark.parametrize(
    'relative_path',
    [
        'python_courses/workshops/workshop_600_california_housing.py',
        'python_courses/slides/module_700_ml/ws_400_analyze_salaries.py',
    ],
)
def test_legacy_python_classifier_for_notebooks(relative_path):
    base_path = Path().absolute()
    notebook_path = base_path / relative_path
    file_path = mock.Mock(is_dir=lambda: False, is_file=lambda: True)
    file_path.name = notebook_path.name
    file_path.parent = notebook_path.parent

    classifier = legacy_python_course_layout(base_path)
    assert classifier.classify(file_path) == 'Notebook'
