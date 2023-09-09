"""
Specs are descriptions of objects that can be edited as text.

A `CourseSpec` is a description of a complete course.
"""

from operator import attrgetter
from typing import (
    TYPE_CHECKING,
)

from attr import field, define

from clm.core.course_layout import CourseLayout
from clm.core.data_source_spec import DataSourceSpec
from clm.utils.general import find
from clm.utils.location import Location

if TYPE_CHECKING:
    from clm.core.data_source import DataSource

SKIP_SPEC_TARGET_DIR_FRAGMENTS = ["-", "", "$skip"]


@define
class CourseSpec:
    source_loc: Location
    target_loc: Location
    layout: CourseLayout
    template_loc: Location = field()
    lang: str = "en"
    data_source_specs: list[DataSourceSpec] = field(factory=list, repr=False)
    prog_lang: str = "python"

    # noinspection PyUnresolvedReferences
    @template_loc.default
    def _template_loc_default(self) -> Location:
        return self.source_loc / "templates"

    def __iter__(self):
        return iter(self.data_source_specs)

    def __len__(self):
        return len(self.data_source_specs)

    def __getitem__(self, item):
        if isinstance(item, int):
            return self.data_source_specs[item]
        else:
            return find(self.data_source_specs, item, key=attrgetter("source_file"))

    def merge(
        self, other: "CourseSpec"
    ) -> tuple[list[DataSourceSpec], list[DataSourceSpec]]:
        """Merge the data-source specs of `other` into our data-source specs.

        Equality is checked according to the source files.

        Returns the new and deleted specs."""

        spec: DataSourceSpec
        new_specs, remaining_specs, deleted_specs = self._copy_existing_specs(other)
        new_specs.extend(sorted(remaining_specs, key=attrgetter("source_loc")))
        return new_specs, deleted_specs

    def _copy_existing_specs(self, other):
        new_specs = []
        deleted_specs = []
        remaining_specs = set(other.data_source_specs)
        for existing_spec in self.data_source_specs:
            # Copy the existing spec if its path was not deleted, i.e., if we
            # find a corresponding spec in the remaining specs.
            spec = find(existing_spec, remaining_specs, key=attrgetter("source_loc"))
            if spec is not None:
                new_specs.append(existing_spec)
                remaining_specs.remove(spec)
            else:
                deleted_specs.append(existing_spec)
        return new_specs, remaining_specs, deleted_specs

    @property
    def data_sources(self) -> list["DataSource"]:
        from clm.core.data_source import DataSource

        return [
            DataSource.from_spec(self, data_source_spec)
            for data_source_spec in self.data_source_specs
            if data_source_spec.target_dir_fragment
            not in SKIP_SPEC_TARGET_DIR_FRAGMENTS
        ]
