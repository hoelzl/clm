import logging
from abc import ABC, abstractmethod
from pathlib import Path

from attrs import field, frozen

from clx.course_file import CourseFile
from clx_common.messaging.base_classes import Payload
from clx_common.operation import Operation

logger = logging.getLogger(__name__)


@frozen
class ConvertSourceOutputFileOperation(Operation, ABC):
    input_file: "CourseFile" = field(repr=False)
    output_file: Path

    @abstractmethod
    def object_type(self) -> str:
        """Return the type of object we are processing, e.g., DrawIO file"""
        ...


    async def execute(self, backend, *args, **kwargs) -> None:
        try:
            logger.info(
                f"Converting {self.object_type()}: '{self.input_file.relative_path}' "
                f"-> '{self.output_file}'"
            )
            await backend.execute_operation(self, self.payload())
        except Exception as e:
            logger.exception(
                f"Error while converting {self.object_type()}: "
                f"'{self.input_file.relative_path}': {e}"
            )
            raise

    @abstractmethod
    def payload(self) -> Payload:
        ...