"""
Course specs are descriptions of objects that can be edited as text.

A `DocumentSpec` is a description of a single document.
A `CourseSpec` is a description of a complete course.
"""

# %%
import csv
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from operator import attrgetter
from pathlib import Path
from typing import (
    Any,
    Callable,
    Iterable,
    Iterator,
    NamedTuple,
    Optional,
    TYPE_CHECKING,
)

from clm.utils.path_utils import PathOrStr, base_path_for_csv_file

if TYPE_CHECKING:
    from clm.core.document import Document

    # Make PyCharm happy, since it doesn't understand the pytest extensions to doctests.
    def getfixture(_name: str) -> Any:
        ...


# %%
NOTEBOOK_DIRS = ["slides", "workshops"]
NOTEBOOK_REGEX = re.compile(
    r"^(nb|lecture|topic|ws|workshop|project)_(.*)\.(py|cpp|ru|md)$"
)
SKIP_DIRS = [
    "__pycache__",
    ".git",
    ".ipynb_checkpoints",
    ".pytest_cache",
    ".tox",
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
]
FOLDER_DIRS = ["examples", "code"]
SKIP_PATH_REGEX = re.compile(r"(.*\.egg-info.*|.*cmake-build-.*)")
SKIP_FILE_REGEX = re.compile(r"^[_.](.*)(\.*)?")
KEEP_FILES = ["__init__.py", "__main__.py"]
HEADER_LENGTH = 5


# %%
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


