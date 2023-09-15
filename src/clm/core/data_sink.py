from abc import ABC, abstractmethod
from attr import define, field
from typing import TYPE_CHECKING, TypeVar, Generic

from clm.utils.location import Location
from clm.core.output_spec import OutputSpec
from clm.core.data_source_location import full_target_location_for_data_source

if TYPE_CHECKING:
    from clm.core.course import Course

    # noinspection PyUnresolvedReferences
    from clm.core.data_source import DataSource


T = TypeVar("T", bound="DataSource")


@define
class DataSink(Generic[T], ABC):
    """Representation of a location to write or copy data to."""

    course: "Course"
    output_spec: OutputSpec
    data_source: T = field()
    target_loc: Location = field()

    # noinspection PyUnresolvedReferences
    @target_loc.default
    def _target_loc_default(self):
        return full_target_location_for_data_source(
            self.data_source, course=self.course, output_spec=self.output_spec
        )

    @abstractmethod
    def write_to_target(self) -> None:
        """Copy the data sink to its destination."""
