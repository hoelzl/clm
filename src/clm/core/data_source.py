"""
A `DataSource` is a file that can be processed into an output.
"""

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from attr import define

from clm.core.data_sink import DataSink
from clm.core.data_source_spec import DataSourceSpec
from clm.core.output_spec import OutputSpec
from clm.utils.location import Location

if TYPE_CHECKING:
    from clm.core.course import Course
    from clm.core.course_spec import CourseSpec


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)


@define
class DataSource(ABC):
    """Representation of a data source existing as file."""

    source_loc: Location
    target_dir_fragment: str
    prog_lang: str
    file_num: int

    @staticmethod
    def from_spec(
        course_spec: "CourseSpec",
        data_source_spec: DataSourceSpec,
    ) -> "DataSource":
        """Return the data_source for this spec."""

        data_source_type: type[DataSource] = DATA_SOURCE_TYPES[data_source_spec.label]
        source_loc = data_source_spec.source_loc
        prog_lang = course_spec.prog_lang
        # noinspection PyArgumentList
        return data_source_type(
            source_loc=source_loc,
            target_dir_fragment=data_source_spec.target_dir_fragment,
            prog_lang=prog_lang,
            file_num=data_source_spec.file_num,
        )

    @abstractmethod
    def process(self, course: "Course", output_spec: OutputSpec) -> DataSink:
        """Process the data source and prepare for copying.

        The output spec determines details of the processing, e.g., whether solutions
        for exercises should be included.
        """
        ...

    @abstractmethod
    def get_target_name(self, course: "Course", output_spec: OutputSpec) -> str:
        """Return the name of the data source in the target directory."""
        ...


DATA_SOURCE_TYPES = {}
"""Mapping from data source label to data source type.

Entries are added by each individual data source type."""
