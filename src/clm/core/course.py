# %%
import csv
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from operator import attrgetter
from pathlib import Path
from typing import Any, Iterator, NamedTuple, TYPE_CHECKING

from clm.core.output_spec import OutputSpec
from clm.utils.path import PathOrStr

if TYPE_CHECKING:
    from clm.core.document import Document

if TYPE_CHECKING:
    # Make PyCharm happy, since it doesn't understand the pytest extensions to doctests.
    def getfixture(_name: str) -> Any:
        ...


# %%
NOTEBOOK_DIRS = ["slides", "workshops"]
NOTEBOOK_REGEX = re.compile(r"^(nb|lecture|topic|ws|workshop)_(.*)\.py$")
SKIP_DIRS = [
    "__pycache__",
    ".git",
    ".ipynb_checkpoints",
    ".pytest_cache",
    ".vs",
    ".vscode",
    ".idea",
    "build",
]
SKIP_PATH_REGEX = re.compile(r".*\.egg-info.*")
SKIP_FILE_REGEX = re.compile(r"^(_|\.)(.*)(\.*)?")
KEEP_FILES = ["__init__.py", "__main__.py"]


# %%
def is_notebook_file(path: PathOrStr) -> bool:
    """Return whether `path` is in a directory containing notebooks.

    >>> is_notebook_file("/usr/slides/nb_100.py")
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


# %%
def is_potential_course_file(path: PathOrStr) -> bool:
    """Return whether we should skip this file when generating course templates.

    >>> is_potential_course_file("_my_private_file.py")
    False
    >>> is_potential_course_file("subdir/_my_private_file.py")
    False
    >>> is_potential_course_file("subdir/a_public_file.py")
    True
    >>> is_potential_course_file("__init__.py")
    True
    >>> is_potential_course_file("__pycache__/some_file.py")
    False
    >>> is_potential_course_file("foo_bar.egg-info")
    False
    >>> is_potential_course_file("foo_bar.egg-info/my_file")
    False
    """
    path = Path(path)
    is_path_in_skipped_dir = any(part in SKIP_DIRS for part in path.parts)
    does_path_match_skip_pattern = SKIP_PATH_REGEX.match(path.as_posix())
    does_name_match_skip_pattern = SKIP_FILE_REGEX.match(path.name)
    keep_anyway = path.name in KEEP_FILES
    return (
        (not is_path_in_skipped_dir)
        and (not does_path_match_skip_pattern)
        and (not does_name_match_skip_pattern or keep_anyway)
    )


# %%
def default_dict(path: PathOrStr) -> str:
    path = Path(path)
    match path.name:
        case "python-logo-no-text.png":
            return "img"
    if "metadata" in path.parts:
        return ""
    return "-"


# %%
def find_potential_course_files(path: PathOrStr) -> Iterator[Path]:
    return (
        file
        for file in Path(path).glob("**/*")
        if file.is_file() and is_potential_course_file(file)
    )


# %%
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
    relative_target_dir: str
    kind: str


# %%
def document_spec_from_source_file(base_dir: Path, source_file: Path) -> DocumentSpec:
    return DocumentSpec(
        source_file.relative_to(base_dir).as_posix(),
        default_dict(source_file),
        "Notebook" if is_notebook_file(source_file) else "DataFile",
    )


# %%
def create_document_specs_from_dir(source_dir: Path) -> list[DocumentSpec]:
    return sorted(
        (
            document_spec_from_source_file(source_dir, file)
            for file in find_potential_course_files(source_dir)
        ),
        key=attrgetter("source_file"),
    )


# %%
def create_course_spec_file(source_dir: Path, spec_file: Path) -> None:
    specs = create_document_specs_from_dir(source_dir)
    with open(spec_file, "x", encoding="utf-8") as csvfile:
        spec_writer = csv.writer(csvfile)
        spec_writer.writerows(specs)


# %%
if __name__ == "__main__" and False:
    base_dir = Path.home() / "programming/python/courses/own/python-courses/"
    create_course_spec_file(
        base_dir / "python-private/",
        base_dir / "course-specs/python-beginner-2.csv",
    )


# %%
def load_course_spec_file(spec_file: Path):
    with open(spec_file, "r", encoding="utf-8") as csvfile:
        spec_reader = csv.reader(csvfile)
        return [DocumentSpec(*data) for data in spec_reader if data[1] != "-"]


# %%
if __name__ == "__main__":
    base_dir = Path.home() / "programming/python/courses/own/python-courses/"
    specs = load_course_spec_file(base_dir / "course-specs/python-beginner.csv")


# %%
class DocumentProvider(ABC):
    """The interface for getting the source files of courses.

    We don't simply use a list of Paths as source documents for courses, since it's
    likely that we will need more elaborate structures, e.g., with videos stored in
    a content-management system.
    """

    @property
    @abstractmethod
    def documents(self):
        ...


# %%


@dataclass()
class Course:
    """A course comprises all data that should be processed or referenced."""

    document_provider: DocumentProvider
    target_dir: Path

    def __init__(
        self, source_document_provider: DocumentProvider, target_dir: PathOrStr
    ):
        self.document_provider = source_document_provider
        self.target_dir = Path(target_dir)

    @property
    def source_documents(self) -> list["Document"]:
        """Return the documents corresponding to the source files of this course."""
        return self.document_provider.documents

    def process(self, output_kind: OutputSpec):
        for doc in self.source_documents:
            doc.process(output_kind, target_path=self.target_dir)
