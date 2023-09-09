from pathlib import Path, PurePosixPath

import pytest

from clm.utils.in_memory_filesystem import (
    InMemoryFilesystem,
    convert_to_in_memory_filesystem,
)
from clm.utils.location import (
    FileSystemLocation,
    InMemoryLocation,
)


def test_fs_location_eq(tmp_path):
    base_path = tmp_path.absolute()
    loc1 = FileSystemLocation(base_path, Path("foo"))
    loc2 = FileSystemLocation(base_path, Path("foo"))
    loc3 = FileSystemLocation(base_path, Path("bar"))
    loc4 = InMemoryLocation(base_path, PurePosixPath("foo"), InMemoryFilesystem({}))
    assert loc1 == loc2
    assert loc1 != loc3
    assert loc1 != loc4


@pytest.fixture
def fs_base_dir_location(tmp_path):
    return FileSystemLocation(base_dir=tmp_path.absolute(), relative_path=Path(""))


def test_fs_base_dir_location_base_dir(fs_base_dir_location):
    assert fs_base_dir_location.base_dir == fs_base_dir_location.absolute()


def test_fs_base_dir_location_exists(fs_base_dir_location):
    assert fs_base_dir_location.exists()


def test_fs_base_dir_location_name(fs_base_dir_location):
    assert fs_base_dir_location.name != ""
    assert fs_base_dir_location.name == fs_base_dir_location.base_dir.name


def test_fs_base_dir_location_absolute(fs_base_dir_location):
    assert fs_base_dir_location.absolute().is_absolute()
    assert fs_base_dir_location.absolute() == fs_base_dir_location.base_dir


def test_fs_base_dir_location_match(fs_base_dir_location):
    assert fs_base_dir_location.match("*")
    assert not fs_base_dir_location.match("foo")


def test_fs_base_dir_location_mkdir(fs_base_dir_location):
    dir_loc = fs_base_dir_location / "foo"
    assert not dir_loc.exists()

    dir_loc.mkdir()
    assert dir_loc.exists()


def test_fs_base_dir_location_mkdir_parents(fs_base_dir_location):
    dir_loc = fs_base_dir_location / "foo" / "bar"
    assert not dir_loc.exists()

    dir_loc.mkdir(parents=True)
    assert dir_loc.exists()


def test_fs_base_dir_location_mkdir_exist_ok(fs_base_dir_location):
    # noinspection PyBroadException
    try:
        fs_base_dir_location.mkdir(exist_ok=True)
    except Exception:
        pytest.fail("mkdir() raised an exception unexpectedly!")


def test_fs_base_dir_location_as_posix(fs_base_dir_location):
    assert fs_base_dir_location.as_posix() == fs_base_dir_location.absolute().as_posix()


def test_fs_base_dir_location_parent(fs_base_dir_location):
    assert fs_base_dir_location.parent == fs_base_dir_location.update(
        base_dir=fs_base_dir_location.base_dir.parent
    )


def test_fs_base_dir_location_parts(fs_base_dir_location):
    assert fs_base_dir_location.parts() == fs_base_dir_location.absolute().parts


def test_fs_base_dir_location_relative_parts(fs_base_dir_location):
    assert fs_base_dir_location.relative_parts() == ()


def test_fs_base_dir_location_joinpath(fs_base_dir_location):
    assert fs_base_dir_location.joinpath("foo") == FileSystemLocation(
        fs_base_dir_location.base_dir, Path("foo")
    )


def test_fs_base_dir_location_truediv(fs_base_dir_location):
    expected = FileSystemLocation(fs_base_dir_location.base_dir, Path("foo"))
    assert fs_base_dir_location / "foo" == expected


def test_fs_base_dir_location_with_name(fs_base_dir_location):
    expected = fs_base_dir_location.update(
        base_dir=fs_base_dir_location.base_dir.with_name("new_foo")
    )
    assert fs_base_dir_location.with_name("new_foo") == expected


