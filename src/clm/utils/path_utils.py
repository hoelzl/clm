import os.path
import zipfile
from collections import Counter
from os import PathLike
from pathlib import Path, PurePath
from typing import Iterable, TypeAlias

PathOrStr: TypeAlias = PathLike | str | bytes


_PARENS_TO_REPLACE = "{}[]"
_REPLACEMENT_PARENS = "()" * (len(_PARENS_TO_REPLACE) // 2)
_CHARS_TO_REPLACE = "/\\$#%&<>*+=^€|"
_REPLACEMENT_CHARS = "_" * len(_CHARS_TO_REPLACE)
_CHARS_TO_DELETE = ";!?\"'`.:"
_STRING_TRANSLATION_TABLE = str.maketrans(
    _PARENS_TO_REPLACE + _CHARS_TO_REPLACE,
    _REPLACEMENT_PARENS + _REPLACEMENT_CHARS,
    _CHARS_TO_DELETE,
)


def sanitize_file_name(text: str):
    sanitized_text = text.strip().translate(_STRING_TRANSLATION_TABLE)
    return sanitized_text


def ensure_absolute_path(path: PurePath, base_dir: PurePath) -> PurePath:
    if not path.is_absolute():
        return base_dir / path
    assert base_dir == path or base_dir in path.parents
    return path


def ensure_relative_path(path: PurePath, base_dir: PurePath) -> PurePath:
    if path.is_absolute():
        return path.relative_to(base_dir)
    assert base_dir == path or base_dir in path.parents
    return path


# noinspection GrazieInspection
def base_path_for_csv_file(csv_file: PurePath):
    """Return a path that is suitable for relative paths in a CSV file.

    This is pretty hacky. We simply assume that we can always use the
    grandparent directory of the CSV file, i.e., that the CSV file is
    contained in a single subdirectory.

    We should probably compute the common prefix and then insert `..`
    until we reach this prefix or something along these lines.
    """
    return csv_file.parents[1]


# noinspection PyPep8Naming
def common_prefix(paths: Iterable[PurePath]):
    """Compute the common prefix of all paths.

    >>> from pathlib import PurePosixPath as PP, PureWindowsPath as WP
    >>> common_prefix([PP("/a/b/c/d"), PP("/a/b/d/e"), PP("/a/b/c")])
    PurePosixPath('/a/b')
    >>> common_prefix([PP("/a/b/c/d"), PP("/b/d/e"), PP("/a/b/c")])
    PurePosixPath('/')
    >>> common_prefix([WP("C:/a/b"), WP("C:/a/d")])
    PureWindowsPath('C:/a')
    >>> common_prefix([WP("C:/a/b"), WP("D:/a/b")])
    Traceback (most recent call last):
    ...
    ValueError: Paths have no common prefix.
    >>> common_prefix([])
    Traceback (most recent call last):
    ...
    ValueError: Cannot find common prefix if no paths are given.
    """
    path_counter, num_paths = _count_subpath_occurrences(paths)
    result_path = _find_longest_path_with_max_count(path_counter, num_paths)
    return result_path


def _count_subpath_occurrences(paths):
    path_counter = Counter()
    num_paths = 0
    for num_paths, path in enumerate(paths, 1):
        path_counter.update([path])
        path_counter.update(path.parents)
    return path_counter, num_paths


def _find_longest_path_with_max_count(path_counter, num_paths):
    if num_paths == 0:
        raise ValueError("Cannot find common prefix if no paths are given.")
    result_path, result_len = None, -1
    for path, num_occurrences in path_counter.items():
        if num_occurrences < num_paths:
            continue
        path_len = len(path.as_posix())
        if path_len > result_len:
            result_path, result_len = path, path_len
        elif path_len == result_len and path != result_path:
            raise ValueError(f"No unique prefix: {result_path}, {path}.")
    if result_path is None:
        raise ValueError("Paths have no common prefix.")
    return result_path


def zip_directory(dir_path: PurePath, subdir=None, archive_name=None):
    if archive_name is None:
        dir_name = dir_path.name
        archive_name = dir_name
        if subdir:
            archive_name += "_" + subdir.replace("\\", "_").replace("/", "_").rstrip(
                "_"
            )
        archive_name += ".zip"
    else:
        dir_name = os.path.splitext(archive_name)[0]

    archive_dir = PurePath(dir_name)
    base_dir = dir_path / subdir

    with zipfile.ZipFile(
        dir_path.parent / archive_name,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as zip_:
        for path, dirs, file_names in os.walk(base_dir):
            dirs.sort()  # deterministic order
            path = PurePath(path)
            archive_relpath = archive_dir / ensure_relative_path(path, base_dir)
            for file_name in sorted(file_names):
                zip_.write(path / file_name, str(archive_relpath / file_name))


def is_folder_to_copy(path: PathOrStr, check_for_dir=True) -> bool:
    """Return whether `path` should be treated as a folder to be copied.

    If `check_for_dir` is true, checks that `path` is actually a folder on the
    file system. Disabling this is mostly provided for simpler testing.

    >>> is_folder_to_copy("/usr/slides/examples/foo/", check_for_dir=False)
    True
    >>> is_folder_to_copy("examples/my-example/", check_for_dir=False)
    True
    >>> is_folder_to_copy("/usr/slides/my_slide.py")
    False
    >>> is_folder_to_copy("/usr/slides/examples")
    False
    >>> is_folder_to_copy("/usr/slides/examples/")
    False
    """
    path = Path(path)
    has_correct_path = path.parent.name in FOLDER_DIRS
    if check_for_dir:
        return has_correct_path and path.is_dir()
    else:
        return has_correct_path


FOLDER_DIRS = ["examples", "code"]


def is_contained_in_folder_to_copy(path: PathOrStr, check_for_dir=True) -> bool:
    path = Path(path)
    for p in path.parents:
        if is_folder_to_copy(p, check_for_dir=check_for_dir):
            return True
    return False
