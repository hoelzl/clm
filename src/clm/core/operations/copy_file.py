import logging
from pathlib import Path
from typing import Any

from attrs import frozen

from clm.core.course_file import CourseFile
from clm.infrastructure.backend import Backend
from clm.infrastructure.operation import Operation
from clm.infrastructure.utils.copy_file_data import CopyFileData

logger = logging.getLogger(__name__)


@frozen
class CopyFileOperation(Operation):
    input_file: "CourseFile"
    output_file: Path

    async def execute(self, backend: Backend, *args, **kwargs) -> Any:
        # ``source_path`` falls back to ``path`` for ordinary files and
        # routes around it for virtual ``<include>`` files (where
        # ``input_file.path`` is the logical location inside the topic
        # but the bytes live at ``source_origin``).
        copy_data = CopyFileData(
            input_path=self.input_file.source_path,
            relative_input_path=self.input_file.relative_path,
            output_path=self.output_file,
        )
        await backend.copy_file_to_output(copy_data)
        self.input_file.generated_outputs.add(self.output_file)
