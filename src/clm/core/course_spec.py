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

from clm.core.document_spec import DocumentSpec
from clm.specs.document_spec_factory import DocumentSpecFactory
from clm.utils.general import find
from clm.utils.path_utils import (
    PathOrStr,
    base_path_for_csv_file,
    is_folder_to_copy,
    is_contained_in_folder_to_copy,
)

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

    @property
    def documents(self) -> list['Document']:
        from clm.core.document import Document

        return [
            Document.from_spec(self, document_spec)
            for document_spec in self.document_specs
            if document_spec.target_dir_fragment
            not in SKIP_SPEC_TARGET_DIR_FRAGMENTS
        ]
