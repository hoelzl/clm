import json
import logging
from pathlib import Path
from typing import Any

from attrs import frozen

from clx.course_file import Notebook
from clx.operation import Operation
from clx.utils.path_utils import is_image_file, is_image_source_file

logger = logging.getLogger(__name__)


@frozen
class ProcessNotebookOperation(Operation):
    input_file: "Notebook"
    output_file: Path
    lang: str
    format: str
    mode: str
    prog_lang: str

    async def execute(self, backend, *args, **kwargs) -> Any:
        try:
            logger.info(
                f"Processing notebook '{self.input_file.relative_path}' "
                f"to '{self.output_file}'"
            )
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
