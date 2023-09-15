from attr import define

from clm.core.course import Course
from clm.core.data_sink import DataSink
from clm.core.data_source import DataSource, DATA_SOURCE_TYPES
from clm.core.dependency import find_dependencies
from clm.core.output_spec import OutputSpec
from clm.data_sinks.folder_data_sink import FolderDataSink
from clm.utils.location import Location


@define
class FolderDataSource(DataSource):
    @property
    def dependencies(self) -> list[tuple[Location, Location], ...]:
        # For file watchers it might make sense to ensure that the dictionary depends on
        # all contained files. Then we could copy the dictionary only if one of the files
        # changes, etc. However, this seems to elaborate for now.
        # loc = source.source_loc
        # children = loc.glob("**/*")
        # return tuple((child, loc) for child in children if child.is_file())
        return []

    def process(self, course, output_spec: OutputSpec) -> DataSink:
        return FolderDataSink(course=course, output_spec=output_spec, data_source=self)

    def get_target_name(self, course: "Course", output_spec: OutputSpec) -> str:
        return self.source_loc.name


DATA_SOURCE_TYPES["Folder"] = FolderDataSource
