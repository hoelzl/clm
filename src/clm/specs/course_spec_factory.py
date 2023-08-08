import re
from operator import attrgetter
from pathlib import Path
from typing import Iterator

from clm.core.directory_kind import IGNORED_LABEL
from clm.core.course_spec import CourseSpec
from clm.core.document_spec import DocumentSpec
from clm.specs.course_spec_readers import CourseSpecCsvReader
from clm.specs.course_spec_writers import CourseSpecCsvWriter
from clm.specs.document_spec_factory import DocumentSpecFactory
from clm.utils.path_utils import (
    PathOrStr,
    is_contained_in_folder_to_copy,
    is_folder_to_copy,
)


class CourseSpecFactory:
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

    @classmethod
    def _create_document_specs(cls, base_dir: Path):
        spec_factory = DocumentSpecFactory(base_dir)
        document_specs = (
            spec_factory.create_document_spec(file, file_num)
            # FIXME: use separate counters by file kind, not only by directory.
            for file_num, file in enumerate(
                cls._find_potential_course_files(base_dir), 1
            )
        )
        # FIXME: Document specs with empty kind should never be generated.
        document_specs = (
            ds for ds in document_specs if ds.label != IGNORED_LABEL
        )
        return sorted(document_specs, key=attrgetter('source_file'))

    @classmethod
    def _find_potential_course_files(cls, base_dir) -> Iterator[Path]:
        return (
            file
            for file in Path(base_dir).glob('**/*')
            if (
                (file.is_file() and is_potential_course_file(file))
                or (
                    is_folder_to_copy(file)
                    and not any(part in SKIP_DIRS for part in file.parts)
                )
            )
        )


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

    course_spec = CourseSpecFactory.from_dir(course_dir, target_dir)
    if lang:
        course_spec.lang = lang.lower()
    if prog_lang:
        course_spec.prog_lang = prog_lang.lower()
    if starting_spec_file:
        print(f'Replacing document specs with {starting_spec_file}')
        # If we have a starting spec we replace the documents in the spec file.
        starting_spec = CourseSpecCsvReader.read_csv(starting_spec_file)
        course_spec.document_specs = starting_spec.document_specs
    CourseSpecCsvWriter.to_csv(course_spec, spec_file)


# %%
def update_course_spec_file(
    spec_file: Path,
) -> tuple[CourseSpec, list[DocumentSpec]]:
    """Update a spec file to reflect changes in its sources."""
    spec = CourseSpecCsvReader.read_csv(spec_file)
    spec_from_dir = CourseSpecFactory.from_dir(
        base_dir=spec.base_dir,
        target_dir=spec.target_dir,
        template_dir=spec.template_dir,
    )
    merged_specs, deleted_specs = spec.merge(spec_from_dir)
    spec.document_specs = merged_specs
    return spec, deleted_specs


SKIP_DIRS = [
    '__pycache__',
    '.git',
    '.ipynb_checkpoints',
    '.mypy_cache',
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
