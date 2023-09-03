import logging
import shutil
from attr import define
from typing import TYPE_CHECKING

from clm.core.course import Course
from clm.core.data_source_paths import full_target_path_for_data_source
from clm.core.data_sink import DataSink
from clm.core.output_spec import OutputSpec

if TYPE_CHECKING:
    from clm.data_sources.plain_file_data_source import PlainFileDataSource


@define
class PlainFileDataSink(DataSink):
    doc: "PlainFileDataSource"

    def write_to_target(self, course: Course, output_spec: OutputSpec) -> None:
        target_path = full_target_path_for_data_source(
            self.doc, course=course, output_spec=output_spec
        )
        logging.info(
            f"Copying file {self.doc.source_loc.as_posix()!r} "
            f"to {target_path.as_posix()!r}."
        )
        target_path.parent.mkdir(exist_ok=True, parents=True)
        shutil.copy(self.doc.source_loc.absolute(), target_path)