def test_fs_base_dir_location_with_suffix(fs_base_dir_location):
    expected = fs_base_dir_location.update(
        base_dir=fs_base_dir_location.base_dir.with_suffix(".bak")
    )
    assert fs_base_dir_location.with_suffix(".bak") == expected


def test_fs_base_dir_location_is_dir(fs_base_dir_location):
    assert fs_base_dir_location.is_dir()


def test_fs_base_dir_location_is_not_file(fs_base_dir_location):
    assert not fs_base_dir_location.is_file()


def test_fs_base_dir_location_open(fs_base_dir_location):
    # noinspection PyTypeChecker
    with pytest.raises((PermissionError, IsADirectoryError)):
        fs_base_dir_location.open("r")


def test_fs_base_dir_read_text(fs_base_dir_location):
    # noinspection PyTypeChecker
    with pytest.raises((PermissionError, IsADirectoryError)):
        fs_base_dir_location.read_text()


def test_fs_base_dir_read_bytes(fs_base_dir_location):
    # noinspection PyTypeChecker
    with pytest.raises((PermissionError, IsADirectoryError)):
        fs_base_dir_location.read_bytes()


@pytest.fixture
def fs_dirs_for_copytree(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "file1.txt").write_text("Content of file1")
    (src / "file2.txt").write_text("Content of file2")
    (src / "dir1").mkdir()
    (src / "dir1" / "file3.txt").write_text("Content of file3")
    (src / "dir1" / "dir2").mkdir()
    (src / "dir1" / "dir2" / "file4.txt").write_text("Content of file4")
    src_loc = FileSystemLocation(src.absolute(), Path(""))
    dst_loc = FileSystemLocation((tmp_path / "dst").absolute(), Path(""))
    return src_loc, dst_loc


def test_fs_copytree(fs_dirs_for_copytree):
    src_loc, dst_loc = fs_dirs_for_copytree

    src_loc.copytree(dst_loc)
    assert dst_loc.exists()
    assert dst_loc.is_dir()
    assert (dst_loc / "file1.txt").exists()
    assert (dst_loc / "file1.txt").read_text() == "Content of file1"
    assert (dst_loc / "file2.txt").exists()
    assert (dst_loc / "file2.txt").read_text() == "Content of file2"
    assert (dst_loc / "dir1").exists()
    assert (dst_loc / "dir1").is_dir()
    assert (dst_loc / "dir1" / "file3.txt").exists()
    assert (dst_loc / "dir1" / "file3.txt").read_text() == "Content of file3"
    assert (dst_loc / "dir1" / "dir2").exists()
    assert (dst_loc / "dir1" / "dir2").is_dir()
    assert (dst_loc / "dir1" / "dir2" / "file4.txt").exists()
    assert (dst_loc / "dir1" / "dir2" / "file4.txt").read_text() == "Content of file4"


def test_fs_to_in_memory_copytree(fs_dirs_for_copytree):
    src, dst = fs_dirs_for_copytree
    src_loc = FileSystemLocation(src.absolute(), Path(""))
    filesystem = InMemoryFilesystem({})
    dst_loc = InMemoryLocation(dst.absolute(), PurePosixPath(""), filesystem)
    src_loc.copytree(dst_loc)
    assert dst_loc.exists()
    assert dst_loc.is_dir()
    assert (dst_loc / "file1.txt").exists()
    assert (dst_loc / "file1.txt").read_text() == "Content of file1"
    assert (dst_loc / "file2.txt").exists()
    assert (dst_loc / "file2.txt").read_text() == "Content of file2"
    assert (dst_loc / "dir1").exists()
    assert (dst_loc / "dir1").is_dir()
    assert (dst_loc / "dir1" / "file3.txt").exists()
    assert (dst_loc / "dir1" / "file3.txt").read_text() == "Content of file3"
    assert (dst_loc / "dir1" / "dir2").exists()
    assert (dst_loc / "dir1" / "dir2").is_dir()
    assert (dst_loc / "dir1" / "dir2" / "file4.txt").exists()
    assert (dst_loc / "dir1" / "dir2" / "file4.txt").read_text() == "Content of file4"


