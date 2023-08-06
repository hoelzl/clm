import re
from pathlib import Path
from typing import Mapping, Sequence, NamedTuple

from clm.core.directory_role import DirectoryRole, GeneralDirectory


class RoleMatcher(NamedTuple):
    """A regular expression that matches a directory role."""

    pattern: re.Pattern
    role: DirectoryRole


class DocumentClassifier:
    def __init__(
        self,
        default_role: GeneralDirectory,
        paths_with_roles: Mapping[Path, DirectoryRole],
        role_patterns: Sequence[tuple[re.Pattern, DirectoryRole]],
    ):
        self.default_role = default_role
        self.paths_with_roles = paths_with_roles
        self.role_patterns = role_patterns

    def classify(self, path: Path) -> str | None:
        """Classify a path as belonging to this directory role.

        Args:
            path: The path to classify.

        Returns:
            The document type of the path, or None if the path does not represent a
            document to be included in a course spec.
        """
        if path in self.paths_with_roles:
            return self.paths_with_roles[path].classify(path)
        for pattern, role in self.role_patterns:
            if pattern.fullmatch(path.name):
                return role.classify(path)
        return self.default_role.classify(path)
