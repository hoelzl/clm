from pathlib import Path

from clm.core.course_spec import CourseSpec
from clm.core.data_source import DataSource
from clm.core.data_source_spec import DataSourceSpec
from clm.data_sources.plain_file_data_source import PlainFileDataSource
from clm.data_sources.folder_data_source import FolderDataSource
from clm.data_sources.notebook_data_source import NotebookDataSource


def data_source_from_spec(
    course_spec: CourseSpec, data_source_spec: DataSourceSpec
) -> "DataSource":
    """Return the data_source for this spec."""

    data_source_type: type[DataSource] = DATA_SOURCE_TYPES[data_source_spec.label]
    source_file = Path(data_source_spec.source_file)
    prog_lang = course_spec.prog_lang
    if not source_file.is_absolute():
        source_file = course_spec.base_dir / source_file
    # noinspection PyArgumentList
    return data_source_type(
        source_file=source_file,
        target_dir_fragment=data_source_spec.target_dir_fragment,
        prog_lang=prog_lang,
        file_num=data_source_spec.file_num,
    )


DATA_SOURCE_TYPES = {
    "Notebook": NotebookDataSource,
    "DataFile": PlainFileDataSource,
    "Folder": FolderDataSource,
}
