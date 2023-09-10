import os
from pathlib import PurePosixPath

import pytest

from clm.utils.in_memory_filesystem import (
    InMemoryFile,
    InMemoryFilesystem,
    convert_to_in_memory_filesystem,
    InMemoryTextIO,
    InMemoryBytesIO,
)


class TestInMemoryBytesIO:
    def test_from_bytes(self):
        file = InMemoryBytesIO._from_bytes(b"abc")
        assert file._data == bytearray(b"abc")
        assert file._mode == "r"
        assert file._name is None
        assert file._closed is False
        assert file._offset == 0

    def test_init_preserves_bytearray(self):
        data = bytearray(b"abc")
        file = InMemoryBytesIO(data)
        assert file._data is data

    def test_read(self):
        file = InMemoryBytesIO._from_bytes(b"abc")
        assert file.read() == b"abc"

    def test_read_with_non_ascii_chars(self):
        file = InMemoryBytesIO._from_bytes(b"\xc3\xa5\xc3\xa4\xc3\xb6")
        assert file.read() == b"\xc3\xa5\xc3\xa4\xc3\xb6"

    def test_read_with_length(self):
        file = InMemoryBytesIO._from_bytes(b"abcdef")
        assert file.read(2) == b"ab"
        assert file.read(3) == b"cde"
        assert file.read(3) == b"f"
        assert file.read(1) == b""

    def test_default_mode(self):
        file = InMemoryBytesIO._from_bytes(b"abc")
        assert file.mode == "r"
        assert file.readable() is True
        assert file.writable() is False

    def test_write_mode(self):
        file = InMemoryBytesIO._from_bytes(b"abc", mode="w")
        assert file.mode == "w"
        assert file.readable() is False
        assert file.writable() is True

    def test_name(self):
        file = InMemoryBytesIO._from_bytes(b"abc", name="file1.txt")
        assert file.name == "file1.txt"

    def test_closed(self):
        file = InMemoryBytesIO._from_bytes(b"abc")
        assert file.closed is False
        file.close()
        assert file.closed is True

    def test_fileno(self):
        file = InMemoryBytesIO._from_bytes(b"abc")
        with pytest.raises(OSError, match="fileno"):
            file.fileno()

    def test_flush(self):
        file = InMemoryBytesIO._from_bytes(b"abc")
        # noinspection PyBroadException
        try:
            file.flush()
        except Exception:
            pytest.fail("flush() unexpectedly raised an exception!")

    def test_isatty(self):
        file = InMemoryBytesIO._from_bytes(b"abc")
        assert file.isatty() is False

    def test_readline(self):
        file = InMemoryBytesIO._from_bytes(b"abc\ndef\nghi")
        assert file.readline() == b"abc\n"
        assert file.readline() == b"def\n"
        assert file.readline() == b"ghi"
        assert file.readline() == b""

    def test_readline_with_limit(self):
        file = InMemoryBytesIO._from_bytes(b"abc\ndef\nghi")
        assert file.readline(2) == b"ab"
        assert file.readline(3) == b"c\n"
        assert file.readline(3) == b"def"
        assert file.readline(4) == b"\n"
        assert file.readline(4) == b"ghi"
        assert file.readline(4) == b""

    def test_seek(self):
        file = InMemoryBytesIO._from_bytes(b"abcdef")
        file.seek(1)
        file.seek(2)  # default is SEEK_SET
        assert file.read() == b"cdef"

    def test_seek_with_seek_end(self):
        file = InMemoryBytesIO._from_bytes(b"abcdef")
        file.seek(-2, os.SEEK_END)
        assert file.read() == b"ef"

    def test_seekable(self):
        file = InMemoryBytesIO._from_bytes(b"abc")
        assert file.seekable() is True

    def test_seek_with_seek_cur(self):
        file = InMemoryBytesIO._from_bytes(b"abcdef")
        file.seek(1)
        file.seek(2, os.SEEK_CUR)
        assert file.read() == b"def"

    def test_tell(self):
        file = InMemoryBytesIO._from_bytes(b"abc")
        assert file.tell() == 0
        file.read(1)
        assert file.tell() == 1
        file.read()
        assert file.tell() == 3

    def test_truncate_to_shorter_length(self):
        file = InMemoryBytesIO._from_bytes(b"abcdef")
        assert file.truncate(3) == 3
        assert file.read() == b"abc"

    def test_truncate_to_same_length(self):
        file = InMemoryBytesIO._from_bytes(b"abcdef")
        assert file.truncate(6) == 6
        assert file.read() == b"abcdef"

    def test_truncate_to_longer_length(self):
        file = InMemoryBytesIO._from_bytes(b"abcdef")
        assert file.truncate(9) == 9
        assert file.read() == b"abcdef\0\0\0"

    def test_truncate_does_not_change_offset(self):
        file = InMemoryBytesIO._from_bytes(b"abcdef")
        file.seek(2)
        assert file.truncate(5) == 5
        assert file.read() == b"cde"

    def test_truncate_to_offset(self):
        file = InMemoryBytesIO._from_bytes(b"abcdef")
        file.seek(4)
        assert file.truncate() == 4
        file.seek(0)
        assert file.read() == b"abcd"

    def test_writelines_with_append_mode(self):
        file = InMemoryBytesIO._from_bytes(b"abc", mode="a+")
        file.writelines([b"de", b"f\n", b"ghi\n"])
        assert file.read() == b"abcdef\nghi\n"

    def test_writelines_with_write_mode(self):
        file = InMemoryBytesIO._from_bytes(b"abc", mode="w+")
        file.writelines([b"de", b"f\n", b"ghi\n"])
        assert file.read() == b"def\nghi\n"