@pytest.fixture
def fs_file_location(tmp_path):
    foo = tmp_path / "foo_file"
    foo.write_text("Content of foo")
    return FileSystemLocation(tmp_path.absolute(), Path("foo_file"))


def test_fs_file_location_base_dir(fs_file_location):
    assert fs_file_location.base_dir == fs_file_location.absolute().parent


def test_fs_file_location_exists(fs_file_location):
    assert fs_file_location.exists()


def test_fs_file_location_name(fs_file_location):
    assert fs_file_location.name == "foo_file"


def test_fs_file_location_absolute(fs_file_location):
    assert fs_file_location.absolute().is_absolute()
    assert fs_file_location.absolute() == fs_file_location.base_dir / "foo_file"


def test_fs_file_location_match(fs_file_location):
    assert fs_file_location.match("*")
    assert fs_file_location.match("foo_file")
    assert fs_file_location.match("foo_*")
    assert not fs_file_location.match("foo")


def test_fs_file_location_mkdir_fails(fs_file_location):
    with pytest.raises(FileExistsError):
        fs_file_location.mkdir()


def test_fs_file_location_parent(fs_file_location):
    assert fs_file_location.parent == fs_file_location.update(
        relative_path=fs_file_location.relative_path.__class__("")
    )


def test_fs_file_location_parts(fs_file_location):
    assert fs_file_location.parts() == fs_file_location.absolute().parts


def test_fs_file_location_relative_parts(fs_file_location):
    assert fs_file_location.relative_parts() == ("foo_file",)


def test_fs_file_location_with_name(fs_file_location):
    expected = fs_file_location.update(
        relative_path=fs_file_location.relative_path.with_name("new_foo"),
    )
    assert fs_file_location.with_name("new_foo") == expected


def test_fs_file_location_with_suffix(fs_file_location):
    expected = fs_file_location.update(
        relative_path=fs_file_location.relative_path.with_suffix(".md"),
    )
    assert fs_file_location.with_suffix(".md") == expected


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


def fs_dir_location_base_dir(fs_dir_location):
    assert fs_dir_location.base_dir == fs_dir_location.absolute().parent


def test_fs_dir_location_exists(fs_dir_location):
    assert fs_dir_location.exists()


def test_fs_dir_location_name(fs_dir_location):
    assert fs_dir_location.name == "foo_dir"


def test_fs_dir_location_absolute(fs_dir_location):
    assert fs_dir_location.absolute().is_absolute()
    assert fs_dir_location.absolute() == fs_dir_location.base_dir / "foo_dir"


def test_fs_dir_location_match(fs_dir_location):
    assert fs_dir_location.match("*")
    assert fs_dir_location.match("foo_dir")
    assert fs_dir_location.match("foo_*")
    assert not fs_dir_location.match("foo")


def test_fs_dir_location_mkdir_fails(fs_dir_location):
    with pytest.raises(FileExistsError):
        fs_dir_location.mkdir()


def test_fs_dir_location_mkdir_exist_ok(fs_dir_location):
    # noinspection PyBroadException
    try:
        fs_dir_location.mkdir(exist_ok=True)
    except Exception:
        pytest.fail("mkdir() raised an exception unexpectedly!")


def test_fs_dir_location_as_posix(fs_dir_location):
    assert fs_dir_location.as_posix() == fs_dir_location.absolute().as_posix()


def test_fs_dir_location_parent(fs_dir_location):
    assert fs_dir_location.parent == FileSystemLocation(
        fs_dir_location.base_dir, Path("")
    )


def test_fs_dir_location_parts(fs_dir_location):
    assert fs_dir_location.parts() == fs_dir_location.absolute().parts


