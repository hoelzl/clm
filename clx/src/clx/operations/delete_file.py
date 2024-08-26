import asyncio
import logging
from pathlib import Path

from attrs import frozen

from clx.course_file import CourseFile
from clx_common.operation import Operation

logger = logging.getLogger(__name__)


@frozen
class DeleteFileOperation(Operation):
    file: "CourseFile"
    file_to_delete: Path

    async def execute(self, backend, *args, **kwargs) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.exec_sync)

    def exec_sync(self):
        logger.info(f"Deleting {self.file_to_delete}")
        self.file_to_delete.unlink()
        self.file.generated_outputs.remove(self.file_to_delete)
