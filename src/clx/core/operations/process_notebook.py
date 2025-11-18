import logging
from base64 import b64encode
from pathlib import Path
from typing import Any

from attrs import frozen

from clx.core.course_files.notebook_file import NotebookFile
from clx.infrastructure.messaging.correlation_ids import (
    new_correlation_id,
    note_correlation_id_dependency,
)
from clx.infrastructure.messaging.notebook_classes import NotebookPayload
from clx.infrastructure.operation import Operation
from clx.infrastructure.utils.path_utils import is_image_file, is_image_source_file, is_ignored_file_for_course

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
        import time
        file_path = self.input_file.relative_path
        try:
            logger.info(f"Processing notebook '{file_path}' to '{self.output_file}'")

            # TIME: Payload construction
            payload_start = time.time()
            payload = await self.payload()
            payload_elapsed = time.time() - payload_start

            # TIME: Backend submission
            backend_start = time.time()
            await backend.execute_operation(self, payload)
            backend_elapsed = time.time() - backend_start

            total_elapsed = payload_elapsed + backend_elapsed

            if total_elapsed > 0.05:  # Log if operation.execute > 50ms
                logger.warning(
                    f"[TIMING] operation.execute took {total_elapsed:.3f}s "
                    f"(payload={payload_elapsed:.3f}s, backend={backend_elapsed:.3f}s) "
                    f"for {file_path}"
                )

            self.input_file.generated_outputs.add(self.output_file)
        except Exception as e:
            op = "'ProcessNotebookOperation'"
            logger.error(f"Error while executing {op} for '{file_path}': {e}")
            logger.debug(f"Error traceback for '{file_path}'", exc_info=e)
            raise

    def compute_other_files(self):
        def relative_path(file):
            return str(file.relative_path).replace("\\", "/")

        other_files = {
            relative_path(file): b64encode(file.path.read_bytes())
            for file in self.input_file.topic.files
            if file != self.input_file
            and not is_image_file(file.path)
            and not is_image_source_file(file.path)
            and not is_ignored_file_for_course(file.path)
        }
        return other_files

    async def payload(self) -> NotebookPayload:
        import time
        payload_start = time.time()

        correlation_id = await new_correlation_id()

        # TIME: Read input file
        read_start = time.time()
        data = self.input_file.path.read_text(encoding="utf-8")
        read_elapsed = time.time() - read_start

        # TIME: Compute other files
        other_start = time.time()
        other_files = self.compute_other_files()
        other_elapsed = time.time() - other_start

        payload = NotebookPayload(
            data=data,
            correlation_id=correlation_id,
            input_file=str(self.input_file.path),
            input_file_name=self.input_file.path.name,
            output_file=str(self.output_file),
            kind=self.kind,
            prog_lang=self.prog_lang,
            language=self.language,
            format=self.format,
            other_files=other_files,
        )
        await note_correlation_id_dependency(correlation_id, payload)

        payload_elapsed = time.time() - payload_start

        if payload_elapsed > 0.05:  # Log if payload construction > 50ms
            logger.warning(
                f"[TIMING] Payload construction took {payload_elapsed:.3f}s "
                f"(read={read_elapsed:.3f}s, other_files={other_elapsed:.3f}s, "
                f"num_other={len(other_files)}) for {self.input_file.path.name}"
            )

        return payload

    @property
    def service_name(self) -> str:
        return "notebook-processor"
