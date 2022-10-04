# %%
from collections import Counter
from os import PathLike
from pathlib import PurePath
from typing import Iterable, TypeAlias

# %%
PathOrStr: TypeAlias = PathLike | str | bytes


# %%
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


# %%
def _count_subpath_occurrences(paths):
    path_counter = Counter()
    num_paths = 0
    for num_paths, path in enumerate(paths, 1):
        path_counter.update([path])
        path_counter.update(path.parents)
    return path_counter, num_paths


# %%
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