def is_folder_to_copy(path: PathOrStr, check_for_dir=True) -> bool:
    """Return whether `path` should be treated as a folder to be copied entierely.

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


def is_contained_in_folder_to_copy(path: PathOrStr, check_for_dir=True) -> bool:
    path = Path(path)
    for p in path.parents:
        if is_folder_to_copy(p, check_for_dir=check_for_dir):
            return True
    return False


# %%
def determine_document_kind(path: PathOrStr) -> str:
    if is_notebook_file(path):
        return "Notebook"
    elif is_folder_to_copy(path) and Path(path).is_dir():
        return "Folder"
    else:
        return "DataFile"


# %%
def is_potential_course_file(path: PathOrStr, check_for_dir=True) -> bool:
    """Return whether we should skip this file when generating course templates.

    >>> is_potential_course_file("_my_private_file.py")
    False
    >>> is_potential_course_file("subdir/_my_private_file.py")
    False
    >>> is_potential_course_file("subdir/a_public_file.py")
    True
    >>> is_potential_course_file("__init__.py")
    True
    >>> is_potential_course_file("examples/my-dir")
    True
    >>> is_potential_course_file("__pycache__/some_file.py")
    False
    >>> is_potential_course_file("foo_bar.egg-info")
    False
    >>> is_potential_course_file("foo_bar.egg-info/my_file")
    False
    >>> is_potential_course_file("examples/my-dir/foo.py", check_for_dir=False)
    False
    >>> is_potential_course_file("code/examples/target/foo.py", check_for_dir=False)
    False
    """
    path = Path(path)
    is_path_in_skipped_dir = any(part in SKIP_DIRS for part in path.parts)
    does_path_match_skip_pattern = SKIP_PATH_REGEX.match(path.as_posix())
    does_name_match_skip_pattern = SKIP_FILE_REGEX.match(path.name)
    keep_anyway = path.name in KEEP_FILES
    if is_path_in_skipped_dir:
        return False
    elif does_path_match_skip_pattern:
        return False
    elif is_contained_in_folder_to_copy(path, check_for_dir=check_for_dir):
        return False
    elif does_name_match_skip_pattern:
        return keep_anyway
    else:
        return True


# %%
SKIP_SPEC_TARGET_DIR_FRAGMENTS = ["-", "", "$skip"]
SKIP_SPEC_TARGET_DIR_FRAGMENT = "-"


# %%
def default_path_fragment(path: PathOrStr) -> str:
    path = Path(path)
    if "metadata" in path.parts:
        return "$root"
    return SKIP_SPEC_TARGET_DIR_FRAGMENT


# %%
def find_potential_course_files(path: PathOrStr) -> Iterator[Path]:
    return (
        file
        for file in Path(path).glob("**/*")
        if (
            (file.is_file() and is_potential_course_file(file))
            or is_folder_to_copy(file)
        )
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
    file_num: int

    @staticmethod
    def from_source_file(base_dir: Path, source_file: Path, file_num: int) -> "DocumentSpec":
        return DocumentSpec(
            source_file.relative_to(base_dir).as_posix(),
            default_path_fragment(source_file),
            determine_document_kind(source_file),
            file_num,
        )


# %%
def find(elt, items: Iterable, key: Optional[Callable] = None):
    """Find an element in an iterable.

    If `key` is not `None`, apply it to each member of `items` and to `elt`
    before performing the comparison.

    >>> find(1, [1, 2, 3])
    1
    >>> find(0, [1, 2, 3]) is None
    True
    >>> find((1, "x"), [(1, "a"), (2, "b")], key=lambda t: t[0])
    (1, 'a')
    >>> find((2, 3), [(1, "a"), (2, "b")], key=lambda t: t[0])
    (2, 'b')
    >>> find((3, "b"), [(1, "a"), (2, "b")], key=lambda t: t[0]) is None
    True
    """
    if key is None:
        for item in items:
            if item == elt:
                return item
    else:
        for item in items:
            if key(item) == key(elt):
                return item
    return None


# %%
@dataclass
class CourseSpec:
    base_dir: Path
    target_dir: Path
    template_dir: Path = None
    lang: str = "en"
    document_specs: list[DocumentSpec] = field(default_factory=list, repr=False)
    prog_lang: str = "python"

    def __post_init__(self):
        if self.template_dir is None:
            self.template_dir = self.base_dir / "templates"

    def __iter__(self):
        return iter(self.document_specs)

    def __len__(self):
        return len(self.document_specs)

    def __getitem__(self, item):
        if isinstance(item, int):
            return self.document_specs[item]
        else:
            return find(self.document_specs, item, key=attrgetter("source_file"))

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
                DocumentSpec.from_source_file(base_dir, file, file_num)
                # FIXME: use separate counters by file kind, not only by directory.
                for file_num, file in enumerate(find_potential_course_files(base_dir), 1)
            ),
            key=attrgetter("source_file"),
        )

    def merge(
        self, other: "CourseSpec"
    ) -> tuple[list[DocumentSpec], list[DocumentSpec]]:
        """Merge the document specs of `other` into our document specs.

        Equality is checked according to the source files.

        Returns the deleted specs.

        >>> cs1 = getfixture("course_spec_1")
        >>> cs1.merge(getfixture("course_spec_2"))
        ([DocumentSpec(source_file='/a/b/topic_3.py',
                target_dir_fragment='part-1',
                kind='Notebook'),
          DocumentSpec(source_file='/a/b/topic_4.py',
                target_dir_fragment='part-1',
                kind='Notebook'),
          DocumentSpec(source_file='/a/b/topic_5.py',
                target_dir_fragment='part-2',
                kind='Notebook'),
          DocumentSpec(source_file='/a/b/topic_6.py',
                target_dir_fragment='part-2',
                kind='Notebook')],
         [DocumentSpec(source_file='/a/b/topic_1.py',
                target_dir_fragment='part-1',
                kind='Notebook'),
          DocumentSpec(source_file='/a/b/topic_2.py',
                target_dir_fragment='part-1',
                kind='Notebook')])
        >>> len(cs1.document_specs)
        4
        >>> [spec.source_file for spec in cs1.document_specs]
        ['/a/b/topic_1.py', '/a/b/topic_2.py', '/a/b/topic_3.py', '/a/b/topic_4.py']
        >>> [spec.target_dir_fragment for spec in cs1.document_specs]
        ['part-1', 'part-1', 'part-1', 'part-1']
        """
        spec: DocumentSpec
        new_specs, remaining_specs, deleted_specs = self._copy_existing_specs(other)
        new_specs.extend(sorted(remaining_specs, key=attrgetter("source_file")))
        return new_specs, deleted_specs

    def _copy_existing_specs(self, other):
        new_specs = []
        deleted_specs = []
        remaining_specs = set(other.document_specs)
        for existing_spec in self.document_specs:
            # Copy the existing spec if its path was not deleted, i.e., if we
            # find a corresponding spec in the remaining specs.
            spec = find(existing_spec, remaining_specs, key=attrgetter("source_file"))
            if spec is not None:
                new_specs.append(existing_spec)
                remaining_specs.remove(spec)
            else:
                deleted_specs.append(existing_spec)
        return new_specs, remaining_specs, deleted_specs

    def to_csv(self, csv_file: Path) -> None:
        with open(csv_file, "x", encoding="utf-8", newline="") as csvfile:
            spec_writer = csv.writer(csvfile, delimiter=",", quotechar='"')
            spec_writer.writerow(
                (
                    "Base Dir:",
                    self.base_dir.relative_to(
                        base_path_for_csv_file(csv_file)
                    ).as_posix(),
                )
            )
            spec_writer.writerow(
                (
                    "Target Dir:",
                    self.target_dir.relative_to(
                        base_path_for_csv_file(csv_file)
                    ).as_posix(),
                )
            )
            spec_writer.writerow(
                (
                    "Template Dir:",
                    self.template_dir.relative_to(
                        base_path_for_csv_file(csv_file)
                    ).as_posix(),
                )
            )
            spec_writer.writerow(("Language:", self.lang))
            spec_writer.writerow(("Programming Language:", self.prog_lang))
            spec_writer.writerow(())
            spec_writer.writerows(self.document_specs)

    @classmethod
    def read_csv(cls, path: PathOrStr) -> "CourseSpec":
        path = Path(path).absolute()
        with open(path, "r", encoding="utf-8", newline="") as csv_file:
            return cls.read_csv_from_stream(csv_file, base_path_for_csv_file(path))

    @classmethod
    def read_csv_from_stream(cls, csv_stream, base_dir: PathOrStr):
        """Read the spec (in CSV format) from a stream.

        >>> CourseSpec.read_csv_from_stream(getfixture("course_spec_csv_stream"),
        ...                                 Path("/tmp").absolute())
        CourseSpec(base_dir=...Path('.../tmp/course'),
                   target_dir=...Path('.../tmp/output'),
                   template_dir=...Path('.../tmp/other-course/templates'),
                   lang='de',
                   prog_lang='python')
        """
        base_dir = Path(base_dir)
        assert base_dir.is_absolute()
        csv_entries = list(csv.reader(csv_stream))
        course_dir, target_dir, template_dir, lang, prog_lang = cls.parse_csv_header(
            csv_entries
        )
        file_counters = defaultdict(int)
        document_specs = []
        for data in csv_entries[HEADER_LENGTH:]:
            if data:
                if len(data) == 3:
                    source_file, target_dir_fragment, kind = data
                    if source_file.startswith('#'):
                        continue  # line is temporarily commented out
                    counter_key = (target_dir_fragment, kind)
                    file_num = file_counters[counter_key] + 1
                    file_counters[counter_key] = file_num
                    document_specs.append(DocumentSpec(*data, file_num))
                else:
                    logging.error(f"Skipping bad entry in CSV file: {data}.")
        return CourseSpec(
            base_dir=base_dir / course_dir,
            target_dir=base_dir / target_dir,
            template_dir=base_dir / template_dir,
            lang=lang,
            prog_lang=prog_lang,
            document_specs=document_specs,
        )

    CsvFileHeader = tuple[Path, Path, Path, str, str]

    @classmethod
    def parse_csv_header(cls, csv_entries: list[list[str]]) -> CsvFileHeader:
        cls._assert_header_is_correct(csv_entries)
        return (
            Path(csv_entries[0][1].strip()),
            Path(csv_entries[1][1].strip()),
            Path(csv_entries[2][1].strip()),
            csv_entries[3][1].strip(),
            csv_entries[4][1].strip(),
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
            # Fix CSV files without Programming Language entry:
            if not csv_entries[4]:
                csv_entries.insert(4, ["Programming Language:", "python"])
            if csv_entries[4][0].strip() != "Programming Language:":
                raise ValueError(
                    f"Bad CSV file: Expected programming language entry, got {csv_entries[4]}."
                )
            if csv_entries[HEADER_LENGTH] and any(csv_entries[HEADER_LENGTH]):
                raise ValueError(
                    f"Bad CSV file: Expected empty line, got {csv_entries[HEADER_LENGTH]}."
                )
        except IndexError:
            raise ValueError(
                f"Bad CSV file: Incomplete header: {csv_entries[:HEADER_LENGTH]}."
            )

    @property
    def documents(self) -> list["Document"]:
        from clm.core.document import Document

        return [
            Document.from_spec(self, document_spec)
            for document_spec in self.document_specs
            if document_spec.target_dir_fragment not in SKIP_SPEC_TARGET_DIR_FRAGMENTS
        ]


# %%
def create_course_spec_file(
    spec_file: Path,
    course_dir: Path,
    target_dir: Path,
    lang: str | None = None,
    prog_lang: str | None = None,
    remove_existing=False,
    starting_spec_file: Path | None = None,
):
    if remove_existing:
        spec_file.unlink(missing_ok=True)

    course_spec = CourseSpec.from_dir(course_dir, target_dir)
    if lang:
        course_spec.lang = lang.lower()
    if prog_lang:
        course_spec.prog_lang = prog_lang.lower()
    if starting_spec_file:
        print(f"Replacing document specs with {starting_spec_file}")
        # If we have a starting spec we replace the documents in the spec file.
        starting_spec = CourseSpec.read_csv(starting_spec_file)
        course_spec.document_specs = starting_spec.document_specs
    course_spec.to_csv(spec_file)


# %%
def update_course_spec_file(spec_file: Path) -> tuple[CourseSpec, list[DocumentSpec]]:
    """Update a spec file to reflect changes in its corresponding directories."""
    spec = CourseSpec.read_csv(spec_file)
    spec_from_dir = CourseSpec.from_dir(
        base_dir=spec.base_dir,
        target_dir=spec.target_dir,
        template_dir=spec.template_dir,
    )
    merged_specs, deleted_specs = spec.merge(spec_from_dir)
    spec.document_specs = merged_specs
    return spec, deleted_specs
