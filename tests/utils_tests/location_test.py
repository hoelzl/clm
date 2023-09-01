from pathlib import Path, PurePosixPath

import pytest

from clm.utils.frozenmap import frozenmap
from clm.utils.location import FileSystemLocation, InMemoryLocation


def test_fs_location_eq(tmp_path):
    base_path = tmp_path.absolute()
    loc1 = FileSystemLocation(base_path, Path("foo"))
    loc2 = FileSystemLocation(base_path, Path("foo"))
    loc3 = FileSystemLocation(base_path, Path("bar"))
    loc4 = InMemoryLocation(base_path, Path("foo"))
    assert loc1 == loc2
    assert loc1 != loc3
    assert loc1 != loc4


@pytest.fixture
def fs_base_dir_location(tmp_path):
    return FileSystemLocation(tmp_path.absolute(), Path(""))


def test_fs_base_dir_location_name(fs_base_dir_location):
    assert fs_base_dir_location.name != ""
    assert fs_base_dir_location.name == fs_base_dir_location.base_dir.name


def test_fs_base_dir_location_absolute(fs_base_dir_location):
    assert fs_base_dir_location.absolute().is_absolute()
    assert fs_base_dir_location.absolute() == fs_base_dir_location.base_dir


def test_fs_base_dir_location_joinpath(fs_base_dir_location):
    assert fs_base_dir_location.joinpath("foo") == FileSystemLocation(
        fs_base_dir_location.base_dir, Path("foo")
    )


def test_fs_base_dir_location_truediv(fs_base_dir_location):
    assert fs_base_dir_location / "foo" == FileSystemLocation(
        fs_base_dir_location.base_dir, Path("foo")
    )


def test_fs_base_dir_location_is_dir(fs_base_dir_location):
    assert fs_base_dir_location.is_dir()


def test_fs_base_dir_location_is_not_file(fs_base_dir_location):
    assert not fs_base_dir_location.is_file()


def test_fs_base_dir_location_open(fs_base_dir_location):
    with pytest.raises(PermissionError):
        fs_base_dir_location.open("r")


def test_fs_base_dir_read_text(fs_base_dir_location):
    with pytest.raises(PermissionError):
        fs_base_dir_location.read_text()


def test_fs_base_dir_read_bytes(fs_base_dir_location):
    with pytest.raises(PermissionError):
        fs_base_dir_location.read_bytes()


@pytest.fixture
def fs_file_location(tmp_path):
    foo = tmp_path / "foo_file"
    foo.write_text("Content of foo")
    return FileSystemLocation(tmp_path.absolute(), Path("foo_file"))


def test_fs_file_location_name(fs_file_location):
    assert fs_file_location.name == "foo_file"


def test_fs_file_location_is_file(fs_file_location):
    assert fs_file_location.is_file()


def test_fs_file_location_is_not_dir(fs_file_location):
    assert not fs_file_location.is_dir()


def test_fs_file_location_open(fs_file_location):
    with fs_file_location.open("r") as f:
        assert f.read() == "Content of foo"


def test_fs_file_location_read_bytes(fs_file_location):
    assert fs_file_location.read_bytes() == b"Content of foo"


def test_fs_file_location_read_text(fs_file_location):
    assert fs_file_location.read_text() == "Content of foo"


def test_fs_file_location_read_text_with_encoding(fs_file_location):
    assert fs_file_location.read_text(encoding="utf-8") == "Content of foo"


@pytest.fixture
def fs_dir_location(tmp_path):
    foo = tmp_path / "foo_dir"
    foo.mkdir()
    return FileSystemLocation(tmp_path.absolute(), Path("foo_dir"))


def test_fs_dir_location_name(fs_dir_location):
    assert fs_dir_location.name == "foo_dir"


def test_fs_dir_location_absolute(fs_dir_location):
    assert fs_dir_location.absolute().is_absolute()
    assert fs_dir_location.absolute() == fs_dir_location.base_dir / "foo_dir"


def test_fs_dir_location_joinpath(fs_dir_location):
    assert fs_dir_location.joinpath("bar") == FileSystemLocation(
        fs_dir_location.base_dir, Path("foo_dir/bar")
    )


def test_fs_dir_location_truediv(fs_dir_location):
    assert fs_dir_location / "bar" == FileSystemLocation(
        fs_dir_location.base_dir, Path("foo_dir/bar")
    )


def test_fs_dir_location_is_dir(fs_dir_location):
    assert fs_dir_location.is_dir()


def test_fs_dir_location_is_not_file(fs_dir_location):
    assert not fs_dir_location.is_file()


def test_fs_dir_location_open(fs_dir_location):
    with pytest.raises(PermissionError):
        fs_dir_location.open("r")


def test_fs_dir_location_read_bytes(fs_dir_location):
    with pytest.raises(PermissionError):
        fs_dir_location.read_bytes()


def test_fs_dir_location_read_text(fs_dir_location):
    with pytest.raises(PermissionError):
        fs_dir_location.read_text()


@pytest.fixture
def fs_non_empty_dir_location(tmp_path):
    foo = tmp_path / "foo_dir"
    foo.mkdir()
    (foo / "file1.txt").write_text("Content of file1")
    (foo / "file2.txt").write_text("Content of file2")
    (foo / "dir1").mkdir()
    (foo / "dir1" / "file3.txt").write_text("Content of file3")
    return FileSystemLocation(tmp_path.absolute(), Path("foo_dir"))


def test_fs_iterdir(fs_non_empty_dir_location):
    expected = {
        fs_non_empty_dir_location / "file1.txt",
        fs_non_empty_dir_location / "file2.txt",
        fs_non_empty_dir_location / "dir1",
    }
    result = set(fs_non_empty_dir_location.iterdir())
    assert result == expected