def test_fs_dir_location_relative_parts(fs_dir_location):
    assert fs_dir_location.relative_parts() == ("foo_dir",)


def test_fs_dir_location_joinpath(fs_dir_location):
    assert fs_dir_location.joinpath("bar") == FileSystemLocation(
        fs_dir_location.base_dir, Path("foo_dir/bar")
    )


def test_fs_dir_location_truediv(fs_dir_location):
    assert fs_dir_location / "bar" == FileSystemLocation(
        fs_dir_location.base_dir, Path("foo_dir/bar")
    )


def test_fs_dir_location_with_name(fs_dir_location):
    expected = FileSystemLocation(
        fs_dir_location.base_dir, fs_dir_location.relative_path.with_name("new_foo")
    )
    assert fs_dir_location.with_name("new_foo") == expected


def test_fs_dir_location_with_name_empty(fs_dir_location):
    with pytest.raises(ValueError):
        fs_dir_location.with_name("")


def test_fs_dir_location_with_suffix(fs_dir_location):
    expected = fs_dir_location.update(
        relative_path=fs_dir_location.relative_path.with_suffix(".bak")
    )
    assert fs_dir_location.with_suffix(".bak") == expected


def test_fs_dir_location_is_dir(fs_dir_location):
    assert fs_dir_location.is_dir()


def test_fs_dir_location_is_not_file(fs_dir_location):
    assert not fs_dir_location.is_file()


def test_fs_dir_location_open(fs_dir_location):
    # noinspection PyTypeChecker
    with pytest.raises((PermissionError, IsADirectoryError)):
        fs_dir_location.open("r")


def test_fs_dir_location_read_bytes(fs_dir_location):
    # noinspection PyTypeChecker
    with pytest.raises((PermissionError, IsADirectoryError)):
        fs_dir_location.read_bytes()


def test_fs_dir_location_read_text(fs_dir_location):
    # noinspection PyTypeChecker
    with pytest.raises((PermissionError, IsADirectoryError)):
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


@pytest.fixture
def in_memory_fs():
    return convert_to_in_memory_filesystem(
        {
            "file1.txt": "Content of file1",
            "file2.txt": b"Content of file2",
            "dir1": {
                "file3.txt": "Content of file3",
                "dir2": {},
                "file4.txt": b"Content of file4",
            },
        }
    )


def test_in_memory_location_eq(in_memory_fs):
    base_path = PurePosixPath("/foo")
    loc1 = InMemoryLocation(base_path, PurePosixPath("bar"), in_memory_fs)
    loc2 = InMemoryLocation(base_path, PurePosixPath("bar"), in_memory_fs)
    loc3 = InMemoryLocation(base_path, PurePosixPath("baz"), in_memory_fs)
    assert loc1 == loc2
    assert loc1 != loc3


@pytest.fixture
def in_memory_base_dir_location(in_memory_fs):
    location = InMemoryLocation(
        PurePosixPath("/base_dir"), PurePosixPath(""), in_memory_fs
    )
    return location


def test_in_memory_base_dir_location_base_dir(in_memory_base_dir_location):
    assert (
        in_memory_base_dir_location.base_dir == in_memory_base_dir_location.absolute()
    )


def test_in_memory_base_dir_location_exists(in_memory_base_dir_location):
    assert in_memory_base_dir_location.exists()


def test_in_memory_base_dir_location_name(in_memory_base_dir_location):
    result = in_memory_base_dir_location.name
    assert result == "base_dir"


def test_in_memory_base_dir_location_absolute(in_memory_base_dir_location):
    assert in_memory_base_dir_location.absolute().is_absolute()
    assert (
        in_memory_base_dir_location.absolute() == in_memory_base_dir_location.base_dir
    )


def test_in_memory_base_dir_location_match(in_memory_base_dir_location):
    assert in_memory_base_dir_location.match("*")
    assert not in_memory_base_dir_location.match("foo")


