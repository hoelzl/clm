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


# noinspection PyMethodMayBeStatic
class GenericBaseDirLocationTests:
    def test_base_dir(self, location):
        assert location.base_dir == location.absolute()

    def test_suffix(self, location):
        assert location.suffix == ""

    def test_exists(self, location):
        assert location.exists()

    def test_absolute(self, location):
        assert location.absolute().is_absolute()
        assert location.absolute() == location.base_dir

    def test_match(self, location):
        assert location.match("*")
        assert not location.match("foo")

    def test_mkdir(self, location):
        dir_loc = location / "foo"
        assert not dir_loc.exists()

        dir_loc.mkdir()
        assert dir_loc.exists()

    def test_mkdir_fails_for_missing_parent(
        self,
        location,
    ):
        dir_loc = location / "foo" / "bar"
        with pytest.raises(FileNotFoundError):
            dir_loc.mkdir()

    def test_mkdir_parents(self, location):
        dir_loc = location / "foo" / "bar"
        assert not dir_loc.exists()

        dir_loc.mkdir(parents=True)
        assert dir_loc.exists()

    def test_mkdir_fails_for_existing_dir(
        self,
        location,
    ):
        dir_loc = location / "foo"
        dir_loc.mkdir()
        with pytest.raises(FileExistsError):
            dir_loc.mkdir()

    def test_mkdir_exist_ok(self, location):
        # noinspection PyBroadException
        try:
            location.mkdir(exist_ok=True)
        except Exception:
            pytest.fail("mkdir() raised an exception unexpectedly!")

    def test_as_posix(self, location):
        assert location.as_posix() == location.absolute().as_posix()

    def test_parent(self, location):
        assert location.parent == location.update(base_dir=location.base_dir.parent)

    def test_parts(self, location):
        assert location.parts == location.absolute().parts

    def test_relative_parts(self, location):
        assert location.relative_parts == ()

    def test_joinpath(self, location):
        assert location.joinpath("foo") == location.update(relative_path="foo")

    def test_truediv(self, location):
        assert location / "foo" == location.joinpath("foo")

    def test_with_name(self, location):
        expected = location.update(base_dir=location.base_dir.with_name("new_foo"))
        assert location.with_name("new_foo") == expected

    def test_with_suffix(self, location):
        expected = location.update(base_dir=location.base_dir.with_suffix(".bak"))
        assert location.with_suffix(".bak") == expected

    def test_is_dir(self, location):
        assert location.is_dir()

    def test_is_not_file(self, location):
        assert not location.is_file()

    def test_open(self, location):
        # noinspection PyTypeChecker
        with pytest.raises((PermissionError, IsADirectoryError)):
            location.open("r")

    def test_read_text(self, location):
        # noinspection PyTypeChecker
        with pytest.raises((PermissionError, IsADirectoryError)):
            location.read_text()

    def test_read_bytes(self, location):
        # noinspection PyTypeChecker
        with pytest.raises((PermissionError, IsADirectoryError)):
            location.read_bytes()


class TestFileSystemBaseDirLocation(GenericBaseDirLocationTests):
    @pytest.fixture
    def location(self, tmp_path):
        return FileSystemLocation(base_dir=tmp_path.absolute(), relative_path=Path(""))

    def test_name(self, location):
        assert location.name != ""
        assert location.name == location.base_dir.name


