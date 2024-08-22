import logging
from pathlib import Path

from attrs import frozen

from clx.course_file import CourseFile
from clx.operation import Operation

logger = logging.getLogger(__name__)


@frozen
class DeleteFileOperation(Operation):
    file: "CourseFile"
    file_to_delete: Path

    async def exec(self, *args, **kwargs) -> None:
        logger.info(f"Deleting {self.file_to_delete}")
        self.file_to_delete.unlink()
        self.file.generated_outputs.remove(self.file_to_delete)
