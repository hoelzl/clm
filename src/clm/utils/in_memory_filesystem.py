import os
from abc import ABC, abstractmethod
from io import IOBase
from pathlib import PurePosixPath, PurePath
from random import randint
from typing import Any, Iterable, Mapping, IO, AnyStr, Callable

from attr import define, field
from cytoolz import get_in

from clm.utils.general import listify
from clm.utils.path_utils import PathOrStr, as_pure_path


def _convert_to_parts(path) -> list[str]:
    if isinstance(path, str):
        path = as_pure_path(path)
    if isinstance(path, PurePath):
        path = path.parts
    return listify(path)


def _update_in_place(d: dict, parts: list[str], new_value):
    for part in parts[:-1]:
        d = d.setdefault(part, {})
    d[parts[-1]] = new_value


class InMemoryIOBase(IOBase, ABC):
    def __init__(self, data: bytearray, mode: str = "r", name: str | None = None):
        self._data = data
        if "w" in mode:
            self._data.clear()
        if "a" in mode:
            self._offset = len(self._data)
        self._closed = False
        self._offset = 0
        self._mode = mode
        self._name = name

    @classmethod
    def _from_bytes(cls, data: bytes | bytearray, *args, **kwargs):
        if isinstance(data, bytes):
            data = bytearray(data)
        return cls(data, *args, **kwargs)

    @classmethod
    def _from_text(cls, text, encoding="utf-8", *args, **kwargs):
        return cls._from_bytes(
            text.encode(encoding), encoding=encoding, *args, **kwargs
        )

    @property
    @abstractmethod
    def _newline(self) -> bytes:
        ...

    @property
    def _remaining_data(self) -> bytes:
        return bytes(self._data[self._offset :])

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def name(self) -> str:
        if self._name is None:
            return "<unnamed in-memory file>"
        return self._name

    def close(self) -> None:
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed

    def fileno(self) -> int:
        raise OSError("In-memory files do not have a fileno.")

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False

    def read(self, n: int = -1) -> AnyStr:
        if not self.readable():
            raise OSError("File not open for reading.")
        result = self._remaining_data
        if n >= 0:
            result = result[:n]
        self._offset += len(result)
        return result

    def readable(self) -> bool:
        return len(set("r+").intersection(self._mode)) > 0

    def readline(self, limit: int = -1) -> AnyStr:
        if not self.readable():
            raise OSError("File not open for reading.")
        data = self._remaining_data
        if limit < 0:
            limit = len(data)
        index = data.find(self._newline, 0, limit)
        if index == -1:
            index = limit
        elif (
            data[index:].startswith(self._newline)
            and index + len(self._newline) < limit
        ):
            index += len(self._newline)
        result = data[:index]
        self._offset += index
        return result

    def readlines(self, hint: int | None = -1) -> list[AnyStr]:
        lines, size = self._readlines_internal(hint)
        self._offset += size
        return lines

    def _readlines_internal(self, hint: int | None = -1) -> tuple[list[AnyStr], int]:
        data = self._remaining_data
        if hint is None:
            hint = -1
        lines = data.split(self._newline)
        if hint > 0:
            result, size_so_far = [], 0
            for line in lines:
                if size_so_far < hint:
                    result.append(line)
                    size_so_far += len(line)
            return result, size_so_far
        return lines, len(data)

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == os.SEEK_SET:
            self._offset = offset
        elif whence == os.SEEK_CUR:
            self._offset += offset
        elif whence == os.SEEK_END:
            self._offset = len(self._data) + offset
        else:
            raise ValueError(f"Invalid whence value {whence}.")
        return self._offset

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._offset

    def truncate(self, size: int = None) -> int:
        data = bytes(self._data)
        if size is None:
            size = self._offset
        self._data[:] = data[:size]
        if size > len(data):
            self._data.extend(b"\x00" * (size - len(data)))
        return size

    def writable(self) -> bool:
        return len(set("awx+").intersection(self._mode)) > 0

    def writelines(self, lines: list[AnyStr]) -> None:
        for line in lines:
            self.write(line)

    def _write_bytes(self, s: bytes) -> int:
        if not self.writable():
            raise OSError("File not open for writing.")
        self._data.extend(s)
        return len(s)


class InMemoryBytesIO(InMemoryIOBase, IO[bytes]):
    @property
    def _newline(self) -> bytes:
        return b"\n"

    def write(self, s: bytes) -> int:
        return self._write_bytes(s)


class InMemoryTextIO(InMemoryIOBase, IO[str]):
    def __init__(
        self,
        data: bytearray,
        mode: str = "r",
        name: str | None = None,
        encoding: str | None = None,
        newline: str | None = None,
        errors: str | None = None,
    ):
        super().__init__(data, mode=mode, name=name)
        if encoding is None:
            encoding = "utf-8"
        if newline is None:
            newline = "\n"
        if errors is None:
            errors = "strict"
        self._errors = errors
        self.encoding = encoding
        self.newline = newline or "\n"

    @property
    def _newline(self) -> bytes:
        return self.newline.encode(self.encoding, self._errors)

    def read(self, n: int = -1) -> str:
        return super().read(n).decode(self.encoding, self._errors)

    def readline(self, limit: int = -1) -> str:
        return super().readline(limit).decode(self.encoding, self._errors)

    def write(self, s: str) -> int:
        return self._write_bytes(s.encode(self.encoding, self._errors))