# noinspection PyMethodMayBeStatic
class GenericFileLocationTests:
    def test_base_dir(self, location):
        assert location.base_dir == location.absolute().parent

    def test_exists(self, location):
        assert location.exists()

    def test_name(self, location):
        assert location.name == "file1.txt"

    def test_suffix(self, location):
        assert location.suffix == ".txt"

    def test_absolute(self, location):
        assert location.absolute().is_absolute()
        assert location.absolute() == location.base_dir / "file1.txt"

    def test_match(self, location):
        assert location.match("*")
        assert location.match("file1.txt")
        assert location.match("file*")
        assert not location.match("file1")

    def test_mkdir_fails(self, location):
        with pytest.raises(FileExistsError):
            location.mkdir()

    def test_parent(self, location):
        assert location.parent == location.update(relative_path="")

    def test_parts(self, location):
        assert location.parts == location.absolute().parts

    def test_relative_parts(self, location):
        assert location.relative_parts == ("file1.txt",)

    def test_with_name(self, location):
        expected = location.update(
            relative_path=location.relative_path.with_name("new_foo"),
        )
        assert location.with_name("new_foo") == expected

    def test_with_name_raises_if_name_is_empty(self, location):
        with pytest.raises(ValueError):
            location.with_name("")

    def test_with_suffix(self, location):
        expected = location.update(
            relative_path=location.relative_path.with_suffix(".md"),
        )
        assert location.with_suffix(".md") == expected

    def test_with_suffix_for_empty_suffix(self, location):
        assert location.with_suffix("") == location.update(relative_path="file1")

    def test_is_file(self, location):
        assert location.is_file()

    def test_is_not_dir(self, location):
        assert not location.is_dir()

    def test_open(self, location):
        with location.open("r") as f:
            assert f.read() == "Content of file1"

    def test_open_binary(self, location):
        with location.open("rb") as f:
            assert f.read() == b"Content of file1"

    def test_read_bytes(self, location):
        assert location.read_bytes() == b"Content of file1"

    def test_read_text(self, location):
        assert location.read_text() == "Content of file1"

    def test_read_text_with_encoding(self, location):
        assert location.read_text(encoding="utf-8") == "Content of file1"

    def test_write_bytes(self, location):
        location.write_bytes(b"New content")
        assert location.read_bytes() == b"New content"

    def test_write_text(self, location):
        location.write_text("New content")
        assert location.read_text() == "New content"

    def test_write_text_with_encoding(self, location):
        location.write_text("New content: ÄÖÜ äöü ß", encoding="utf-8")
        assert location.read_text(encoding="utf-8") == "New content: ÄÖÜ äöü ß"

    def test_write_text_to_existing_file(self, location):
        location.write_text("New content")
        location.write_text("Newer content")
        assert location.read_text() == "Newer content"


class TestFileSystemFileLocation(GenericFileLocationTests):
    @pytest.fixture
    def location(self, tmp_path):
        foo = tmp_path / "file1.txt"
        foo.write_text("Content of file1")
        return FileSystemLocation(tmp_path.absolute(), Path("file1.txt"))


# noinspection PyMethodMayBeStatic
class GenericDirLocationTests:
    def test_name(self, location):
        assert location.name == "dir1"

    def test_suffix(self, location):
        assert location.suffix == ""

    def test_base_dir(self, location):
        assert location.base_dir == location.absolute().parent

    def test_exists(self, location):
        assert location.exists()

    def test_absolute(self, location):
        assert location.absolute().is_absolute()
        assert location.absolute() == location.base_dir / "dir1"

    def test_match(self, location):
        assert location.match("*")
        assert location.match("dir1")
        assert location.match("di*")
        assert not location.match("foo")

    def test_mkdir_fails(self, location):
        with pytest.raises(FileExistsError):
            location.mkdir()

    def test_mkdir_exist_ok(self, location):
        # noinspection PyBroadException
        try:
            location.mkdir(exist_ok=True)
        except Exception:
            pytest.fail("mkdir() raised an exception unexpectedly!")

    def test_as_posix(self, location):
        assert location.as_posix() == location.absolute().as_posix()

    def test_parent(self, location):
        assert location.parent == location.update(relative_path="")

    def test_parts(self, location):
        assert location.parts == location.absolute().parts

    def test_relative_parts(self, location):
        assert location.relative_parts == ("dir1",)

    def test_joinpath(self, location):
        assert location.joinpath("bar") == location.update(relative_path="dir1/bar")

    def test_truediv(self, location):
        assert location / "bar" == location.joinpath("bar")

    def test_with_name(self, location):
        expected = FileSystemLocation(
            location.base_dir, location.relative_path.with_name("new_foo")
        )
        assert location.with_name("new_foo") == location.update(
            relative_path=location.relative_path.with_name("new_foo")
        )

    def test_with_name_empty(self, location):
        with pytest.raises(ValueError):
            location.with_name("")

    def test_with_suffix(self, location):
        expected = location.update(
            relative_path=location.relative_path.with_suffix(".bak")
        )
        assert location.with_suffix(".bak") == expected

    def test_is_dir(self, location):
        assert location.is_dir()

    def test_is_not_file(self, location):
        assert not location.is_file()

    def test_open(self, location):
        # noinspection PyTypeChecker
        with pytest.raises((PermissionError, IsADirectoryError)):
            location.open("r")

    def test_read_bytes(self, location):
        # noinspection PyTypeChecker
        with pytest.raises((PermissionError, IsADirectoryError)):
            location.read_bytes()

    def test_read_text(self, location):
        # noinspection PyTypeChecker
        with pytest.raises((PermissionError, IsADirectoryError)):
            location.read_text()


