import re
from pathlib import Path
from typing import Callable, Mapping, Any

from attr import frozen, define
from cattrs import Converter
from cattrs.gen import make_dict_unstructure_fn, override, make_dict_structure_fn

from clm.core.directory_kind import (
    DirectoryKind,
    GeneralDirectory,
    directory_kind_registry,
)

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

KEPT_FILES = ("__init__.py", "__main__.py")
IGNORE_FILE_REGEX = re.compile(r"^[_.](.*)(\.*)?")
IGNORE_PATH_REGEX = re.compile(r"(.*\.egg-info.*|.*cmake-build-.*)")


@frozen
class CourseLayout:
    name: str
    directory_patterns: tuple[tuple[str, type[DirectoryKind]], ...]
    kept_files: tuple[str, ...] = KEPT_FILES
    ignored_files: tuple[str, ...] = (".gitignore",)
    ignored_files_regex: re.Pattern = IGNORE_FILE_REGEX
    ignored_directories: tuple[str, ...] = SKIP_DIRS
    ignored_directories_regex: re.Pattern = IGNORE_PATH_REGEX
    default_directory_kind: DirectoryKind = GeneralDirectory()


@define
class PathClassifier:
    """Performs classification for a course with a particular layout."""

    layout: CourseLayout
    _resolved_directory_paths: dict[Path, DirectoryKind] = {}

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
        for pattern, directory_kind in self.layout.directory_patterns:
            if containing_dir.match(pattern):
                return directory_kind()
        return self.layout.default_directory_kind


course_layout_registry: dict[str, CourseLayout] = {}


def get_course_layout(name: str) -> CourseLayout:
    layout = course_layout_registry.get(name)
    if layout is None:
        raise ValueError(f"Unknown course layout: {name}")
    return layout


def convert_str_to_directory_kind(name: str, _: Any) -> DirectoryKind:
    factory_fun = directory_kind_registry.get(name)
    if factory_fun is None:
        raise ValueError(f"Unknown directory kind: {name}")
    return factory_fun()


course_layout_converter = Converter()

course_layout_converter.register_unstructure_hook(re.Pattern, lambda x: x.pattern)
course_layout_converter.register_unstructure_hook(
    DirectoryKind, lambda x: type(x).__name__
)
course_layout_converter.register_unstructure_hook(
    CourseLayout,
    make_dict_unstructure_fn(
        CourseLayout,
        course_layout_converter,
        directory_patterns=override(
            unstruct_hook=lambda patterns: [[x[0], x[1].__name__] for x in patterns]
        ),
    ),
)


course_layout_converter.register_structure_hook(
    re.Pattern, lambda pattern_str, _: re.compile(pattern_str)
)
course_layout_converter.register_structure_hook(
    DirectoryKind, convert_str_to_directory_kind
)
course_layout_converter.register_structure_hook(list, lambda lst, _: tuple(lst))
course_layout_converter.register_structure_hook(
    CourseLayout,
    make_dict_structure_fn(
        CourseLayout,
        course_layout_converter,
        directory_patterns=override(
            struct_hook=lambda patterns, _: tuple(
                (x[0], directory_kind_registry[x[1]]) for x in patterns
            )
        ),
    ),
)


def course_layout_to_dict(course_layout: CourseLayout) -> dict:
    return course_layout_converter.unstructure(course_layout)


def course_layout_from_dict(data: Mapping) -> CourseLayout:
    return course_layout_converter.structure(data, CourseLayout)
