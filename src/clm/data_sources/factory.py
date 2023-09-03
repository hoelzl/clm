from typing import Type

from clm.core.course_spec import CourseSpec
from clm.core.data_source import DataSource
from clm.core.data_source_spec import DataSourceSpec
from clm.data_sources.folder_data_source import FolderDataSource
from clm.data_sources.notebook_data_source import NotebookDataSource
from clm.data_sources.plain_file_data_source import PlainFileDataSource
from clm.utils.location import Location, FileSystemLocation


def data_source_from_spec(
    course_spec: CourseSpec,
    data_source_spec: DataSourceSpec,
    location_type: Type[Location] = FileSystemLocation,
) -> "DataSource":
    """Return the data_source for this spec."""

    data_source_type: type[DataSource] = DATA_SOURCE_TYPES[data_source_spec.label]
    # noinspection PyArgumentList
    source_loc = location_type(
        base_dir=course_spec.base_dir, relative_path=data_source_spec.source_file
    )
    prog_lang = course_spec.prog_lang
    # noinspection PyArgumentList
    return data_source_type(
        source_loc=source_loc,
        target_dir_fragment=data_source_spec.target_dir_fragment,
        prog_lang=prog_lang,
        file_num=data_source_spec.file_num,
    )


DATA_SOURCE_TYPES = {
    "Notebook": NotebookDataSource,
    "DataFile": PlainFileDataSource,
    "Folder": FolderDataSource,
}
