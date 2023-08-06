"""
Specs are descriptions of objects that can be edited as text.

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
    Iterator,
    TYPE_CHECKING,
)

from clm.utils.general import find
from clm.utils.path_utils import (
    PathOrStr,
    base_path_for_csv_file,
    is_folder_to_copy,
    is_contained_in_folder_to_copy,
)
from clm.core.document_spec import DocumentSpec

if TYPE_CHECKING:
    from clm.core.document import Document


# %%
SKIP_DIRS = [
    '__pycache__',
    '.git',
    '.ipynb_checkpoints',
    '.pytest_cache',
    '.tox',
    '.vs',
    '.vscode',
    '.idea',
    'build',
    'dist',
    '.cargo',
    '.idea',
    '.vscode',
    'target',
    'out',
]
SKIP_PATH_REGEX = re.compile(r'(.*\.egg-info.*|.*cmake-build-.*)')
SKIP_FILE_REGEX = re.compile(r'^[_.](.*)(\.*)?')
KEEP_FILES = ['__init__.py', '__main__.py']
HEADER_LENGTH = 5


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
SKIP_SPEC_TARGET_DIR_FRAGMENTS = ['-', '', '$skip']


# %%
def find_potential_course_files(path: PathOrStr) -> Iterator[Path]:
    return (
        file
        for file in Path(path).glob('**/*')
        if (
            (file.is_file() and is_potential_course_file(file))
            or is_folder_to_copy(file)
        )
    )


@dataclass
class CourseSpec:
    base_dir: Path
    target_dir: Path
    template_dir: Path = None
    lang: str = 'en'
    document_specs: list[DocumentSpec] = field(
        default_factory=list, repr=False
    )
    prog_lang: str = 'python'

    def __post_init__(self):
        if self.template_dir is None:
            self.template_dir = self.base_dir / 'templates'

    def __iter__(self):
        return iter(self.document_specs)

    def __len__(self):
        return len(self.document_specs)

    def __getitem__(self, item):
        if isinstance(item, int):
            return self.document_specs[item]
        else:
            return find(
                self.document_specs, item, key=attrgetter('source_file')
            )

    @classmethod
    def from_dir(
        cls,
        base_dir: PathOrStr,
        target_dir: PathOrStr,
        template_dir: PathOrStr | None = None,
    ) -> 'CourseSpec':
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
                for file_num, file in enumerate(
                    find_potential_course_files(base_dir), 1
                )
            ),
            key=attrgetter('source_file'),
        )

    def merge(
        self, other: 'CourseSpec'
    ) -> tuple[list[DocumentSpec], list[DocumentSpec]]:
        """Merge the document specs of `other` into our document specs.

        Equality is checked according to the source files.

        Returns the new and deleted specs."""

        spec: DocumentSpec
        new_specs, remaining_specs, deleted_specs = self._copy_existing_specs(
            other
        )
        new_specs.extend(
            sorted(remaining_specs, key=attrgetter('source_file'))
        )
        return new_specs, deleted_specs

    def _copy_existing_specs(self, other):
        new_specs = []
        deleted_specs = []
        remaining_specs = set(other.document_specs)
        for existing_spec in self.document_specs:
            # Copy the existing spec if its path was not deleted, i.e., if we
            # find a corresponding spec in the remaining specs.
            spec = find(
                existing_spec, remaining_specs, key=attrgetter('source_file')
            )
            if spec is not None:
                new_specs.append(existing_spec)
                remaining_specs.remove(spec)
            else:
                deleted_specs.append(existing_spec)
        return new_specs, remaining_specs, deleted_specs

    def to_csv(self, csv_file: Path) -> None:
        with open(csv_file, 'x', encoding='utf-8', newline='') as csvfile:
            spec_writer = csv.writer(csvfile, delimiter=',', quotechar='"')
            spec_writer.writerow(
                (
                    'Base Dir:',
                    self.base_dir.relative_to(
                        base_path_for_csv_file(csv_file)
                    ).as_posix(),
                )
            )
            spec_writer.writerow(
                (
                    'Target Dir:',
                    self.target_dir.relative_to(
                        base_path_for_csv_file(csv_file)
                    ).as_posix(),
                )
            )
            spec_writer.writerow(
                (
                    'Template Dir:',
                    self.template_dir.relative_to(
                        base_path_for_csv_file(csv_file)
                    ).as_posix(),
                )
            )
            spec_writer.writerow(('Language:', self.lang))
            spec_writer.writerow(('Programming Language:', self.prog_lang))
            spec_writer.writerow(())
            # Write only the first three fields of the spec, ignore the dir number.
            spec_writer.writerows(spec[:3] for spec in self.document_specs)

    @classmethod
    def read_csv(cls, path: PathOrStr) -> 'CourseSpec':
        path = Path(path).absolute()
        with open(path, 'r', encoding='utf-8', newline='') as csv_file:
            return cls.read_csv_from_stream(
                csv_file, base_path_for_csv_file(path)
            )

    @classmethod
    def read_csv_from_stream(cls, csv_stream, root_dir: PathOrStr):
        """Read the spec (in CSV format) from a stream.

        Resolve relative paths against `root_dir`."""

        if isinstance(root_dir, str):
            root_dir = Path(root_dir)
        assert root_dir.is_absolute()
        csv_entries = list(csv.reader(csv_stream))
        (
            course_dir,
            target_dir,
            template_dir,
            lang,
            prog_lang,
        ) = cls.parse_csv_header(csv_entries)
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
                    logging.error(f'Skipping bad entry in CSV file: {data}.')
        return CourseSpec(
            base_dir=root_dir / course_dir,
            target_dir=root_dir / target_dir,
            template_dir=root_dir / template_dir,
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
            if csv_entries[0][0].strip() != 'Base Dir:':
                raise ValueError(
                    f'Bad CSV file: Expected base dir entry, got {csv_entries[0]}.'
                )
            if csv_entries[1][0].strip() != 'Target Dir:':
                raise ValueError(
                    f'Bad CSV file: Expected target dir entry, got {csv_entries[1]}.'
                )
            if csv_entries[2][0].strip() != 'Template Dir:':
                raise ValueError(
                    f'Bad CSV file: Expected template dir entry, got {csv_entries[2]}.'
                )
            if csv_entries[3][0].strip() != 'Language:':
                raise ValueError(
                    f'Bad CSV file: Expected language entry, got {csv_entries[3]}.'
                )
            # Fix CSV files without Programming Language entry:
            if not csv_entries[4]:
                csv_entries.insert(4, ['Programming Language:', 'python'])
            if csv_entries[4][0].strip() != 'Programming Language:':
                raise ValueError(
                    f'Bad CSV file: Expected programming language entry, got {csv_entries[4]}.'
                )
            if csv_entries[HEADER_LENGTH] and any(csv_entries[HEADER_LENGTH]):
                raise ValueError(
                    f'Bad CSV file: Expected empty line, got {csv_entries[HEADER_LENGTH]}.'
                )
        except IndexError:
            raise ValueError(
                f'Bad CSV file: Incomplete header: {csv_entries[:HEADER_LENGTH]}.'
            )

    @property
    def documents(self) -> list['Document']:
        from clm.core.document import Document

        return [
            Document.from_spec(self, document_spec)
            for document_spec in self.document_specs
            if document_spec.target_dir_fragment
            not in SKIP_SPEC_TARGET_DIR_FRAGMENTS
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
        print(f'Replacing document specs with {starting_spec_file}')
        # If we have a starting spec we replace the documents in the spec file.
        starting_spec = CourseSpec.read_csv(starting_spec_file)
        course_spec.document_specs = starting_spec.document_specs
    course_spec.to_csv(spec_file)


# %%
def update_course_spec_file(
    spec_file: Path,
) -> tuple[CourseSpec, list[DocumentSpec]]:
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
