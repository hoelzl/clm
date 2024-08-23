import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any

from attrs import frozen

from clx.backend import Backend
from clx.course_files.data_file import DataFile
from clx.operation import Operation

logger = logging.getLogger(__name__)


@frozen
class CopyFileOperation(Operation):
    input_file: "DataFile"
    output_file: Path

    async def execute(self, backend: Backend, *args, **kwargs) -> Any:
        logger.info(f"Copying {self.input_file.relative_path} to {self.output_file}")
        # TODO: This should be moved to the backend
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self.exec_sync)
        except Exception as e:
            logger.exception(
                f"Error while copying file '{self.input_file.relative_path}' "
                f"to {self.output_file}: {e}"
            )
            raise

    def exec_sync(self):
        if not self.output_file.parent.exists():
            self.output_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.input_file.path, self.output_file)
        self.input_file.generated_outputs.add(self.output_file)