@define
class InMemoryFile:
    _contents: bytearray = field(factory=bytearray)
    encoding: str = field(default="utf-8")
    name: str | None = field(
        converter=lambda n: "<unnamed file>" if n is None else n, default=None
    )

    @classmethod
    def from_bytes(
        cls, data: bytes | bytearray, encoding: str = "utf-8", name: str | None = None
    ):
        if isinstance(data, bytes):
            data = bytearray(data)
        return cls(data, encoding=encoding, name=name)

    @classmethod
    def from_text(cls, text: str, encoding: str = "utf-8", name: str | None = None):
        return cls.from_bytes(text.encode(encoding), encoding=encoding, name=name)

    @property
    def data(self) -> bytes:
        return bytes(self._contents)

    @data.setter
    def data(self, value: bytes | bytearray):
        self._contents.clear()
        self._contents.extend(value)

    @property
    def text(self) -> str:
        return self.data.decode(self.encoding)

    @text.setter
    def text(self, value: str):
        self.data = value.encode(self.encoding)

    def open(
        self,
        mode: str = "r",
        encoding: str | None = None,
        newline: str | None = None,
        errors: str | None = None,
        name: str | None = None,
        *_args,
        **_kwargs,
    ) -> IO[Any]:
        if encoding is None:
            encoding = self.encoding
        if newline is None:
            newline = "\n"
        if errors is None:
            errors = "strict"
        if name is None:
            name = "<unnamed stream>"
        if "b" in mode:
            return InMemoryBytesIO(self._contents, mode=mode, name=name)
        else:
            return InMemoryTextIO(
                self._contents,
                mode=mode,
                encoding=encoding,
                newline=newline,
                errors=errors,
                name=name,
            )


@define(eq=False, order=False)
class InMemoryFilesystem:
    _hash: int = field(factory=lambda: randint(0, 2**32 - 1), init=False)
    # Actually recursively nested dicts with string keys and multiple possible values.
    contents: dict[str, Any] = {}
    path_factory: Callable[[PathOrStr], PurePath] = as_pure_path

    def __hash__(self):
        return self._hash

    def __eq__(self, other):
        return self._hash == hash(other)

    def __getitem__(self, path: PathOrStr | str | Iterable) -> InMemoryFile | Mapping:
        parts = _convert_to_parts(path)
        return get_in(parts, self.contents)

    def __setitem__(self, path, value):
        _update_in_place(self.contents, _convert_to_parts(path), value)

    def exists(self, path: PathOrStr) -> bool:
        return self[self.path_factory(path)] is not None

    def is_file(self, path: PathOrStr) -> bool:
        return isinstance(self[self.path_factory(path)], InMemoryFile)

    def is_dir(self, path: PathOrStr) -> bool:
        return isinstance(self[self.path_factory(path)], Mapping)

    def open(
        self,
        path: PathOrStr,
        mode: str = "r",
        encoding: str | None = None,
        newline: str | None = None,
        errors: str | None = None,
        *args,
        **kwargs,
    ) -> IO[Any]:
        data = self[path]
        if isinstance(data, InMemoryFile):
            return data.open(
                mode=mode,
                encoding=encoding,
                newline=newline,
                errors=errors,
                name=data.name,
                *args,
                **kwargs,
            )
        raise PermissionError(f"Cannot open directory {path}.")

    def iterdir(self, path: PathOrStr) -> Iterable[PurePosixPath]:
        path = self.path_factory(path)
        return (path / child for child in self[path].keys())


def _convert_data_for_in_memory_filesystem(
    name: str,
    value: Mapping | str | bytes | bytearray | InMemoryFile,
):
    if isinstance(value, Mapping):
        return {
            k: _convert_data_for_in_memory_filesystem(k, v) for k, v in value.items()
        }
    if isinstance(value, str):
        return InMemoryFile.from_text(value, name=name)
    if isinstance(value, (bytes, bytearray)):
        return InMemoryFile.from_bytes(value, name=name)
    if isinstance(value, InMemoryFile):
        return value
    raise TypeError(
        f"Cannot convert value of type {type(value)} to InMemoryFilesystem."
    )


def convert_to_in_memory_filesystem(
    value: Mapping | InMemoryFilesystem,
) -> InMemoryFilesystem:
    if isinstance(value, InMemoryFilesystem):
        return value
    if isinstance(value, Mapping):
        return InMemoryFilesystem(
            {k: _convert_data_for_in_memory_filesystem(k, v) for k, v in value.items()}
        )
    raise TypeError(
        f"Cannot convert value {value!r} of type {type(value)} to InMemoryFilesystem."
    )
