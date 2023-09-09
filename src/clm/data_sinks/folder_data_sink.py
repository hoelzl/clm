import logging
import shutil
from attr import define
from typing import TYPE_CHECKING

from clm.core.data_source_location import full_target_location_for_data_source
from clm.core.data_sink import DataSink

if TYPE_CHECKING:
    from clm.data_sources.folder_data_source import FolderDataSource

# FIXME: This should be taken from the course layout.
SKIP_DIRS = [
    "__pycache__",
    ".git",
    ".ipynb_checkpoints",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    ".vs",
    ".vscode",
    ".idea",
    "build",
    "dist",
    ".cargo",
    ".idea",
    ".vscode",
    "target",
    "out",
]


@define
class FolderDataSink(DataSink):
    data_source: "FolderDataSource"

    def write_to_target(self, course, output_spec):
        target_location = full_target_location_for_data_source(
            self.data_source, course=course, output_spec=output_spec
        )
        logging.info(
            f"Copying folder {self.data_source.source_loc!r} "
            f"to {target_location.as_posix()!r}."
        )
        if not self.data_source.source_loc.exists():
            logging.warning(
                f"Trying to copy folder {self.data_source.source_loc} which does not exist."
            )
        target_location.parent.mkdir(exist_ok=True, parents=True)
        self.data_source.source_loc.copytree(
            target_location,
            ignore=shutil.ignore_patterns("*.egg-info", *SKIP_DIRS),
        )
