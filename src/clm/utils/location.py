from abc import ABC, abstractmethod
from importlib.abc import Traversable
from io import StringIO, BytesIO
from pathlib import Path, PurePath, PurePosixPath
from typing import IO, Any, Iterator, Mapping

from attr import frozen, field

from clm.utils.frozenmap import FrozenMap
from clm.utils.path_utils import PathOrStr


# noinspection PyUnresolvedReferences
@frozen
class Location(Traversable, ABC):
    @property
    @abstractmethod
    def base_dir(self) -> Path:
        """The base directory of the course."""
        ...

    """A location relative to a course directory."""
    relative_path: PurePath = field(
        converter=PurePath, validator=lambda _, __, val: not val.is_absolute()
    )
    """The relative path from the base directory to the location."""

    @property
    def name(self) -> str:
        return self.relative_path.name or self.base_dir.name

    def absolute(self):
        return self.base_dir / self.relative_path


@frozen(init=False)
class FileSystemLocation(Location):
    """A location in the file system."""

    base_dir: Path = field(
        converter=Path,
        validator=lambda _, __, val: val.is_absolute() and val.is_dir(),
    )

    def __init__(self, base_dir: PathOrStr, relative_path: PathOrStr) -> None:
        # noinspection PyUnresolvedReferences
        self.__attrs_init__(relative_path, base_dir)

    def is_dir(self) -> bool:
        return self.absolute().is_dir()

    def is_file(self) -> bool:
        return self.absolute().is_file()

    # noinspection PyArgumentList
    def joinpath(self, child: PathOrStr) -> Traversable:
        return type(self)(self.base_dir, self.relative_path / child)

    def __truediv__(self, child: PathOrStr) -> Traversable:
        return self.joinpath(child)

    def open(
        self,
        mode: str = "r",
        buffering: int = 1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> IO[Any]:
        return self.absolute().open(mode, buffering, encoding, errors, newline)

    def read_bytes(self) -> bytes:
        return self.absolute().read_bytes()

    def read_text(self, encoding: str | None = None) -> str:
        return self.absolute().read_text(encoding)

    # noinspection PyArgumentList
    def iterdir(self) -> Iterator[Traversable]:
        cls: type[Traversable] = type(self)
        absolute = self.absolute()
        return (
            cls(self.base_dir, self.relative_path / child.relative_to(absolute))
            for child in absolute.iterdir()
        )


def str_or_frozen_map(
    value: str | Mapping[str, "InMemoryLocation"]
) -> str | FrozenMap[str, "InMemoryLocation"]:
    if isinstance(value, str):
        return value
    return FrozenMap(value)


@frozen(init=False)
class InMemoryLocation(Location):
    """A location in memory."""

    base_dir: PurePosixPath = field(
        converter=PurePosixPath,
        validator=lambda _, __, val: val.is_absolute(),
    )

    _content: str | FrozenMap[str, str | FrozenMap] = field(
        converter=str_or_frozen_map, default=""
    )

    def __init__(
        self,
        base_dir: PathOrStr,
        relative_path: PathOrStr,
        content: str | Mapping[str, str | FrozenMap] = "",
    ) -> None:
        # noinspection PyUnresolvedReferences
        self.__attrs_init__(relative_path, base_dir, content)

    def __getitem__(self, item):
        if isinstance(self._content, str):
            raise PermissionError(f"Cannot index file {self.name}.")
        new_content = self._content.get(item)
        if new_content is None:
            raise FileNotFoundError(f"Item {item} not found in directory {self.name}.")
        return type(self)(self.base_dir, self.relative_path / item, new_content)

    def is_dir(self) -> bool:
        return not self.is_file()

    def is_file(self) -> bool:
        return isinstance(self._content, str)

    def joinpath(self, child: PathOrStr) -> Traversable:
        if isinstance(self._content, str):
            raise PermissionError(f"Cannot join path {child} to file {self.name}.")
        subdirs = PurePath(child).parts
        new_content = self._content
        try:
            for subdir in subdirs:
                new_content = new_content[subdir]
        except KeyError:
            raise FileNotFoundError(f"Item {child} not found in directory {self.name}.")
        return type(self)(self.base_dir, self.relative_path / child, new_content)

    def open(
        self,
        mode: str = "r",
        buffering: int = 1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> IO[Any]:
        if isinstance(self._content, str):
            if "b" not in mode:
                return StringIO(self._content)
            return BytesIO(self._content.encode(encoding or "utf-8"))
        raise PermissionError(f"Cannot open directory {self.name}.")

    def read_bytes(self) -> bytes:
        if isinstance(self._content, str):
            return self._content.encode("utf-8")
        raise PermissionError(f"Cannot read directory {self.name}.")

    def read_text(self, encoding: str | None = None) -> str:
        if isinstance(self._content, str):
            return self._content
        raise PermissionError(f"Cannot read directory {self.name}.")

    def iterdir(self) -> Iterator[Traversable]:
        if isinstance(self._content, str):
            raise PermissionError(f"Cannot iterate over file {self.name}.")
        return (
            type(self)(self.base_dir, self.relative_path / child, content)
            for child, content in self._content.items()
        )