class TestInMemoryTextIO:
    def test_in_memory_text_io_from_text(self):
        file = InMemoryTextIO._from_text("abc")
        assert file._data == bytearray(b"abc")
        assert file._mode == "r"
        assert file._name is None
        assert file._closed is False
        assert file._offset == 0
        assert file.encoding == "utf-8"
        assert file._errors == "strict"
        assert file.newline == "\n"

    def test_in_memory_text_io_init_preserves_bytearray(self):
        data = bytearray(b"abc")
        file = InMemoryTextIO(data)
        assert file._data is data

    def test_in_memory_text_io_read(self):
        file = InMemoryTextIO._from_text("abc")
        assert file.read() == "abc"

    def test_in_memory_text_io_read_with_non_ascii_chars(self):
        file = InMemoryTextIO._from_bytes(b"\xc3\xa5\xc3\xa4\xc3\xb6")
        assert file.read() == "åäö"

    def test_in_memory_text_io_read_with_length(self):
        file = InMemoryTextIO._from_text("abcdef")
        assert file.read(2) == "ab"
        assert file.read(3) == "cde"
        assert file.read(3) == "f"
        assert file.read(1) == ""

    def test_in_memory_text_io_default_mode(self):
        file = InMemoryTextIO._from_text("abc")
        assert file.mode == "r"
        assert file.readable() is True
        assert file.writable() is False

    def test_in_memory_text_io_write_mode(self):
        file = InMemoryTextIO._from_text("abc", mode="w")
        assert file.mode == "w"
        assert file.readable() is False
        assert file.writable() is True

    def test_in_memory_text_io_name(self):
        file = InMemoryTextIO._from_text("abc", name="file1.txt")
        assert file.name == "file1.txt"

    def test_in_memory_text_io_closed(self):
        file = InMemoryTextIO._from_text("abc")
        assert file.closed is False
        file.close()
        assert file.closed is True

    def test_in_memory_text_io_fileno(self):
        file = InMemoryTextIO._from_text("abc")
        with pytest.raises(OSError, match="fileno"):
            file.fileno()

    def test_in_memory_text_io_flush(self):
        file = InMemoryTextIO._from_text("abc")
        # noinspection PyBroadException
        try:
            file.flush()
        except Exception:
            pytest.fail("flush() unexpectedly raised an exception!")

    def test_in_memory_text_io_isatty(self):
        file = InMemoryTextIO._from_text("abc")
        assert file.isatty() is False

    def test_in_memory_text_io_readline(self):
        file = InMemoryTextIO._from_text("abc\ndef\nghi")
        assert file.readline() == "abc\n"
        assert file.readline() == "def\n"
        assert file.readline() == "ghi"
        assert file.readline() == ""

    def test_in_memory_text_io_readline_with_limit(self):
        file = InMemoryTextIO._from_text("abc\ndef\nghi")
        assert file.readline(2) == "ab"
        assert file.readline(3) == "c\n"
        assert file.readline(3) == "def"
        assert file.readline(4) == "\n"
        assert file.readline(4) == "ghi"
        assert file.readline(4) == ""

    def test_in_memory_text_io_seek(self):
        file = InMemoryTextIO._from_text("abcdef")
        file.seek(1)
        file.seek(2)  # default is SEEK_SET
        assert file.read() == "cdef"

    def test_in_memory_text_io_seek_with_seek_cur(self):
        file = InMemoryTextIO._from_text("abcdef")
        file.seek(1)
        file.seek(2, os.SEEK_CUR)
        assert file.read() == "def"

    def test_in_memory_text_io_seek_with_seek_end(self):
        file = InMemoryTextIO._from_text("abcdef")
        file.seek(-2, os.SEEK_END)
        assert file.read() == "ef"

    def test_in_memory_text_io_seekable(self):
        file = InMemoryTextIO._from_text("abc")
        assert file.seekable() is True

    def test_in_memory_text_io_tell(self):
        file = InMemoryTextIO._from_text("abc")
        assert file.tell() == 0
        file.read(1)
        assert file.tell() == 1
        file.read()
        assert file.tell() == 3

    def test_in_memory_text_io_truncate_to_shorter_length(self):
        file = InMemoryTextIO._from_text("abcdef")
        assert file.truncate(3) == 3
        assert file.read() == "abc"

    def test_in_memory_text_io_truncate_to_same_length(self):
        file = InMemoryTextIO._from_text("abcdef")
        assert file.truncate(6) == 6
        assert file.read() == "abcdef"

    def test_in_memory_text_io_truncate_to_longer_length(self):
        file = InMemoryTextIO._from_text("abcdef")
        assert file.truncate(9) == 9
        assert file.read() == "abcdef\0\0\0"

    def test_in_memory_text_io_truncate_does_not_change_offset(self):
        file = InMemoryTextIO._from_text("abcdef")
        file.seek(2)
        assert file.truncate(5) == 5
        assert file.read() == "cde"

    def test_in_memory_text_io_truncate_to_offset(self):
        file = InMemoryTextIO._from_text("abcdef")
        file.seek(4)
        assert file.truncate() == 4
        file.seek(0)
        assert file.read() == "abcd"

    def test_in_memory_text_io_writelines_with_append_mode(self):
        file = InMemoryTextIO._from_text("abc", mode="a+")
        file.writelines(["de", "f\n", "ghi\n"])
        assert file.read() == "abcdef\nghi\n"

    def test_in_memory_text_io_writelines_with_write_mode(self):
        file = InMemoryTextIO._from_text("abc", mode="w+")
        file.writelines(["de", "f\n", "ghi\n"])
        assert file.read() == "def\nghi\n"

    def test_in_memory_text_io_newline(self):
        file = InMemoryTextIO._from_text("abc")
        assert file.newline == "\n"

    def test_in_memory_text_io_encoding(self):
        file = InMemoryTextIO._from_text("abc", encoding="latin-1")
        assert file.encoding == "latin-1"

    def test_in_memory_text_io_encoding_default(self):
        file = InMemoryTextIO._from_text("abc")
        assert file.encoding == "utf-8"

    def test_in_memory_text_io_errors(self):
        file = InMemoryTextIO._from_text("abc", errors="ignore")
        assert file._errors == "ignore"

    def test_in_memory_text_io_errors_default(self):
        file = InMemoryTextIO._from_text("abc")
        assert file._errors == "strict"