def test_in_memory_base_dir_location_mkdir(in_memory_base_dir_location):
    dir_loc = in_memory_base_dir_location / "foo"
    assert not dir_loc.exists()

    dir_loc.mkdir()
    assert dir_loc.exists()


def test_in_memory_base_dir_location_mkdir_fails_for_existing_dir(
    in_memory_base_dir_location,
):
    dir_loc = in_memory_base_dir_location / "foo"
    dir_loc.mkdir()
    with pytest.raises(FileExistsError):
        dir_loc.mkdir()


def test_in_memory_base_dir_location_mkdir_fails_for_missing_parent(
    in_memory_base_dir_location,
):
    dir_loc = in_memory_base_dir_location / "foo" / "bar"
    with pytest.raises(FileNotFoundError):
        dir_loc.mkdir()


def test_in_memory_base_dir_location_mkdir_parents(in_memory_base_dir_location):
    dir_loc = in_memory_base_dir_location / "foo" / "bar"
    assert not dir_loc.exists()

    dir_loc.mkdir(parents=True)
    assert dir_loc.exists()


def test_in_memory_base_dir_location_mkdir_exist_ok(in_memory_base_dir_location):
    # noinspection PyBroadException
    try:
        in_memory_base_dir_location.mkdir(exist_ok=True)
    except Exception:
        pytest.fail("mkdir() raised an exception unexpectedly!")


def test_in_memory_base_dir_location_as_posix(in_memory_base_dir_location):
    assert (
        in_memory_base_dir_location.as_posix()
        == in_memory_base_dir_location.absolute().as_posix()
    )


def test_in_memory_base_dir_location_parent(in_memory_base_dir_location):
    assert in_memory_base_dir_location.parent == in_memory_base_dir_location.update(
        base_dir=in_memory_base_dir_location.base_dir.parent
    )


def test_in_memory_base_dir_location_parts(in_memory_base_dir_location):
    assert (
        in_memory_base_dir_location.parts()
        == in_memory_base_dir_location.absolute().parts
    )


def test_in_memory_base_dir_location_relative_parts(in_memory_base_dir_location):
    assert in_memory_base_dir_location.relative_parts() == ()


def test_in_memory_base_dir_location_joinpath(in_memory_base_dir_location):
    expected = InMemoryLocation(
        in_memory_base_dir_location.base_dir,
        in_memory_base_dir_location.relative_path / "foo",
        in_memory_base_dir_location._file_system,
    )
    assert in_memory_base_dir_location.joinpath("foo") == expected


def test_in_memory_base_dir_location_truediv(in_memory_base_dir_location):
    expected = InMemoryLocation(
        in_memory_base_dir_location.base_dir,
        in_memory_base_dir_location.relative_path / "foo",
        in_memory_base_dir_location._file_system,
    )
    assert in_memory_base_dir_location / "foo" == expected


def test_in_memory_base_dir_location_with_name(in_memory_base_dir_location):
    expected = InMemoryLocation(
        in_memory_base_dir_location.base_dir.with_name("new_foo"),
        in_memory_base_dir_location.relative_path,
        in_memory_base_dir_location._file_system,
    )
    result = in_memory_base_dir_location.with_name("new_foo")
    assert result == expected


def test_in_memory_base_dir_location_with_suffix(in_memory_base_dir_location):
    expected = in_memory_base_dir_location.update(
        base_dir=in_memory_base_dir_location.base_dir.with_suffix(".bak"),
    )
    assert in_memory_base_dir_location.with_suffix(".bak") == expected


def test_in_memory_base_dir_location_is_dir(in_memory_base_dir_location):
    assert in_memory_base_dir_location.is_dir()


def test_in_memory_base_dir_location_is_not_file(in_memory_base_dir_location):
    assert not in_memory_base_dir_location.is_file()


def test_in_memory_base_dir_location_open(in_memory_base_dir_location):
    # noinspection PyTypeChecker
    with pytest.raises((PermissionError, IsADirectoryError)):
        in_memory_base_dir_location.open("r")


