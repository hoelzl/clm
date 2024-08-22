import json
import logging
from pathlib import Path
from typing import Any

from attrs import frozen

from clx.course_file import Notebook
from clx.operation import Operation
from clx.utils.path_utils import is_image_file, is_image_source_file
from clx.utils.text_utils import sanitize_key_name, unescape

logger = logging.getLogger(__name__)

NB_PROCESS_ROUTING_KEY = "notebook.process"
NB_PROCESS_STREAM = "NOTEBOOK_PROCESS_STREAM"
NB_RESULT_STREAM = "NOTEBOOK_RESULT_STREAM"
NB_RESULT_ROUTING_KEY = "notebook.result"

# TODO: Fix this

@frozen
class ProcessNotebookOperation(Operation):
    input_file: "Notebook"
    output_file: Path
    lang: str
    format: str
    mode: str
    prog_lang: str

    @property
    def reply_subject(self) -> str:
        id_ = self.input_file.topic.id
        num = self.input_file.number_in_section
        lang = self.lang
        format_ = self.format
        mode = self.mode
        routing_key_postfix = f"{id_}_{num}_{lang}_{format_}_{mode}"
        return sanitize_key_name(f"notebook.result.{routing_key_postfix}")

    async def exec(self, *args, **kwargs) -> Any:
        try:
            logger.info(
                f"Processing notebook '{self.input_file.relative_path}' "
                f"to '{self.output_file}'"
            )
            await self.process_request()
            self.input_file.generated_outputs.add(self.output_file)
        except Exception as e:
            logger.exception(
                f"Error while processing notebook {self.input_file.relative_path}: {e}"
            )
            raise

    async def process_request(self):
        logger.debug(
            f"Notebook-Processor: Processing request for "
            f"{self.input_file.relative_path}"
        )

        logger.debug(f"Notebook-Processor: Processing {self.input_file.relative_path} ")

    def build_payload(self):
        notebook_path = self.input_file.relative_path.name
        other_files = {
            str(file.relative_path): file.path.read_text()
            for file in self.input_file.topic.files
            if file != self.input_file
            and not is_image_file(file.path)
            and not is_image_source_file(file.path)
        }
        return {
            "notebook_text": self.input_file.path.read_text(),
            "notebook_path": notebook_path,
            "reply_subject": self.reply_subject,
            "prog_lang": self.prog_lang,
            "language": self.lang,
            "notebook_format": self.format,
            "output_type": self.mode,
            "other_files": other_files,
        }

    def write_notebook_to_file(self, msg):
        data = json.loads(msg.data.decode())
        logger.debug(f"Notebook-Processor: Decoded message {str(data)[:50]}")
        if isinstance(data, dict):
            if notebook := data.get("result"):
                logger.debug(
                    f"Notebook-Processor: Writing notebook to {self.output_file}"
                )
                if not self.output_file.parent.exists():
                    self.output_file.parent.mkdir(parents=True, exist_ok=True)
                self.output_file.write_text(notebook)
            elif error := data.get("error"):
                logger.error(f"Notebook-Processor: Error: {error}")
            else:
                logger.error(f"Notebook-Processor: No key 'result' in {unescape(data)}")
        else:
            logger.error(f"Notebook-Processor: Reply not a dict {unescape(data)}")
