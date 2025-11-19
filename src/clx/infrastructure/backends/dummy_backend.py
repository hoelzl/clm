import logging
from pathlib import Path

from attrs import define

from clx.infrastructure.backend import Backend
from clx.infrastructure.messaging.base_classes import Payload
from clx.infrastructure.operation import Operation
from clx.infrastructure.utils.copy_dir_group_data import CopyDirGroupData
from clx.infrastructure.utils.copy_file_data import CopyFileData
from clx.infrastructure.utils.file import File

logger = logging.getLogger(__name__)


@define
class DummyBackend(Backend):
    async def execute_operation(self, operation: "Operation", payload: Payload) -> None:
        logger.info(f"DummyBackend:Skipping operation:{operation!r}")

    async def wait_for_completion(self) -> None:
        logger.info("DummyBackend:Waiting for completion")

    async def copy_file_to_output(self, copy_data: "CopyFileData"):
        logger.info(f"DummyBackend:Copying file to output:{copy_data}")

    async def copy_dir_group_to_output(self, copy_data: "CopyDirGroupData"):
        logger.info(f"DummyBackend:Copying dir-group to output:{copy_data!r}")

    async def delete_dependencies(self, file: "File") -> None:
        logger.info(f"DummyBackend:Deleting dependencies from {file.path.name}")

    async def delete_file(self, path: Path) -> None:
        logger.info(f"DummyBackend:Deleting file {path}")