def test_in_memory_base_dir_read_text(in_memory_base_dir_location):
    # noinspection PyTypeChecker
    with pytest.raises((PermissionError, IsADirectoryError)):
        in_memory_base_dir_location.read_text()


def test_in_memory_base_dir_read_bytes(in_memory_base_dir_location):
    # noinspection PyTypeChecker
    with pytest.raises((PermissionError, IsADirectoryError)):
        in_memory_base_dir_location.read_bytes()


@pytest.fixture
def in_memory_file_location(in_memory_fs):
    return InMemoryLocation(PurePosixPath("/base_dir"), Path("file1.txt"), in_memory_fs)


def test_in_memory_file_location_base_dir(in_memory_file_location):
    assert in_memory_file_location.base_dir == in_memory_file_location.absolute().parent


def test_in_memory_file_location_exists(in_memory_file_location):
    assert in_memory_file_location.exists()


def test_in_memory_file_location_name(in_memory_file_location):
    assert in_memory_file_location.name == "file1.txt"


def test_in_memory_file_location_absolute(in_memory_file_location):
    assert in_memory_file_location.absolute().is_absolute()
    assert (
        in_memory_file_location.absolute()
        == in_memory_file_location.base_dir / "file1.txt"
    )


def test_in_memory_file_location_match(in_memory_file_location):
    assert in_memory_file_location.match("*")
    assert in_memory_file_location.match("file1.txt")
    assert in_memory_file_location.match("file*")
    assert not in_memory_file_location.match("file1")


def test_in_memory_file_location_mkdir_fails(in_memory_file_location):
    with pytest.raises(FileExistsError):
        in_memory_file_location.mkdir()


def test_in_memory_file_location_parent(in_memory_file_location):
    assert in_memory_file_location.parent == in_memory_file_location.update(
        relative_path=in_memory_file_location.relative_path.__class__("")
    )


def test_in_memory_file_location_parts(in_memory_file_location):
    assert in_memory_file_location.parts() == in_memory_file_location.absolute().parts


def test_in_memory_file_location_relative_parts(in_memory_file_location):
    assert in_memory_file_location.relative_parts() == ("file1.txt",)


def test_in_memory_file_location_with_name(in_memory_file_location):
    expected = InMemoryLocation(
        in_memory_file_location.base_dir,
        in_memory_file_location.relative_path.with_name("new_file"),
        in_memory_file_location._file_system,
    )
    assert in_memory_file_location.with_name("new_file") == expected


def test_in_memory_file_location_with_name_empty(in_memory_file_location):
    with pytest.raises(ValueError):
        in_memory_file_location.with_name("")


def test_in_memory_file_location_is_file(in_memory_file_location):
    assert in_memory_file_location.is_file()


def test_in_memory_file_location_is_not_dir(in_memory_file_location):
    assert not in_memory_file_location.is_dir()


def test_in_memory_file_location_open(in_memory_file_location):
    with in_memory_file_location.open("r") as f:
        assert f.read() == "Content of file1"


def test_in_memory_file_location_open_binary(in_memory_file_location):
    with in_memory_file_location.open("rb") as f:
        assert f.read() == b"Content of file1"


def test_in_memory_file_location_read_bytes(in_memory_file_location):
    assert in_memory_file_location.read_bytes() == b"Content of file1"


def test_in_memory_file_location_read_text(in_memory_file_location):
    assert in_memory_file_location.read_text() == "Content of file1"


def test_in_memory_file_location_read_text_with_encoding(in_memory_file_location):
    assert in_memory_file_location.read_text(encoding="utf-8") == "Content of file1"


@pytest.fixture
def in_memory_dir_location(in_memory_fs):
    return InMemoryLocation(PurePosixPath("/base_dir"), Path("dir1"), in_memory_fs)


def test_in_memory_dir_location_name(in_memory_dir_location):
    assert in_memory_dir_location.name == "dir1"


