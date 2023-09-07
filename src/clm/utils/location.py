from abc import ABC, abstractmethod
from importlib.abc import Traversable
from pathlib import Path, PurePosixPath
from typing import IO, Any, Iterator

from attr import frozen, field, define

from clm.utils.in_memory_filesystem import (
    InMemoryFilesystem,
    convert_to_in_memory_filesystem,
)
from clm.utils.path_utils import PathOrStr


# noinspection PyUnresolvedReferences
@frozen
class Location(Traversable, ABC):
    """A location relative to a course directory."""

    relative_path: PurePosixPath = field(
        converter=PurePosixPath, validator=lambda _, __, val: not val.is_absolute()
    )
    """The relative path from the base directory to the location."""

    @abstractmethod
    def update(self, *args, **kwargs) -> "Location":
        """Return a clone of the location with possibly updated attributes."""
        ...

    @property
    @abstractmethod
    def base_dir(self) -> Path:
        """The base directory of the course."""
        ...

    @abstractmethod
    def exists(self):
        """Return whether the location exists."""
        ...

    @abstractmethod
    def mkdir(self, parents: bool = False, exist_ok: bool = False) -> None:
        ...

    @property
    def name(self) -> str:
        return self.relative_path.name or self.base_dir.name

    def absolute(self) -> Path:
        return self.base_dir / self.relative_path

    def as_posix(self) -> str:
        return self.absolute().as_posix()

    def parent(self) -> "Location":
        if not self.relative_path.name:
            return self.update(base_dir=self.base_dir.parent)
        return self.update(relative_path=self.relative_path.parent)

    def parts(self) -> tuple[str, ...]:
        return self.absolute().parts

    def relative_parts(self) -> tuple[str, ...]:
        return self.relative_path.parts

    def joinpath(self, child: PathOrStr) -> "Location":
        return self.update(relative_path=self.relative_path / child)

    def __truediv__(self, child: PathOrStr) -> "Location":
        return self.joinpath(child)

    def with_name(self, new_name: str) -> "Location":
        if not self.relative_path.name:
            return self.update(base_dir=self.base_dir.with_name(new_name))
        return self.update(relative_path=self.relative_path.with_name(new_name))

    def with_suffix(self, new_suffix: str) -> "Location":
        if not self.relative_path.name:
            return self.update(base_dir=self.base_dir.with_suffix(new_suffix))
        return self.update(relative_path=self.relative_path.with_suffix(new_suffix))


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

    def update(
        self,
        base_dir: Path | None = None,
        relative_path: PurePosixPath | None = None,
        *args,
        **kwargs,
    ) -> "FileSystemLocation":
        cls = type(self)
        relative_path = self.relative_path if relative_path is None else relative_path
        base_dir = self.base_dir if base_dir is None else base_dir
        return cls(
            base_dir=base_dir,
            relative_path=relative_path,
        )

    def exists(self) -> bool:
        return self.absolute().exists()

    def mkdir(self, parents: bool = False, exist_ok: bool = False) -> None:
        self.absolute().mkdir(parents=parents, exist_ok=exist_ok)

    def is_dir(self) -> bool:
        return self.absolute().is_dir()

    def is_file(self) -> bool:
        return self.absolute().is_file()

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
    def iterdir(self) -> Iterator["FileSystemLocation"]:
        cls: type["FileSystemLocation"] = type(self)
        absolute = self.absolute()
        return (
            cls(self.base_dir, self.relative_path / child.relative_to(absolute))
            for child in absolute.iterdir()
        )


@define(init=False)
class InMemoryLocation(Location):
    """A location in memory."""

    base_dir: PurePosixPath = field(
        converter=PurePosixPath,
        validator=lambda _, __, val: val.is_absolute(),
    )

    _file_system: InMemoryFilesystem = field(converter=convert_to_in_memory_filesystem)

    def __init__(
        self,
        base_dir: PathOrStr,
        relative_path: PathOrStr,
        file_system: InMemoryFilesystem,
    ) -> None:
        """Overridden init method.

        Note that the argument order is different from the order of the fields.
        """

        # noinspection PyUnresolvedReferences
        self.__attrs_init__(relative_path, base_dir, file_system)

    def update(
        self,
        base_dir: PurePosixPath | None = None,
        relative_path: PurePosixPath | None = None,
        file_system: InMemoryFilesystem | None = None,
        *args,
        **kwargs,
    ) -> "InMemoryLocation":
        cls = type(self)
        relative_path = self.relative_path if relative_path is None else relative_path
        base_dir = self.base_dir if base_dir is None else base_dir
        file_system = self._file_system if file_system is None else file_system
        return cls(
            base_dir=base_dir, relative_path=relative_path, file_system=file_system
        )

    def exists(self) -> bool:
        return self._file_system.exists(self.relative_path)

    def mkdir(self, parents: bool = False, exist_ok: bool = False) -> None:
        if self.exists():
            if exist_ok:
                return
            raise FileExistsError(
                f"Cannot create directory {self.name}: file already exists."
            )
        parent = self.parent()
        if not parent.exists() and not parents:
            raise FileNotFoundError(
                f"Cannot create directory {self.name}: parent directory "
                f"{parent.name} does not exist."
            )
        self._file_system[self.relative_path] = {}

    def is_dir(self) -> bool:
        return self._file_system.is_dir(self.relative_path)

    def is_file(self) -> bool:
        return self._file_system.is_file(self.relative_path)

    def open(
        self,
        mode: str = "r",
        buffering: int = 1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> IO[Any]:
        if self.is_file():
            return self._file_system.open(
                self.relative_path,
                mode,
                buffering=buffering,
                encoding=encoding,
                errors=errors,
                newline=newline,
            )
        raise PermissionError(f"Cannot open directory {self.name}.")

    def read_bytes(self) -> bytes:
        if self.is_file():
            return self._file_system[self.relative_path].data
        raise PermissionError(f"Cannot read directory {self.name}.")

    def read_text(self, encoding: str | None = None) -> str:
        if self.is_file():
            return self._file_system[self.relative_path].text
        raise PermissionError(f"Cannot read directory {self.name}.")

    def iterdir(self) -> Iterator["InMemoryLocation"]:
        if self.is_dir():
            # noinspection PyArgumentList
            return (
                type(self)(self.base_dir, child, self._file_system)
                for child in self._file_system.iterdir(self.relative_path)
            )
        raise PermissionError(f"Cannot iterate over file {self.name}.")
