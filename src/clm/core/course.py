# %%
import csv
import re
from dataclasses import dataclass
from operator import attrgetter
from pathlib import Path
from typing import Any, Iterable, Iterator, NamedTuple, TYPE_CHECKING, TypeAlias

from clm.core.output_spec import OutputSpec
from clm.utils.path import PathOrStr

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
SKIP_FILE_REGEX = re.compile(r"^[_.](.*)(\.*)?")
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
def default_path_fragment(path: PathOrStr) -> str:
    path = Path(path)
    match path.name:
        case "python-logo-no-text.png":
            return "img"
    if "metadata" in path.parts:
        return ".."
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
        default_path_fragment(source_file),
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
def create_document_from_spec(base_dir: Path, spec: DocumentSpec):
    """Create a document from a spec.

    >>> notebook_spec = DocumentSpec("foo.py", "week1", "Notebook")
    >>> create_document_from_spec(Path("/tmp"), notebook_spec)
    Notebook(source_path=PosixPath('/tmp/foo.py'), target_dir_fragment='week1')
    >>> data_file_spec = DocumentSpec("my_img.png", "week1", "DataFile")
    >>> create_document_from_spec(Path("/tmp"), data_file_spec)
    DataFile(source_path=PosixPath('/tmp/my_img.png'), target_dir_fragment='week1')
    >>> invalid_spec = DocumentSpec("foo", "week1", "Dir")
    >>> create_document_from_spec(Path("/tmp"), invalid_spec)
    Traceback (most recent call last):
    ...
    ValueError: Invalid document kind: Dir.
    """

    from clm.core.document import Notebook, DataFile

    source_path, dir_fragment, doc_kind = spec
    match doc_kind:
        case "Notebook":
            return Notebook(base_dir / source_path, dir_fragment)
        case "DataFile":
            return DataFile(base_dir / source_path, dir_fragment)
    raise ValueError(f"Invalid document kind: {doc_kind}.")


# %%
CourseSpec: TypeAlias = list[DocumentSpec]


# %%
def create_documents(base_dir: Path, specs: Iterable[DocumentSpec]):
    return [create_document_from_spec(base_dir, spec) for spec in specs]


@dataclass()
class Course:
    """A course comprises all data that should be processed or referenced."""

    target_dir: Path

    def __init__(
        self, source_dir: Path, course_spec: CourseSpec, target_dir: PathOrStr
    ):
        self.documents = create_documents(source_dir, course_spec)
        self.target_dir = Path(target_dir)

    def process(self, output_kind: OutputSpec):
        for doc in self.documents:
            doc.process(output_kind, target_path=self.target_dir)
            doc.copy_to_target(output_kind, target_path=self.target_dir)


# %%
if __name__ == "__main__":
    from clm.core.output_spec import CompletedOutput, CodeAlongOutput, SpeakerOutput

    base_dir = Path.home() / "programming/python/courses/own/python-courses/"
    specs = load_course_spec_file(base_dir / "course-specs/python-beginner.csv")
    course = Course(
        base_dir / "python-private/", specs, base_dir / "online/python-einsteiger"
    )

    output_specs = [
        CompletedOutput("de", "public/Folien"),
        CodeAlongOutput("de", "public/CodeAlong"),
        SpeakerOutput("de", "private/speaker"),
    ]

    for output_kind in output_specs:
        course.process(output_kind)
