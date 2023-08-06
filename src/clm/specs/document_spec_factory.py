import re
from pathlib import Path

from clm.core.document_spec import DocumentSpec
from clm.utils.path_utils import PathOrStr, is_folder_to_copy


class DocumentSpecFactory:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir

    def create_document_spec(
        self, source_file: Path, file_num: int
    ) -> 'DocumentSpec':
        return DocumentSpec(
            source_file.relative_to(self.base_dir).as_posix(),
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
