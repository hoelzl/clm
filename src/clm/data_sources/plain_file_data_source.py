from attr import define

from clm.core.course import Course
from clm.core.data_source import DataSource
from clm.core.data_sink import DataSink
from clm.core.output_spec import OutputSpec
from clm.data_sinks.plain_file_data_sink import PlainFileDataSink


@define
class PlainFileDataSource(DataSource):
    def process(self, course, output_spec: OutputSpec) -> DataSink:
        return PlainFileDataSink(self)

    def get_target_name(self, course: "Course", output_spec: OutputSpec) -> str:
        return self.source_loc.name
