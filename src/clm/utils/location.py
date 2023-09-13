import fnmatch
import functools
import re

from abc import ABC, abstractmethod
from importlib.abc import Traversable
from pathlib import Path, PurePath
from typing import IO, Any, Iterator, Callable

from attr import frozen, field, define

from clm.utils.in_memory_filesystem import (
    InMemoryFilesystem,
    convert_to_in_memory_filesystem,
)
from clm.utils.path_utils import PathOrStr, as_pure_path


@frozen
class Location(Traversable, ABC):
    """A location relative to a course directory."""

    relative_path: PurePath = field(
        converter=as_pure_path, validator=lambda _, __, val: not val.is_absolute()
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

    @property
    def suffix(self) -> str:
        if self.relative_path:
            return self.relative_path.suffix
        else:
            return self.base_dir.suffix

    def absolute(self) -> Path:
        return self.base_dir / self.relative_path

    def match(self, pattern: str) -> bool:
        return self.absolute().match(pattern)

    def as_posix(self) -> str:
        return self.absolute().as_posix()

    @property
    def parent(self) -> "Location":
        if not self.relative_path.name:
            return self.update(base_dir=self.base_dir.parent)
        return self.update(relative_path=self.relative_path.parent)

    @property
    def parents(self) -> Iterator["Location"]:
        loc = self
        while loc.relative_path.name:
            loc = loc.parent
            yield loc
        while not loc.relative_path.name and loc.base_dir.name:
            loc = loc.parent
            yield loc

    @property
    def parts(self) -> tuple[str, ...]:
        return self.absolute().parts

    @property
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

    def copytree(
        self,
        target_loc: "Location",
        ignore: Callable[[Any, list[str]], set[str]] | None = None,
    ) -> None:
        """Copy the directory tree rooted at this location to the target location."""
        # Note that the implementation does some more conversions between locations
        # and names that seems necessary. This is so that we can keep the interface
        # of ignore the same as for shutil.copytree.
        if self.is_dir():
            all_child_names = list(loc.name for loc in self.iterdir())
            filtered_child_names = (
                all_child_names if ignore is None else ignore(self, all_child_names)
            )
            target_loc.mkdir(parents=True, exist_ok=True)
            for child in (self / loc for loc in filtered_child_names):
                child.copytree(
                    target_loc / child.name,
                    ignore=ignore,
                )
        elif self.is_file():
            target_loc.parent.mkdir(parents=True, exist_ok=True)
            with self.open("rb") as src, target_loc.open("wb") as dst:
                dst.write(src.read())
        else:
            raise FileNotFoundError(
                f"Cannot copy {self.name}: "
                "file does not exist or is not a regular file or directory."
            )

    def glob(self, pattern):
        """Iterate over this subtree and yield all existing files (of any
        kind, including directories) matching the given relative pattern.
        """
        if not pattern:
            raise ValueError("Unacceptable pattern: {!r}".format(pattern))
        pattern_parts = _split_pattern(pattern)
        selector = make_selector(pattern_parts)
        for p in selector.select_from(self):
            yield p

    def rglob(self, pattern):
        """Recursively yield all existing files (of any kind, including
        directories) matching the given relative pattern, anywhere in
        this subtree.
        """
        pattern_parts = _split_pattern(pattern)
        selector = make_selector(("**",) + pattern_parts)
        for p in selector.select_from(self):
            yield p


# Utilities for implementing globbing.
#
# Some of this code is based on the pathlib glob implementation from the Python 3.11
# standard library.


PATTERN_SPLIT_REGEX = re.compile(r"[\\/]")


def _split_pattern(pattern: str) -> tuple[str, ...]:
    """Split a pattern into path components.

    >>> _split_pattern("foo/bar")
    ('foo', 'bar')
    >>> _split_pattern("foo/bar/")
    ('foo', 'bar', '')
    """
    return tuple(re.split(PATTERN_SPLIT_REGEX, pattern))


@functools.lru_cache(maxsize=None)
def make_selector(pattern_parts) -> "Selector | TerminatingSelector":
    pattern = pattern_parts[0]
    child_parts = pattern_parts[1:]
    if not pattern:
        return TerminatingSelector()
    if pattern == "**":
        cls = RecursiveWildcardSelector
    elif "**" in pattern:
        raise ValueError("Invalid pattern: '**' can only be an entire path component")
    elif _is_wildcard_pattern(pattern):
        cls = WildcardSelector
    else:
        cls = PreciseSelector
    return cls(pattern, child_parts)


def _is_wildcard_pattern(pattern):
    """Returns whether pattern is a wildcard pattern.

    Non-wildcard patterns can directly be used to match against path names.

    >>> _is_wildcard_pattern("foo")
    False
    >>> _is_wildcard_pattern("foo*")
    True
    >>> _is_wildcard_pattern("foo[!bar]")
    True
    >>> _is_wildcard_pattern("foo?")
    True
    """
    return bool({"*", "?", "["}.intersection(pattern))


def _compile_pattern(pattern) -> Callable[[str], re.Match[str] | None]:
    return lambda pat: re.compile(fnmatch.translate(pattern)).fullmatch(pat)


class Selector(ABC):
    """A selector matches a specific glob pattern part against the children
    of a given path."""

    def __init__(self, child_parts):
        self.child_parts = child_parts
        if child_parts:
            self.successor = make_selector(child_parts)
            self.dironly = True
        else:
            self.successor = TerminatingSelector()
            self.dironly = False

    def select_from(self, parent_loc: Location):
        """Iterate over all child paths of `parent_path` matched by this
        selector.  This can contain parent_path itself."""

        if not parent_loc.is_dir():
            return iter([])
        return self.yield_selections(parent_loc)

    @abstractmethod
    def yield_selections(self, parent_loc: Location):
        ...


class TerminatingSelector:
    def __init__(self):
        self.dironly = False

    @staticmethod
    def yield_selections(parent_path):
        yield parent_path


class PreciseSelector(Selector):
    def __init__(self, name, child_parts):
        self.name = name
        Selector.__init__(self, child_parts)

    def yield_selections(self, parent_path: Location):
        try:
            path = parent_path / self.name
            if path.is_dir() if self.dironly else path.exists():
                for p in self.successor.yield_selections(path):
                    yield p
        except PermissionError:
            return


class WildcardSelector(Selector):
    def __init__(self, pattern, child_parts):
        super().__init__(child_parts)
        self.match = _compile_pattern(pattern)

    def yield_selections(self, parent_loc: Location):
        try:
            for entry in parent_loc.iterdir():
                if self.dironly:
                    if not entry.is_dir():
                        continue
                name = entry.name
                if self.match(name):
                    loc = parent_loc / name
                    for p in self.successor.yield_selections(loc):
                        yield p
        except PermissionError:
            return


class RecursiveWildcardSelector(Selector):
    def __init__(self, _pattern, child_parts):
        Selector.__init__(self, child_parts)

    def _iterate_directories(self, parent_loc: Location):
        yield parent_loc
        try:
            for entry in parent_loc.iterdir():
                if entry.is_dir():
                    loc = parent_loc / entry.name
                    for p in self._iterate_directories(loc):
                        yield p
        except PermissionError:
            return

    def yield_selections(self, parent_path):
        try:
            yielded = set()
            try:
                successor_select = self.successor.yield_selections
                for starting_point in self._iterate_directories(parent_path):
                    for p in successor_select(starting_point):
                        if p not in yielded:
                            yield p
                            yielded.add(p)
            finally:
                yielded.clear()
        except PermissionError:
            return


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

    def __str__(self):
        if not self.relative_path.name:
            return f"{self.base_dir} (file system location)"
        return f"{self.relative_path} (in {self.base_dir})"

    def update(
        self,
        base_dir: Path | str | None = None,
        relative_path: PurePath | str | None = None,
        *args,
        **kwargs,
    ) -> "FileSystemLocation":
        cls = type(self)

        if relative_path is None:
            relative_path = self.relative_path
        elif isinstance(relative_path, str):
            relative_path = self.relative_path.__class__(relative_path)

        if base_dir is None:
            base_dir = self.base_dir
        elif isinstance(base_dir, str):
            base_dir = self.base_dir.__class__(base_dir)

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
        if "b" in mode:
            buffering = -1
        return self.absolute().open(mode, buffering, encoding, errors, newline)

    def read_bytes(self) -> bytes:
        return self.absolute().read_bytes()

    def read_text(self, encoding: str | None = None) -> str:
        return self.absolute().read_text(encoding)

    def write_bytes(self, data: bytes) -> None:
        self.absolute().write_bytes(data)

    def write_text(self, data: str, encoding: str | None = None) -> None:
        self.absolute().write_text(data, encoding)

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

    base_dir: PurePath = field(
        converter=as_pure_path,
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

    def __str__(self):
        if not self.relative_path.name:
            return f"{self.base_dir} (in-memory location)"
        return f"{self.relative_path} (in {self.base_dir}, in-memory)"

    def update(
        self,
        base_dir: PurePath | None = None,
        relative_path: PurePath | None = None,
        file_system: InMemoryFilesystem | None = None,
        *args,
        **kwargs,
    ) -> "InMemoryLocation":
        if relative_path is None:
            relative_path = self.relative_path
        elif isinstance(relative_path, str):
            relative_path = self.relative_path.__class__(relative_path)

        if base_dir is None:
            base_dir = self.base_dir
        elif isinstance(base_dir, str):
            base_dir = self.base_dir.__class__(base_dir)

        if file_system is None:
            file_system = self._file_system

        return self.__class__(
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
        parent = self.parent
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
        return self._file_system.open(
            self.relative_path,
            mode,
            buffering=buffering,
            encoding=encoding,
            errors=errors,
            newline=newline,
        )

    def read_bytes(self) -> bytes:
        if self.is_dir():
            raise PermissionError(f"Cannot read directory {self.name}.")
        return self._file_system[self.relative_path].data

    def read_text(self, encoding: str | None = None) -> str:
        if self.is_dir():
            raise PermissionError(f"Cannot read directory {self.name}.")
        return self._file_system[self.relative_path].text

    def write_bytes(self, data: bytes) -> None:
        if self.is_dir():
            raise PermissionError(f"Cannot write to directory {self.name}.")
        self._file_system.get_or_create_file(self.relative_path).data = data

    def write_text(self, data: str, encoding: str | None = None) -> None:
        if self.is_dir():
            raise PermissionError(f"Cannot write to directory {self.name}.")
        self._file_system.get_or_create_file(self.relative_path).text = data

    def iterdir(self) -> Iterator["InMemoryLocation"]:
        if self.is_dir():
            # noinspection PyArgumentList
            return (
                type(self)(self.base_dir, child, self._file_system)
                for child in self._file_system.iterdir(self.relative_path)
            )
        raise PermissionError(f"Cannot iterate over file {self.name}.")
