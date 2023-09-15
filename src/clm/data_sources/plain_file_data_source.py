from attr import define

from clm.core.course import Course
from clm.core.data_sink import DataSink
from clm.core.data_source import DataSource, DATA_SOURCE_TYPES
from clm.core.dependency import find_dependencies
from clm.core.output_spec import OutputSpec
from clm.data_sinks.plain_file_data_sink import PlainFileDataSink
from clm.utils.location import Location


@define
class PlainFileDataSource(DataSource):
    def process(self, course, output_spec: OutputSpec) -> DataSink:
        return PlainFileDataSink(
            course=course, output_spec=output_spec, data_source=self
        )

    def get_target_name(self, course: Course, output_spec: OutputSpec) -> str:
        return self.source_loc.name


DEPENDENT_SUFFIX_MAP = {
    ".pu": [".svg"],
    ".drawio": [".svg"],
}


@find_dependencies.register
def _(source: PlainFileDataSource, _course: Course) -> list[tuple[Location, Location]]:
    dependent_suffixes = DEPENDENT_SUFFIX_MAP.get(source.source_loc.suffix, [])
    loc = source.source_loc
    return [(loc, loc.with_suffix(suffix)) for suffix in dependent_suffixes]


DATA_SOURCE_TYPES["DataFile"] = PlainFileDataSource
