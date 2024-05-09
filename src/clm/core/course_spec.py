"""
Specs are descriptions of objects that can be edited as text.

A `CourseSpec` is a description of a complete course.
"""

import itertools
from operator import attrgetter
from typing import (
    TYPE_CHECKING,
)

from attr import field, define
from networkx import DiGraph

from clm.core.course_layout import CourseLayout
from clm.core.data_source_spec import DataSourceSpec
from clm.utils.general import find, split_list_by_predicate
from clm.utils.location import Location

if TYPE_CHECKING:
    from clm.core.data_source import DataSource

SKIP_SPEC_TARGET_DIR_FRAGMENTS = ["-", "", "$skip"]


def has_skipped_target_dir(data_source_spec: DataSourceSpec) -> bool:
    return data_source_spec.target_dir_fragment in SKIP_SPEC_TARGET_DIR_FRAGMENTS


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
        self, other: "CourseSpec", drop_unused: bool = False, debug: bool = False
    ) -> tuple[list[DataSourceSpec], list[DataSourceSpec]]:
        """Merge the data-source specs of `other` into our data-source specs.

        Equality is checked according to the source files.

        Returns the new and deleted specs.
        """

        spec: DataSourceSpec
        existing_specs, new_specs, deleted_specs = self._copy_existing_specs(
            other, debug=debug
        )
        if debug:
            print("While merging specs:")
            print(f"  Found {len(existing_specs)} existing specs.")
            print(f"  Found {len(new_specs)} new specs.")
            print(f"  Deleting {len(deleted_specs)} deleted specs.")
        existing_specs.extend(new_specs)
        existing_specs = self._sort_specs(existing_specs)

        if drop_unused:
            dropped_specs, existing_specs = split_list_by_predicate(
                existing_specs, has_skipped_target_dir
            )
            return existing_specs, self._sort_specs(dropped_specs + deleted_specs)
        return existing_specs, self._sort_specs(deleted_specs)

    def _copy_existing_specs(self, other, debug):
        existing_specs = []
        deleted_specs = []
        other_specs = set(other.data_source_specs)
        other_specs_to_delete = set()
        for existing_spec in self.data_source_specs:
            # Copy the existing spec if its path was not deleted, i.e., if we
            # find a corresponding spec in the remaining specs.
            spec = find(existing_spec, other_specs, key=attrgetter("source_loc"))
            if spec is not None:
                if debug:
                    print(
                        f"Copying existing spec {existing_spec.source_loc.relative_path}"
                    )
                existing_specs.append(existing_spec)
                # Don't delete the other spec right away, since we want to retain duplicates in existing_specs
                # (i.e., files that appear in multiple output subdirectories).
                other_specs_to_delete.add(spec)
            else:
                deleted_specs.append(existing_spec)
        new_specs = other_specs - other_specs_to_delete
        return existing_specs, new_specs, deleted_specs

    def _sort_specs(self, specs):
        inactive_specs, active_specs = split_list_by_predicate(
            specs, has_skipped_target_dir
        )
        specs = sorted(active_specs, key=lambda s: s.target_dir_fragment) + sorted(
            inactive_specs, key=attrgetter("source_loc")
        )
        return specs

    @property
    def data_source_map(self) -> dict[Location, "DataSource"]:
        from clm.core.data_source import DataSource

        result = {}

        for data_source_spec in self.data_source_specs:
            if not has_skipped_target_dir(data_source_spec):
                existing_data_sources = result.setdefault(
                    data_source_spec.source_loc, []
                )
                existing_data_sources.append(
                    DataSource.from_spec(self, data_source_spec)
                )
        return result

    @staticmethod
    def dependency_graph(data_source_map: dict[Location, list["DataSource"]]):
        dependency_graph = DiGraph()
        for data_source in itertools.chain.from_iterable(data_source_map.values()):
            dependency_graph.add_node(data_source.source_loc)
            for dependency in data_source.dependencies:
                dependency_graph.add_edge(*dependency, tag="dependency")
        return dependency_graph
