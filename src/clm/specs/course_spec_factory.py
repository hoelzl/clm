import re
from operator import attrgetter
from pathlib import Path
from typing import Iterator

from attr import define, field

from clm.core.course_layout import (
    CourseLayout,
    get_course_layout,
)
from clm.core.course_spec import CourseSpec
from clm.core.directory_kind import IGNORED_LABEL
from clm.core.data_source_spec import DataSourceSpec
from clm.specs.course_spec_readers import CourseSpecCsvReader
from clm.specs.course_spec_writers import CourseSpecCsvWriter
from clm.specs.data_source_spec_factory import DataSourceSpecFactory
from clm.utils.location import Location, FileSystemLocation


@define
class CourseSpecFactory:
    base_loc: Location = field(validator=lambda _, __, val: val.is_absolute())
    target_loc: Location
    template_loc: Location = None
    course_layout: CourseLayout | str = "legacy_python"

    def __attrs_post_init__(self):
        if self.template_loc is None:
            self.template_loc = self.base_loc / "templates"
        if isinstance(self.course_layout, str):
            self.course_layout = get_course_layout(self.course_layout)

    def create_spec(self) -> "CourseSpec":
        return CourseSpec(
            base_loc=self.base_loc,
            target_loc=self.target_loc,
            template_loc=self.template_loc,
            data_source_specs=list(self._create_data_source_specs()),
            layout=self.course_layout,
        )

    def _create_data_source_specs(self):
        spec_factory = DataSourceSpecFactory(self.course_layout, self.base_loc)
        data_source_specs = (
            spec_factory.create_data_source_spec(file, file_num)
            # FIXME: use separate counters by file kind, not only by directory.
            for file_num, file in enumerate(self._find_potential_course_files(), 1)
        )
        # FIXME: Data source specs with empty kind should never be generated.
        data_source_specs = (
            ds for ds in data_source_specs if ds.label != IGNORED_LABEL
        )
        return sorted(data_source_specs, key=attrgetter("source_file"))

    def _find_potential_course_files(self) -> Iterator[Path]:
        for dir_ in self._find_potential_course_dirs():
            for file in dir_.glob("*"):
                if not self._is_ignored_file(file):
                    yield file

    def _find_potential_course_dirs(self) -> Iterator[Path]:
        for pattern, _ in self.course_layout.directory_patterns:
            for dir_ in self.base_loc.glob(pattern):
                if not self._is_ignored_dir(dir_):
                    yield dir_

    def _is_ignored_file(self, file) -> bool:
        if file.name in self.course_layout.kept_files:
            return False
        if file.name in self.course_layout.ignored_files:
            return True
        return bool(re.match(self.course_layout.ignored_files_regex, file.name))

    def _is_ignored_dir(self, dir_) -> bool:
        for part in dir_.parts:
            if part in self.course_layout.ignored_directories:
                return True
            if re.match(self.course_layout.ignored_directories_regex, part):
                return True
        return False


def create_course_spec_file(
    spec_file: Path,
    course_dir: Path,
    target_dir: Path,
    lang: str | None = None,
    prog_lang: str | None = None,
    course_layout: str | None = None,
    remove_existing=False,
    starting_spec_file: Path | None = None,
):
    if course_layout is None:
        if lang == "python":
            course_layout = "legacy_python"
        else:
            course_layout = lang
    course_spec = CourseSpecFactory(
        FileSystemLocation(course_dir, ""),
        FileSystemLocation(target_dir, ""),
        course_layout=course_layout,
    ).create_spec()
    if lang:
        course_spec.lang = lang.lower()
    if prog_lang:
        course_spec.prog_lang = prog_lang.lower()
    if starting_spec_file:
        print(f"Replacing data-source specs with {starting_spec_file}")
        # If we have a starting spec we replace the data_sources in the spec file.
        starting_spec = CourseSpecCsvReader.read_csv(
            starting_spec_file, FileSystemLocation
        )
        course_spec.data_source_specs = starting_spec.data_source_specs

    if remove_existing:
        spec_file.unlink(missing_ok=True)
    CourseSpecCsvWriter.to_csv(course_spec, spec_file)


def update_course_spec_file(
    spec_file: Path,
) -> tuple[CourseSpec, list[DataSourceSpec]]:
    """Update a spec file to reflect changes in its sources."""
    spec = CourseSpecCsvReader.read_csv(spec_file, FileSystemLocation)
    layout = spec.layout
    spec_from_dir = CourseSpecFactory(
        base_loc=spec.base_loc,
        target_loc=spec.target_loc,
        template_loc=spec.template_loc,
        course_layout=layout,
    ).create_spec()
    merged_specs, deleted_specs = spec.merge(spec_from_dir)
    spec.data_source_specs = merged_specs
    return spec, deleted_specs