def test_in_memory_location_eq():
    base_path = Path("foo")
    loc1 = InMemoryLocation(base_path, Path("bar"))
    loc2 = InMemoryLocation(base_path, Path("bar"))
    loc3 = InMemoryLocation(base_path, Path("baz"))
    loc4 = FileSystemLocation(base_path, Path("bar"))
    assert loc1 == loc2
    assert loc1 != loc3
    assert loc1 != loc4


@pytest.fixture
def in_memory_base_dir_location():
    return InMemoryLocation(PurePosixPath("/base_dir"), Path(""), frozenmap())


def test_in_memory_base_dir_location_name(in_memory_base_dir_location):
    assert in_memory_base_dir_location.name != ""
    assert in_memory_base_dir_location.name == in_memory_base_dir_location.base_dir.name


def test_in_memory_base_dir_location_absolute(in_memory_base_dir_location):
    assert in_memory_base_dir_location.absolute().is_absolute()
    assert (
        in_memory_base_dir_location.absolute() == in_memory_base_dir_location.base_dir
    )


def test_in_memory_base_dir_location_joinpath(in_memory_base_dir_location):
    with pytest.raises(FileNotFoundError):
        in_memory_base_dir_location.joinpath("foo")


def test_in_memory_base_dir_location_truediv(in_memory_base_dir_location):
    with pytest.raises(FileNotFoundError):
        in_memory_base_dir_location / "foo"


def test_in_memory_base_dir_location_is_dir(in_memory_base_dir_location):
    assert in_memory_base_dir_location.is_dir()


def test_in_memory_base_dir_location_is_not_file(in_memory_base_dir_location):
    assert not in_memory_base_dir_location.is_file()


def test_in_memory_base_dir_location_open(in_memory_base_dir_location):
    with pytest.raises(PermissionError):
        in_memory_base_dir_location.open("r")


def test_in_memory_base_dir_read_text(in_memory_base_dir_location):
    with pytest.raises(PermissionError):
        in_memory_base_dir_location.read_text()


def test_in_memory_base_dir_read_bytes(in_memory_base_dir_location):
    with pytest.raises(PermissionError):
        in_memory_base_dir_location.read_bytes()


@pytest.fixture
def in_memory_file_location():
    return InMemoryLocation(
        PurePosixPath("/base_dir"), Path("foo_file"), "Contents of foo_file"
    )


def test_in_memory_file_location_name(in_memory_file_location):
    assert in_memory_file_location.name == "foo_file"


def test_in_memory_file_location_is_file(in_memory_file_location):
    assert in_memory_file_location.is_file()


def test_in_memory_file_location_is_not_dir(in_memory_file_location):
    assert not in_memory_file_location.is_dir()


def test_in_memory_file_location_open(in_memory_file_location):
    with in_memory_file_location.open("r") as f:
        assert f.read() == "Contents of foo_file"


def test_in_memory_file_location_open_binary(in_memory_file_location):
    with in_memory_file_location.open("rb") as f:
        assert f.read() == b"Contents of foo_file"


def test_in_memory_file_location_read_bytes(in_memory_file_location):
    assert in_memory_file_location.read_bytes() == b"Contents of foo_file"


def test_in_memory_file_location_read_text(in_memory_file_location):
    assert in_memory_file_location.read_text() == "Contents of foo_file"


def test_in_memory_file_location_read_text_with_encoding(in_memory_file_location):
    assert in_memory_file_location.read_text(encoding="utf-8") == "Contents of foo_file"


@pytest.fixture
def in_memory_dir_location():
    return InMemoryLocation(PurePosixPath("/base_dir"), Path("foo_dir"), frozenmap())


def test_in_memory_dir_location_is_dir(in_memory_dir_location):
    assert in_memory_dir_location.is_dir()


def test_in_memory_dir_location_is_not_file(in_memory_dir_location):
    assert not in_memory_dir_location.is_file()


def test_in_memory_dir_location_open(in_memory_dir_location):
    with pytest.raises(PermissionError):
        in_memory_dir_location.open("r")


def test_in_memory_dir_location_read_bytes(in_memory_dir_location):
    with pytest.raises(PermissionError):
        in_memory_dir_location.read_bytes()


def test_in_memory_dir_location_read_text(in_memory_dir_location):
    with pytest.raises(PermissionError):
        in_memory_dir_location.read_text()


@pytest.fixture
def in_memory_non_empty_dir_location():
    return InMemoryLocation(
        PurePosixPath("/base_dir"),
        Path("foo_dir"),
        frozenmap(
            {
                "file1.txt": "Content of file1",
                "file2.txt": "Content of file2",
                "dir1": frozenmap({"file3.txt": "Content of file3"}),
            }
        ),
    )


def test_in_memory_non_empty_dir_location_iterdir(in_memory_non_empty_dir_location):
    expected = {
        in_memory_non_empty_dir_location / "file1.txt",
        in_memory_non_empty_dir_location / "file2.txt",
        in_memory_non_empty_dir_location / "dir1",
    }
    result = set(in_memory_non_empty_dir_location.iterdir())

    assert result == expected


def test_in_memory_non_empty_dir_location_read_text(in_memory_non_empty_dir_location):
    assert (
        in_memory_non_empty_dir_location / "file1.txt"
    ).read_text() == "Content of file1"
    assert (
        in_memory_non_empty_dir_location / "file2.txt"
    ).read_text() == "Content of file2"
    assert (
        in_memory_non_empty_dir_location / "dir1" / "file3.txt"
    ).read_text() == "Content of file3"
    assert (
        in_memory_non_empty_dir_location / "dir1/file3.txt"
    ).read_text() == "Content of file3"
    with pytest.raises(PermissionError):
        (in_memory_non_empty_dir_location / "dir1").read_text()
