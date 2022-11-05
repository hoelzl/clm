"""
Course specs are descriptions of objects that can be edited as text.

A `DocumentSpec` is a description of a single document.
A `CourseSpec` is a description of a complete course.
"""

# %%
import csv
import re
from dataclasses import dataclass, field
from operator import attrgetter
from pathlib import Path
from typing import Any, Iterator, NamedTuple, TYPE_CHECKING

from clm.utils.path_utils import PathOrStr

if TYPE_CHECKING:
    from clm.core.document import Document

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
SKIP_SPEC_TARGET_DIR_FRAGMENT = "-"


# %%
def default_path_fragment(path: PathOrStr) -> str:
    path = Path(path)
    match path.name:
        case "python-logo-no-text.png":
            return "img/"
    if "metadata" in path.parts:
        return "../"
    return SKIP_SPEC_TARGET_DIR_FRAGMENT


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
    target_dir_fragment: str
    kind: str

    @staticmethod
    def from_source_file(base_dir: Path, source_file: Path) -> "DocumentSpec":
        return DocumentSpec(
            source_file.relative_to(base_dir).as_posix(),
            default_path_fragment(source_file),
            "Notebook" if is_notebook_file(source_file) else "DataFile",
        )


# %%
@dataclass
class CourseSpec:
    base_dir: Path
    target_dir: Path
    template_dir: Path = None
    lang: str = "en"
    document_specs: list[DocumentSpec] = field(default_factory=list, repr=False)

    def __post_init__(self):
        if self.template_dir is None:
            self.template_dir = self.base_dir / "templates"

    def __iter__(self):
        return iter(self.document_specs)

    def __len__(self):
        return len(self.document_specs)

    def __getitem__(self, item):
        return self[item]

    @classmethod
    def from_dir(
        cls,
        base_dir: PathOrStr,
        target_dir: PathOrStr,
        template_dir: PathOrStr | None = None,
    ) -> "CourseSpec":
        base_dir = Path(base_dir)
        target_dir = Path(target_dir)
        if template_dir is not None:
            template_dir = Path(template_dir)
        return CourseSpec(
            base_dir=base_dir,
            target_dir=target_dir,
            template_dir=template_dir,
            document_specs=list(cls._create_document_specs(base_dir)),
        )

    @staticmethod
    def _create_document_specs(base_dir: Path):
        return sorted(
            (
                DocumentSpec.from_source_file(base_dir, file)
                for file in find_potential_course_files(base_dir)
            ),
            key=attrgetter("source_file"),
        )

    def to_csv(self, csv_file: Path) -> None:
        with open(csv_file, "x", encoding="utf-8", newline="") as csvfile:
            spec_writer = csv.writer(csvfile, delimiter=",", quotechar='"')
            spec_writer.writerow(("Base Dir:", self.base_dir.absolute().as_posix()))
            spec_writer.writerow(("Target Dir:", self.target_dir.absolute().as_posix()))
            spec_writer.writerow(
                ("Template Dir:", self.template_dir.absolute().as_posix())
            )
            spec_writer.writerow(("Language", self.lang))
            spec_writer.writerow(())
            spec_writer.writerows(self.document_specs)

    @classmethod
    def read_csv(cls, path: PathOrStr) -> "CourseSpec":
        with open(path, "r", encoding="utf-8", newline="") as csv_file:
            return cls.read_csv_from_stream(csv_file)

    @classmethod
    def read_csv_from_stream(cls, csv_stream):
        """Read the spec (in CSV format) from a stream.

        >>> CourseSpec.read_csv_from_stream(getfixture("course_spec_csv_stream"))
        CourseSpec(base_dir=...Path('/tmp/course'),
                   target_dir=...Path('/tmp/output'),
                   template_dir=...Path('/tmp/other-course/templates'),
                   lang='de')
        """
        spec_reader = csv.reader(csv_stream)
        csv_entries = [entry for entry in spec_reader]
        base_dir, target_dir, template_dir, lang = cls.parse_csv_header(csv_entries)
        document_specs = [DocumentSpec(*data) for data in csv_entries[5:]]
        return CourseSpec(
            base_dir=base_dir,
            target_dir=target_dir,
            template_dir=template_dir,
            lang=lang,
            document_specs=document_specs,
        )

    CsvFileHeader = tuple[Path, Path, Path, str]

    @classmethod
    def parse_csv_header(cls, csv_entries: list[list[str]]) -> CsvFileHeader:
        cls._assert_header_is_correct(csv_entries)
        return (
            Path(csv_entries[0][1].strip()),
            Path(csv_entries[1][1].strip()),
            Path(csv_entries[2][1].strip()),
            csv_entries[3][1].strip(),
        )

    @classmethod
    def _assert_header_is_correct(cls, csv_entries: list[list[str]]) -> None:
        try:
            if csv_entries[0][0].strip() != "Base Dir:":
                raise ValueError(
                    f"Bad CSV file: Expected base dir entry, got {csv_entries[0]}."
                )
            if csv_entries[1][0].strip() != "Target Dir:":
                raise ValueError(
                    f"Bad CSV file: Expected target dir entry, got {csv_entries[1]}."
                )
            if csv_entries[2][0].strip() != "Template Dir:":
                raise ValueError(
                    f"Bad CSV file: Expected template dir entry, got {csv_entries[2]}."
                )
            if csv_entries[3][0].strip() != "Language:":
                raise ValueError(
                    f"Bad CSV file: Expected language entry, got {csv_entries[3]}."
                )
            if csv_entries[4] and any(csv_entries[4]):
                raise ValueError(
                    f"Bad CSV file: Expected empty line, got {csv_entries[3]}."
                )
        except IndexError:
            raise ValueError(f"Bad CSV file: Incomplete header: {csv_entries[:4]}.")

    @property
    def documents(self) -> list["Document"]:
        from clm.core.document import Document

        return [
            Document.from_spec(self, document_spec)
            for document_spec in self.document_specs
            if document_spec.target_dir_fragment != SKIP_SPEC_TARGET_DIR_FRAGMENT
        ]


# %%
def create_course_spec_file(
    spec_file: Path, course_dir: Path, target_dir: Path, remove_existing=False
):
    if remove_existing:
        spec_file.unlink(missing_ok=True)

    course_spec = CourseSpec.from_dir(course_dir, target_dir)
    course_spec.to_csv(spec_file)
