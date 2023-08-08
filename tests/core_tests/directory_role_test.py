from pathlib import PurePosixPath, PureWindowsPath
from unittest import mock

from clm.core.directory_kind import GeneralDirectory, IGNORED_KIND


def test_eq_for_general_directory_true_cases():
    assert GeneralDirectory() == GeneralDirectory()


def test_eq_for_general_directory_false_case():
    assert GeneralDirectory() != 0


def test_classify_file_for_general_directory():
    file_path = mock.Mock(is_file=lambda: True)
    assert GeneralDirectory().classify(file_path) == 'DataFile'


def test_classify_directory_for_general_directory():
    directory_path = mock.Mock(is_file=lambda: False)
    assert GeneralDirectory().classify(directory_path) == IGNORED_KIND
