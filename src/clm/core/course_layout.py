import re
from pathlib import Path
from typing import Sequence, Callable

from attr import define

from clm.core.directory_kind import DirectoryKind, GeneralDirectory

SKIP_DIRS = (
    "__pycache__",
    ".git",
    ".ipynb_checkpoints",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    ".venv",
    ".vs",
    ".vscode",
    ".idea",
    "build",
    "dist",
    ".cargo",
    ".idea",
    ".vscode",
    "target",
    "out",
)

KEPT_FILES = ["__init__.py", "__main__.py"]
IGNORE_FILE_REGEX = re.compile(r"^[_.](.*)(\.*)?")
IGNORE_PATH_REGEX = re.compile(r"(.*\.egg-info.*|.*cmake-build-.*)")


class CourseLayout:
    def __init__(
        self,
        name: str,
        base_path: Path,
        directory_patterns: Sequence[tuple[str, type[DirectoryKind]]],
        kept_files: Sequence[str] | None = None,
        ignored_files: Sequence[str] = (".gitignore",),
        ignored_files_regex: re.Pattern = IGNORE_FILE_REGEX,
        ignored_directories: Sequence[str] = SKIP_DIRS,
        ignored_directories_regex: re.Pattern = IGNORE_PATH_REGEX,
        default_directory_kind: DirectoryKind = GeneralDirectory(),
    ):
        if kept_files is None:
            kept_files = KEPT_FILES
        self.name = name
        self.base_path = base_path
        self.directory_patterns = directory_patterns
        self.kept_files = kept_files
        self.ignored_files = ignored_files
        self.ignored_files_regex = ignored_files_regex
        self.ignored_directories = ignored_directories
        self.ignored_directories_regex = ignored_directories_regex
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


course_layout_registry: dict[str, Callable[[Path], CourseLayout]] = {}


def get_course_layout(name: str, base_dir: Path) -> CourseLayout:
    factory_fun = course_layout_registry.get(name)
    if factory_fun is None:
        raise ValueError(f"Unknown course layout: {name}")
    return factory_fun(base_dir)