class TestFileSystemDirLocation(GenericDirLocationTests):
    @pytest.fixture
    def location(self, tmp_path):
        foo = tmp_path / "dir1"
        foo.mkdir()
        return FileSystemLocation(tmp_path.absolute(), Path("dir1"))


def _populate_dir(src):
    src.mkdir(exist_ok=True)
    (src / "file1.txt").write_text("Content of file1")
    (src / "file2.txt").write_text("Content of file2")
    (src / "dir1").mkdir()
    (src / "dir1" / "file3.txt").write_text("Content of file3")
    (src / "dir1" / "dir2").mkdir()
    (src / "dir1" / "dir2" / "file4.txt").write_text("Content of file4")


def _assert_dir_contents_is_correct(dst_loc):
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


class TestIterdirAndCopytree:
    @pytest.fixture
    def in_memory_dirs(self):
        src_loc = InMemoryLocation(
            PurePosixPath("/src"), PurePosixPath(""), InMemoryFilesystem({})
        )
        _populate_dir(src_loc)
        dst_loc = InMemoryLocation(
            PurePosixPath("/dst"), PurePosixPath(""), InMemoryFilesystem({})
        )
        return src_loc, dst_loc

    @pytest.fixture
    def fs_dirs(self, tmp_path):
        src = tmp_path / "src"
        _populate_dir(src)
        src_loc = FileSystemLocation(src.absolute(), Path(""))
        dst_loc = FileSystemLocation((tmp_path / "dst").absolute(), Path(""))
        return src_loc, dst_loc

    def test_fs_iterdir(self, fs_dirs):
        src_loc, _ = fs_dirs
        expected = {src_loc / "file1.txt", src_loc / "file2.txt", src_loc / "dir1"}
        result = set(src_loc.iterdir())
        assert result == expected

    def test_in_memory_iterdir(self, in_memory_dirs):
        src_loc, _ = in_memory_dirs
        expected = {src_loc / "file1.txt", src_loc / "file2.txt", src_loc / "dir1"}
        result = set(src_loc.iterdir())
        assert result == expected

    def test_fs_copytree(self, fs_dirs):
        src_loc, dst_loc = fs_dirs

        src_loc.copytree(dst_loc)
        _assert_dir_contents_is_correct(dst_loc)

    def test_fs_to_in_memory_copytree(self, fs_dirs):
        src_loc, _ = fs_dirs
        dst_loc = InMemoryLocation(
            PurePosixPath("/dst"), PurePosixPath(""), InMemoryFilesystem({})
        )
        src_loc.copytree(dst_loc)
        _assert_dir_contents_is_correct(dst_loc)

    def test_in_memory_copytree(self, in_memory_dirs):
        src_loc, dst_loc = in_memory_dirs

        src_loc.copytree(dst_loc)
        _assert_dir_contents_is_correct(dst_loc)


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


class TestInMemoryBaseDirLocation(GenericBaseDirLocationTests):
    @pytest.fixture
    def location(self, in_memory_fs):
        return InMemoryLocation(
            PurePosixPath("/base_dir"), PurePosixPath(""), in_memory_fs
        )

    def test_name(self, location):
        result = location.name
        assert result == "base_dir"


class TestInMemoryFileLocation(GenericFileLocationTests):
    @pytest.fixture
    def location(self, in_memory_fs):
        return InMemoryLocation(
            PurePosixPath("/base_dir"), Path("file1.txt"), in_memory_fs
        )


class TestInMemoryDirLocation(GenericDirLocationTests):
    @pytest.fixture
    def location(self, in_memory_fs):
        return InMemoryLocation(PurePosixPath("/base_dir"), Path("dir1"), in_memory_fs)


@pytest.fixture
def in_memory_non_empty_base_dir_location(in_memory_fs):
    return InMemoryLocation(PurePosixPath("/base_dir"), Path(""), in_memory_fs)


@pytest.fixture
def in_memory_non_empty_dir_location(in_memory_fs):
    return InMemoryLocation(PurePosixPath("/base_dir"), Path("dir1"), in_memory_fs)


class TestInMemoryNonEmptyDirLocation:
    def test_iterdir(self, in_memory_non_empty_dir_location):
        expected = {
            in_memory_non_empty_dir_location / "file3.txt",
            in_memory_non_empty_dir_location / "file4.txt",
            in_memory_non_empty_dir_location / "dir2",
        }
        result = set(in_memory_non_empty_dir_location.iterdir())

        assert result == expected

    def test_read_text(
        self,
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