class TestInMemoryFile:
    def test_init(self):
        file = InMemoryFile()
        assert isinstance(file._contents, bytearray)
        assert file.data == b""
        assert file.encoding == "utf-8"

    def test_from_bytes(self):
        file = InMemoryFile.from_bytes(b"abc")
        assert file.data == b"abc"
        assert file.encoding == "utf-8"

    def test_from_text(self):
        file = InMemoryFile.from_text("abc")
        assert file.data == b"abc"
        assert file.encoding == "utf-8"

    def test_from_text_with_non_ascii_chars(self):
        file = InMemoryFile.from_text("åäö")
        assert file.data == b"\xc3\xa5\xc3\xa4\xc3\xb6"
        assert file.encoding == "utf-8"

    def test_data(self):
        file = InMemoryFile.from_text("abc")
        assert file.data == b"abc"
        file.data = b"def"
        assert file.data == b"def"

    def test_text(self):
        file = InMemoryFile.from_text("abc")
        assert file.text == "abc"
        file.text = "def"
        assert file.text == "def"

    def test_open_for_read(self):
        file = InMemoryFile.from_text("abc")
        with file.open() as f:
            assert isinstance(f, InMemoryTextIO)
            assert f.read() == "abc"

    def test_open_for_read_binary(self):
        file = InMemoryFile.from_text("abc")
        with file.open("rb") as f:
            assert isinstance(f, InMemoryBytesIO)
            assert f.read() == b"abc"

    def test_open_for_write(self):
        file = InMemoryFile.from_text("abc")
        with file.open("w") as f:
            assert isinstance(f, InMemoryTextIO)
            f.write("def")
        assert file.text == "def"


def test_convert_to_in_memory_filesystem():
    fs = convert_to_in_memory_filesystem({"file1.txt": "Content of file1", "dir": {}})
    assert isinstance(fs, InMemoryFilesystem)
    assert fs["file1.txt"].text == "Content of file1"
    assert fs["dir"] == {}


@pytest.fixture
def in_memory_fs():
    return convert_to_in_memory_filesystem(
        {
            "file1.txt": "Content of file1",
            "file2.txt": b"Content of file2",
            "dir1": {"file3.txt": "Content of file3"},
        }
    )


