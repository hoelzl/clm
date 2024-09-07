import logging
from base64 import b64encode
from pathlib import Path
from typing import Any

from attrs import frozen

from clx.course_files.notebook_file import NotebookFile
from clx_common.messaging.correlation_ids import (
    new_correlation_id,
    note_correlation_id_dependency,
)
from clx_common.messaging.notebook_classes import NotebookPayload
from clx_common.operation import Operation
from clx_common.utils.path_utils import is_image_file, is_image_source_file

logger = logging.getLogger(__name__)


@frozen
class ProcessNotebookOperation(Operation):
    input_file: "NotebookFile"
    output_file: Path
    language: str
    format: str
    kind: str
    prog_lang: str

    async def execute(self, backend, *args, **kwargs) -> Any:
        file_path = self.input_file.relative_path
        try:
            logger.info(f"Processing notebook '{file_path}' to '{self.output_file}'")
            payload = await self.payload()
            await backend.execute_operation(self, payload)
            self.input_file.generated_outputs.add(self.output_file)
        except Exception as e:
            op = "'ProcessNotebookOperation'"
            logger.error(f"Error while executing {op} for '{file_path}': {e}")
            logger.debug(f"Error traceback for '{file_path}'", exc_info=e)
            raise

    def compute_other_files(self):
        other_files = {
            str(file.relative_path): b64encode(file.path.read_bytes())
            for file in self.input_file.topic.files
            if file != self.input_file
            and not is_image_file(file.path)
            and not is_image_source_file(file.path)
        }
        return other_files

    async def payload(self) -> NotebookPayload:
        correlation_id = await new_correlation_id()
        payload = NotebookPayload(
            data=self.input_file.path.read_text(),
            correlation_id=correlation_id,
            input_file=str(self.input_file.path),
            input_file_name=self.input_file.path.name,
            output_file=str(self.output_file),
            kind=self.kind,
            prog_lang=self.prog_lang,
            language=self.language,
            format=self.format,
            other_files=self.compute_other_files(),
        )
        await note_correlation_id_dependency(correlation_id, payload)
        return payload

    @property
    def service_name(self) -> str:
        return "notebook-processor"
