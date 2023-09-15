import logging
from typing import TYPE_CHECKING

from attr import define

from clm.core.data_sink import DataSink
from clm.core.data_source_location import full_target_location_for_data_source

if TYPE_CHECKING:
    # noinspection PyUnresolvedReferences
    from clm.data_sources.plain_file_data_source import PlainFileDataSource


@define
class PlainFileDataSink(DataSink["PlainFileDataSource"]):
    def write_to_target(self) -> None:
        target_loc = full_target_location_for_data_source(
            self.data_source, course=self.course, output_spec=self.output_spec
        )
        logging.info(
            f"Copying file {self.data_source.source_loc.as_posix()!r} "
            f"to {target_loc.as_posix()!r}."
        )
        target_loc.parent.mkdir(exist_ok=True, parents=True)
        self.data_source.source_loc.copytree(target_loc)
