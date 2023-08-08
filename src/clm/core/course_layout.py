from pathlib import Path
from typing import Sequence

from clm.core.directory_kind import DirectoryKind, GeneralDirectory


class CourseLayout:
    def __init__(
        self,
        base_path: Path,
        directory_patterns: Sequence[tuple[str, type[DirectoryKind]]],
        default_directory_kind: DirectoryKind = GeneralDirectory(),
    ):
        assert base_path.is_absolute()
        self.base_path = base_path
        self.directory_patterns = directory_patterns
        self.default_directory_kind = default_directory_kind
        self._resolved_directory_paths: dict[Path, DirectoryKind] = {}

    def classify(self, path: Path) -> str:
        """Classify a file or directory in this course."""
        containing_dir = path.parent
        directory_kind = self._resolve_directory_kind(containing_dir)
        return directory_kind.label_for(path)

    def _resolve_directory_kind(self, containing_dir: Path) -> DirectoryKind:
        directory_kind = self._resolved_directory_paths.get(containing_dir)
        if directory_kind is None:
            directory_kind = self._find_directory_kind(containing_dir)
            self._resolved_directory_paths[containing_dir] = directory_kind
        return directory_kind

    def _find_directory_kind(self, containing_dir: Path) -> DirectoryKind:
        for pattern, directory_kind in self.directory_patterns:
            if containing_dir.match(pattern):
                return directory_kind()
        return self.default_directory_kind
