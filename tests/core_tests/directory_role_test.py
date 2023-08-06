from unittest import mock

from clm.core.directory_role import GeneralDirectory


def test_eq_for_general_directory():
    assert GeneralDirectory() == GeneralDirectory()
    assert GeneralDirectory() != 0


def test_classify_file_for_general_directory():
    file_path = mock.Mock(is_file=lambda: True)
    assert GeneralDirectory().classify(file_path) == 'DataFile'


def test_classify_directory_for_general_directory():
    directory_path = mock.Mock(is_file=lambda: False)
    assert GeneralDirectory().classify(directory_path) is None
