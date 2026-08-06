import asyncio
import logging
from pathlib import Path

from attrs import define

from clm.core.backend import Backend
from clm.core.build_data_classes import BuildWarning
from clm.core.messaging.base_classes import Payload
from clm.core.operation import Operation
from clm.core.utils.copy_dir_group_data import CopyDirGroupData
from clm.core.utils.copy_file_data import CopyFileData
from clm.core.utils.file import File

logger = logging.getLogger(__name__)


@define
class DummyBackend(Backend):
    async def execute_operation(self, operation: "Operation", payload: Payload) -> None:
        logger.info(f"DummyBackend:Skipping operation:{operation!r}")

    async def wait_for_completion(self, all_submitted: asyncio.Event | None = None) -> bool:
        logger.info("DummyBackend:Waiting for completion")
        return True

    async def copy_file_to_output(self, copy_data: "CopyFileData"):
        logger.info(f"DummyBackend:Copying file to output:{copy_data}")

    async def copy_dir_group_to_output(self, copy_data: "CopyDirGroupData") -> list[BuildWarning]:
        logger.info(f"DummyBackend:Copying dir-group to output:{copy_data!r}")
        return []

    async def delete_dependencies(self, file: "File") -> None:
        logger.info(f"DummyBackend:Deleting dependencies from {file.path.name}")

    async def delete_file(self, path: Path) -> None:
        logger.info(f"DummyBackend:Deleting file {path}")
