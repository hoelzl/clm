from pathlib import Path
from typing import Callable, NamedTuple, Sequence

from clm.core.directory_role import DirectoryRole, GeneralDirectory

PathToDirectoryRoleFun = Callable[[Path], DirectoryRole | None]


class ExactPathToDirectoryRoleFun(NamedTuple):
    role: DirectoryRole
    paths: list[Path]

    def __call__(self, path: Path) -> DirectoryRole | None:
        for target_path in self.paths:
            if target_path == path:
                return self.role
        return None


class SubpathToDirectoryRoleFun(NamedTuple):
    role: DirectoryRole
    subpaths: list[Path]

    def __call__(self, path: Path) -> DirectoryRole | None:
        for target_subpath in self.subpaths:
            if target_subpath == path or target_subpath in path.parents:
                return self.role
        return None


class PredicateToDirectoryRoleFun(NamedTuple):
    role: DirectoryRole
    predicate: Callable[[Path], bool]

    def __call__(self, path: Path) -> DirectoryRole | None:
        if self.predicate(path):
            return self.role
        return None


class DocumentClassifier:
    def __init__(
        self,
        default_role: DirectoryRole = GeneralDirectory(),
        path_to_dir_role_funs: Sequence[PathToDirectoryRoleFun] | None = None,
    ):
        if path_to_dir_role_funs is None:
            path_to_dir_role_funs = []
        self.default_role = default_role
        self.path_to_dir_role_funs = path_to_dir_role_funs

    def classify(self, path: Path) -> str | None:
        """Classify a path as belonging to this directory role.

        Args:
            path: The path to classify.

        Returns:
            The document type of the path, or None if the path does not
            represent a document to be included in a course spec.
        """
        containing_dir = path.parent
        for path_to_dir_role_fun in self.path_to_dir_role_funs:
            role = path_to_dir_role_fun(containing_dir)
            if role is not None:
                return role.classify(path)
        return self.default_role.classify(path)