def test_in_memory_dir_location_absolute(in_memory_dir_location):
    assert in_memory_dir_location.absolute().is_absolute()
    assert in_memory_dir_location.absolute() == in_memory_dir_location.base_dir / "dir1"


def test_in_memory_dir_location_match(in_memory_dir_location):
    assert in_memory_dir_location.match("*")
    assert in_memory_dir_location.match("dir1")
    assert in_memory_dir_location.match("dir*")
    assert not in_memory_dir_location.match("dir1.txt")


def test_in_memory_dir_location_with_name(in_memory_dir_location):
    expected = InMemoryLocation(
        in_memory_dir_location.base_dir,
        in_memory_dir_location.relative_path.with_name("new_foo"),
        in_memory_dir_location._file_system,
    )
    assert in_memory_dir_location.with_name("new_foo") == expected


def test_in_memory_dir_location_with_name_empty(in_memory_dir_location):
    with pytest.raises(ValueError):
        in_memory_dir_location.with_name("")


def test_in_memory_dir_location_is_dir(in_memory_dir_location):
    assert in_memory_dir_location.is_dir()


def test_in_memory_dir_location_is_not_file(in_memory_dir_location):
    assert not in_memory_dir_location.is_file()


def test_in_memory_dir_location_open(in_memory_dir_location):
    # noinspection PyTypeChecker
    with pytest.raises((PermissionError, IsADirectoryError)):
        in_memory_dir_location.open("r")


def test_in_memory_dir_location_read_bytes(in_memory_dir_location):
    # noinspection PyTypeChecker
    with pytest.raises((PermissionError, IsADirectoryError)):
        in_memory_dir_location.read_bytes()


def test_in_memory_dir_location_read_text(in_memory_dir_location):
    # noinspection PyTypeChecker
    with pytest.raises((PermissionError, IsADirectoryError)):
        in_memory_dir_location.read_text()


@pytest.fixture
def in_memory_non_empty_base_dir_location(in_memory_fs):
    return InMemoryLocation(PurePosixPath("/base_dir"), Path(""), in_memory_fs)


def test_in_memory_non_empty_base_dir_location_iterdir(
    in_memory_non_empty_base_dir_location,
):
    expected = {
        in_memory_non_empty_base_dir_location / "file1.txt",
        in_memory_non_empty_base_dir_location / "file2.txt",
        in_memory_non_empty_base_dir_location / "dir1",
    }
    result = set(in_memory_non_empty_base_dir_location.iterdir())

    assert result == expected


@pytest.fixture
def in_memory_non_empty_dir_location(in_memory_fs):
    return InMemoryLocation(PurePosixPath("/base_dir"), Path("dir1"), in_memory_fs)


def test_in_memory_non_empty_dir_location_iterdir(in_memory_non_empty_dir_location):
    expected = {
        in_memory_non_empty_dir_location / "file3.txt",
        in_memory_non_empty_dir_location / "file4.txt",
        in_memory_non_empty_dir_location / "dir2",
    }
    result = set(in_memory_non_empty_dir_location.iterdir())

    assert result == expected


def test_in_memory_non_empty_dir_location_read_text(
    in_memory_non_empty_base_dir_location,
):
    assert (
        in_memory_non_empty_base_dir_location / "file1.txt"
    ).read_text() == "Content of file1"
    assert (
        in_memory_non_empty_base_dir_location / "file2.txt"
    ).read_text() == "Content of file2"
    assert (
        in_memory_non_empty_base_dir_location / "dir1" / "file3.txt"
    ).read_text() == "Content of file3"
    assert (
        in_memory_non_empty_base_dir_location / "dir1/file3.txt"
    ).read_text() == "Content of file3"
    # noinspection PyTypeChecker
    with pytest.raises((PermissionError, IsADirectoryError)):
        (in_memory_non_empty_base_dir_location / "dir1").read_text()
