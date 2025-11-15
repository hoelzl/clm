import logging
from pathlib import Path
from typing import Any

from attrs import frozen

from clx.core.course_files.data_file import DataFile
from clx.infrastructure.backend import Backend
from clx.infrastructure.operation import Operation
from clx.infrastructure.utils.copy_file_data import CopyFileData

logger = logging.getLogger(__name__)


@frozen
class CopyFileOperation(Operation):
    input_file: "DataFile"
    output_file: Path

    async def execute(self, backend: Backend, *args, **kwargs) -> Any:
        copy_data = CopyFileData(
            input_path=self.input_file.path,
            relative_input_path=self.input_file.relative_path,
            output_path=self.output_file,
        )
        await backend.copy_file_to_output(copy_data)
        self.input_file.generated_outputs.add(self.output_file)
