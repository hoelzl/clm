import logging
from abc import ABC, abstractmethod
from pathlib import Path

from attrs import frozen

from clx.course_file import CourseFile
from clx.operation import Operation

logger = logging.getLogger(__name__)


@frozen
class ConvertSourceOutputFileOperation(Operation, ABC):
    input_file: "CourseFile"
    output_file: Path

    @abstractmethod
    def object_type(self) -> str:
        """Return the type of object we are processing, e.g., DrawIO file"""
        ...


    async def execute(self, backend, *args, **kwargs) -> None:
        try:
            logger.info(
                f"Converting {self.object_type}: '{self.input_file.relative_path}' "
                f"-> '{self.output_file}'"
            )
            backend.execute_operation(self, *args, **kwargs)
        except Exception as e:
            logger.exception(
                f"Error while converting {self.object_type}: "
                f"'{self.input_file.relative_path}': {e}"
            )
            raise