class TestInMemoryFilesystem:
    def test_getitem_for_file_1(self, in_memory_fs):
        assert isinstance(in_memory_fs["file1.txt"], InMemoryFile)
        assert in_memory_fs["file1.txt"].text == "Content of file1"
        assert in_memory_fs["file1.txt"] is in_memory_fs[["file1.txt"]]

    def test_getitem_for_file_3(self, in_memory_fs):
        assert isinstance(in_memory_fs["dir1/file3.txt"], InMemoryFile)
        assert in_memory_fs["dir1/file3.txt"].text == "Content of file3"
        assert in_memory_fs["dir1/file3.txt"] is in_memory_fs[["dir1", "file3.txt"]]

    def test_getitem_for_path(self, in_memory_fs):
        assert in_memory_fs[PurePosixPath("file1.txt")].text == "Content of file1"
        assert in_memory_fs[PurePosixPath("dir1/file3.txt")].text == "Content of file3"

    def test_getitem_for_str_with_slash(self, in_memory_fs):
        assert in_memory_fs["dir1/file3.txt"].text == "Content of file3"

    def test_setitem_for_single_file(self, in_memory_fs):
        in_memory_fs["new_file.md"] = InMemoryFile.from_text("New file")
        assert in_memory_fs.exists(PurePosixPath("new_file.md"))
        assert in_memory_fs["new_file.md"].text == "New file"

    def test_setitem_for_directory(self, in_memory_fs):
        in_memory_fs["new_dir/subdir"] = {
            "new_file.md": InMemoryFile.from_text("New file")
        }
        assert in_memory_fs.exists("new_dir")
        assert in_memory_fs.is_dir("new_dir")
        assert in_memory_fs.exists("new_dir/subdir")
        assert in_memory_fs.is_dir("new_dir/subdir")
        assert in_memory_fs.exists("new_dir/subdir/new_file.md")
        assert in_memory_fs.is_file("new_dir/subdir/new_file.md")
        assert in_memory_fs["new_dir/subdir/new_file.md"].text == "New file"

    def test_exists_for_file(self, in_memory_fs):
        assert in_memory_fs.exists("file1.txt")
        assert in_memory_fs.exists("file2.txt")
        assert in_memory_fs.exists("dir1/file3.txt")

    def test_exists_for_directory(self, in_memory_fs):
        assert in_memory_fs.exists("dir1")

    def test_exists_for_non_existing_file(self, in_memory_fs):
        assert not in_memory_fs.exists("file4.txt")

    def test_is_file_for_file(self, in_memory_fs):
        assert in_memory_fs.is_file("file1.txt")
        assert in_memory_fs.is_file("file2.txt")
        assert in_memory_fs.is_file("dir1/file3.txt")

    def test_is_file_for_directory(self, in_memory_fs):
        assert not in_memory_fs.is_file("dir1")

    def test_is_file_for_non_existing_file(self, in_memory_fs):
        assert not in_memory_fs.is_file("file4.txt")

    def test_is_dir_for_file(self, in_memory_fs):
        assert not in_memory_fs.is_dir("file1.txt")
        assert not in_memory_fs.is_dir("file2.txt")
        assert not in_memory_fs.is_dir("dir1/file3.txt")

    def test_is_dir_for_directory(self, in_memory_fs):
        assert in_memory_fs.is_dir("dir1")

    def test_is_dir_for_non_existing_file(self, in_memory_fs):
        assert not in_memory_fs.is_dir("file4.txt")

    def test_open_for_file_read(self, in_memory_fs):
        with in_memory_fs.open("file1.txt") as f:
            assert f.read() == "Content of file1"

    def test_open_for_file_read_binary(self, in_memory_fs):
        with in_memory_fs.open("file1.txt", "rb") as f:
            assert f.read() == b"Content of file1"

    def test_open_for_non_existing_file_read(self, in_memory_fs):
        with pytest.raises(FileNotFoundError):
            in_memory_fs.open("file4.txt")

    def test_open_for_file_write(self, in_memory_fs):
        with in_memory_fs.open("file1.txt", "w") as f:
            f.write("New content of file1")
        assert in_memory_fs["file1.txt"].text == "New content of file1"

    def test_open_for_file_write_binary(self, in_memory_fs):
        with in_memory_fs.open("file1.txt", "wb") as f:
            f.write(b"New content of file1")
        assert in_memory_fs["file1.txt"].text == "New content of file1"

    def test_open_for_non_existing_file_write(self, in_memory_fs):
        with in_memory_fs.open("file4.txt", "w") as f:
            f.write("New content of file4")
        assert isinstance(in_memory_fs["file4.txt"], InMemoryFile)
        assert in_memory_fs["file4.txt"].text == "New content of file4"

    def test_iterdir(self, in_memory_fs):
        assert list(in_memory_fs.iterdir(".")) == [
            PurePosixPath("file1.txt"),
            PurePosixPath("file2.txt"),
            PurePosixPath("dir1"),
        ]
