import logging
import os.path
import zipfile
from collections import Counter
from os import PathLike
from pathlib import PurePath, PurePosixPath
from typing import Iterable, TypeAlias, TYPE_CHECKING

from clm.utils.config import config

if TYPE_CHECKING:
    from clm.utils.location import Location

PathOrStr: TypeAlias = PathLike | str | bytes


_PARENS_TO_REPLACE = config.parens_to_replace
_REPLACEMENT_PARENS = "()" * (len(_PARENS_TO_REPLACE) // 2)
_CHARS_TO_REPLACE = config.chars_to_replace
_REPLACEMENT_CHARS = "_" * len(_CHARS_TO_REPLACE)
_CHARS_TO_DELETE = config.chars_to_delete
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


def zip_directory(dir_loc: "Location", subdir=None, archive_name=None):
    if archive_name is None:
        dir_name = dir_loc.name
        archive_name = dir_name
        if subdir:
            archive_name += "_" + subdir.replace("\\", "_").replace("/", "_").rstrip(
                "_"
            )
        archive_name += ".zip"
    else:
        dir_name = os.path.splitext(archive_name)[0]

    archive_dir = PurePath(dir_name)
    base_dir = (dir_loc / subdir).absolute()
    zip_file_name = (dir_loc.parent / archive_name).as_posix()
    logging.info(f"Zipping {base_dir} to {zip_file_name}.")

    with zipfile.ZipFile(
        zip_file_name,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as zip_:
        logging.debug(f"Created zip file {zip_file_name}. Adding files in {base_dir}.")
        for path, dirs, file_names in os.walk(base_dir):
            logging.debug(f"Walking directory: {path}, {dirs}, {file_names}.")
            dirs.sort()  # deterministic order
            path = PurePath(path)
            archive_relpath = archive_dir / ensure_relative_path(path, base_dir)
            for file_name in sorted(file_names):
                logging.debug(f"Adding {path / file_name} to archive.")
                zip_.write(path / file_name, str(archive_relpath / file_name))


def as_pure_path(path: PathOrStr) -> PurePath:
    if isinstance(path, PurePath):
        return path
    return PurePosixPath(path)
