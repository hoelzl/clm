from pathlib import Path
from typing import Callable, NamedTuple, Sequence

from clm.core.directory_role import DirectoryRole, GeneralDirectory
from clm.utils.path_utils import ensure_relative_path

PathToDirectoryRoleFun = Callable[[Path], DirectoryRole | None]


class ExactPathToDirectoryRoleFun(NamedTuple):
    role: DirectoryRole
    """The directory role to assign if the path is equal to a target path."""
    paths: list[Path]
    """The target paths. \
    If the path is equal to any of these, the role is assigned."""
    base_path: Path = Path()
    """The base path of the course."""

    def __call__(self, path: Path) -> DirectoryRole | None:
        path = ensure_relative_path(path, self.base_path)
        for target_path in self.paths:
            if target_path == path:
                return self.role
        return None


class SubpathToDirectoryRoleFun(NamedTuple):
    role: DirectoryRole
    """The directory role to assign if the path is a subpath of a target \
    path."""
    subpaths: list[Path]
    """The target subpaths. \
    If the path is a subpath of any of these, the role is assigned."""
    base_path: Path = Path()
    """The base path of the course."""

    def __call__(self, path: Path) -> DirectoryRole | None:
        path = ensure_relative_path(path, self.base_path)
        for target_subpath in self.subpaths:
            if target_subpath == path or target_subpath in path.parents:
                return self.role
        return None


class PredicateToDirectoryRoleFun(NamedTuple):
    """A function that assigns a directory role if a predicate is true."""

    role: DirectoryRole
    """The directory role to assign if the predicate is true."""
    predicate: Callable[[Path, Path], bool]
    """The predicate. Called with the path to classify and the base path."""
    base_path: Path = Path()
    """The base path of the course."""

    def __call__(self, path: Path) -> DirectoryRole | None:
        if self.predicate(path, self.base_path):
            return self.role
        return None


class DocumentClassifier:
    def __init__(
        self,
        base_path: Path,
        default_role: DirectoryRole = GeneralDirectory(),
        path_to_dir_role_funs: Sequence[PathToDirectoryRoleFun] | None = None,
    ):
        assert base_path.is_absolute()
        # base_path = base_path.absolute()
        if path_to_dir_role_funs is None:
            path_to_dir_role_funs = []
        self.base_path = base_path
        self.default_role = default_role
        self.path_to_dir_role_funs = path_to_dir_role_funs

    def classify(self, path: Path) -> str | None:
        """Classify a path as belonging to this directory role.

        Args:
            path: The path to classify.

        Returns:
            The document type of the path or None, if the document should be
            ignored.
        """
        containing_dir = path.parent
        for path_to_dir_role_fun in self.path_to_dir_role_funs:
            role = path_to_dir_role_fun(containing_dir)
            if role is not None:
                return role.classify(path)
        return self.default_role.classify(path)
