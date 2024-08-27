import logging
from pathlib import Path
from typing import Any

from attrs import frozen

from clx.course_files.notebook_file import NotebookFile
from clx_common.utils.path_utils import is_image_file, is_image_source_file
from clx_common.messaging.notebook_classes import NotebookPayload
from clx_common.operation import Operation

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
        try:
            logger.info(
                f"Processing notebook '{self.input_file.relative_path}' "
                f"to '{self.output_file}'"
            )
            await backend.execute_operation(self, self.payload())
            self.input_file.generated_outputs.add(self.output_file)
        except Exception as e:
            logger.exception(
                f"Error while processing notebook {self.input_file.relative_path}: {e}"
            )
            raise

    def compute_other_files(self):
        other_files = {str(file.relative_path): file.path.read_text() for file in
            self.input_file.topic.files if
            file != self.input_file and not is_image_file(
                file.path) and not is_image_source_file(file.path)}
        return other_files

    def payload(self) -> NotebookPayload:
        return NotebookPayload(
            notebook_text=self.input_file.path.read_text(),
            notebook_path=self.input_file.relative_path.name,
            kind=self.kind,
            prog_lang=self.prog_lang,
            language=self.language,
            format=self.format,
            other_files=self.compute_other_files(),
        )

    @property
    def service_name(self) -> str:
        return "notebook-processor"
