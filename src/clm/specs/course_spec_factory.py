import re
from pathlib import Path
from typing import Iterator

from attr import define

from clm.core.course_layout import (
    CourseLayout,
    get_course_layout,
)
from clm.core.course_spec import CourseSpec
from clm.core.data_source_spec import DataSourceSpec
from clm.core.directory_kind import IGNORED_LABEL
from clm.specs.course_spec_readers import CourseSpecCsvReader
from clm.specs.course_spec_writers import CourseSpecCsvWriter
from clm.specs.data_source_spec_factory import DataSourceSpecFactory
from clm.utils.location import Location, FileSystemLocation


@define
class CourseSpecFactory:
    base_loc: Location
    target_loc: Location
    template_loc: Location = None
    course_layout: CourseLayout | str = "legacy_python"

    def __attrs_post_init__(self):
        if self.template_loc is None:
            self.template_loc = self.base_loc / "templates"
        if isinstance(self.course_layout, str):
            self.course_layout = get_course_layout(self.course_layout)

    def create_spec(self, debug=False) -> "CourseSpec":
        return CourseSpec(
            source_loc=self.base_loc,
            target_loc=self.target_loc,
            template_loc=self.template_loc,
            data_source_specs=list(self._create_data_source_specs(debug)),
            layout=self.course_layout,
        )

    def _create_data_source_specs(self, debug):
        spec_factory = DataSourceSpecFactory(self.course_layout, self.base_loc)
        initial_data_source_specs = [
            spec_factory.create_data_source_spec(file, file_num)
            # FIXME: use separate counters by file kind, not only by directory.
            for file_num, file in enumerate(self._find_potential_course_files(debug), 1)
        ]
        if debug:
            print(f"Found {len(initial_data_source_specs)} data source specs.")
        # FIXME: Data source specs with empty kind should never be generated.
        data_source_specs = [
            ds for ds in initial_data_source_specs if ds.label != IGNORED_LABEL
        ]
        if debug:
            print(f"Retained {len(data_source_specs)} data source specs.")
            print("Dropped data source specs:")
            for ds in initial_data_source_specs:
                if ds not in data_source_specs:
                    print(f"  {ds.source_loc.as_posix()}")
        return sorted(data_source_specs, key=lambda ds: ds.source_loc.as_posix())

    def _find_potential_course_files(self, debug) -> Iterator[Location]:
        for dir_ in self._find_potential_course_dirs(debug):
            if debug:
                print(f"Checking potential course dir {dir_.relative_path}")
            for file in dir_.glob("*"):
                if not self._is_ignored_file(file):
                    if debug:
                        print(f"  Adding potential course file {file.relative_path}")
                    yield file
                elif debug:
                    print(f"  Ignoring potential course file {file.relative_path}")

    def _find_potential_course_dirs(self, debug) -> Iterator[Location]:
        visited_dirs = set()
        for pattern, _ in self.course_layout.directory_patterns:
            for dir_ in self.base_loc.glob(pattern):
                if not self._is_ignored_dir(dir_) and dir_ not in visited_dirs:
                    visited_dirs.add(dir_)
                    yield dir_
                elif debug and self._is_ignored_dir(dir_):
                    print(f"Ignoring potential course dir {dir_.relative_path}")

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
    factory = CourseSpecFactory(
        FileSystemLocation(course_dir, ""),
        FileSystemLocation(target_dir, ""),
        course_layout=course_layout,
    )
    course_spec = factory.create_spec()
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
    spec_file: Path, drop_unused: bool = False, debug: bool = False
) -> tuple[CourseSpec, list[DataSourceSpec]]:
    """Update a spec file to reflect changes in its sources."""
    spec = CourseSpecCsvReader.read_csv(spec_file, FileSystemLocation)
    layout = spec.layout
    spec_from_dir = CourseSpecFactory(
        base_loc=spec.source_loc,
        target_loc=spec.target_loc,
        template_loc=spec.template_loc,
        course_layout=layout,
    ).create_spec(debug=debug)
    merged_specs, deleted_specs = spec.merge(
        spec_from_dir, drop_unused=drop_unused, debug=debug
    )
    spec.data_source_specs = merged_specs
    return spec, deleted_specs
