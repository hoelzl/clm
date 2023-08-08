from pathlib import PurePosixPath, PureWindowsPath
from unittest import mock

from clm.core.directory_kind import GeneralDirectory, IGNORED_KIND


def test_eq_for_general_directory_true_cases():
    assert GeneralDirectory(PurePosixPath('/a')) == GeneralDirectory(
        PurePosixPath('/a')
    )
    assert GeneralDirectory(PureWindowsPath(r'C:\a')) == GeneralDirectory(
        PureWindowsPath(r'C:\a')
    )


def test_eq_for_general_directory_false_cases():
    assert GeneralDirectory(PurePosixPath('/a')) != GeneralDirectory(
        PurePosixPath('/b')
    )
    assert GeneralDirectory(PureWindowsPath(r'C:\a')) != GeneralDirectory(
        PureWindowsPath(r'C:\b')
    )
    assert GeneralDirectory(PurePosixPath('/a')) != 0


def test_classify_file_for_general_directory():
    file_path = mock.Mock(is_file=lambda: True)
    assert (
        GeneralDirectory(PurePosixPath('/a')).classify(file_path) == 'DataFile'
    )


def test_classify_directory_for_general_directory():
    directory_path = mock.Mock(is_file=lambda: False)
    assert (
        GeneralDirectory(PurePosixPath('/a')).classify(directory_path)
        == IGNORED_KIND
    )
