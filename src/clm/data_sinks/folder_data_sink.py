import logging
import shutil
from typing import TYPE_CHECKING

from attr import define

from clm.core.data_sink import DataSink

if TYPE_CHECKING:
    # noinspection PyUnresolvedReferences
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
    "CMakeFiles",
]


@define
class FolderDataSink(DataSink["FolderDataSource"]):
    def write_to_target(self):
        target_loc = self.target_loc
        logging.info(
            f"Copying folder {self.data_source.source_loc!r} "
            f"to {target_loc.as_posix()!r}."
        )
        if not self.data_source.source_loc.exists():
            logging.warning(
                f"Trying to copy folder {self.data_source.source_loc} which does not exist."
            )
        target_loc.parent.mkdir(exist_ok=True, parents=True)
        self.data_source.source_loc.copytree(
            target_loc,
            ignore=shutil.ignore_patterns("*.egg-info", *SKIP_DIRS),
        )
