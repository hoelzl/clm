"""
Specs are descriptions of objects that can be edited as text.

A `DocumentSpec` is a description of a single document.
"""
import re
from pathlib import Path
from typing import NamedTuple

from clm.utils.path_utils import PathOrStr, is_folder_to_copy


class DocumentSpec(NamedTuple):
    """A description how to build a document.

    Document specs are the intermediate representation from which we generate the
    actual course.

    The idea is that we auto-generate a file containing document specs that can be
    manually edited to serve as input for the actual course. Therefore, we set the
    relative target dir to `"-"` which means "don't create a document for this
    source". For documents that should be included in the course, this value can then
    be changed to the actual subdirectory in which the generated document should live
    (e.g., "week1", "week2", etc. for online courses).
    """

    source_file: str
    target_dir_fragment: str
    kind: str
    file_num: int

    @staticmethod
    def from_source_file(
        base_dir: Path, source_file: Path, file_num: int
    ) -> 'DocumentSpec':
        return DocumentSpec(
            source_file.relative_to(base_dir).as_posix(),
            default_path_fragment(source_file),
            determine_document_kind(source_file),
            file_num,
        )


def default_path_fragment(path: PathOrStr) -> str:
    path = Path(path)
    if 'metadata' in path.parts:
        return '$root'
    return SKIP_SPEC_TARGET_DIR_FRAGMENT


def is_notebook_file(path: PathOrStr) -> bool:
    """Return whether `path` is in a directory containing notebooks.

    >>> is_notebook_file("/usr/slides/nb_100.py")
    True
    >>> is_notebook_file("/usr/slides/nb_100.md")
    True
    >>> is_notebook_file("/usr/slides/nb_100.ru")
    True
    >>> is_notebook_file("/usr/slides/nb_100.cpp")
    True
    >>> is_notebook_file("slides/lecture_210_files.py")
    True
    >>> is_notebook_file("workshops/ws_234.py")
    True
    >>> is_notebook_file("slides/")
    False
    >>> is_notebook_file("/usr/slides/lecture_123.txt")
    False
    >>> is_notebook_file("foo/lecture_123.txt")
    False
    """
    path = Path(path)
    is_path_in_correct_dir = any(part in NOTEBOOK_DIRS for part in path.parts)
    does_name_match_pattern = bool(NOTEBOOK_REGEX.match(path.name))
    return is_path_in_correct_dir and bool(does_name_match_pattern)


NOTEBOOK_DIRS = ['slides', 'workshops']
NOTEBOOK_REGEX = re.compile(
    r'^(nb|lecture|topic|ws|workshop|project)_(.*)\.(py|cpp|ru|md)$'
)


def determine_document_kind(path: PathOrStr) -> str:
    if is_notebook_file(path):
        return 'Notebook'
    elif is_folder_to_copy(path) and Path(path).is_dir():
        return 'Folder'
    else:
        return 'DataFile'


SKIP_SPEC_TARGET_DIR_FRAGMENT = '-'
