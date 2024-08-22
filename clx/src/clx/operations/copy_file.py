import logging
import shutil
from pathlib import Path
from typing import Any

from attrs import frozen

from clx.course_file import DataFile
from clx.operation import Operation

logger = logging.getLogger(__name__)


@frozen
class CopyFileOperation(Operation):
    input_file: "DataFile"
    output_file: Path

    async def exec(self, *args, **kwargs) -> Any:
        logger.info(f"Copying {self.input_file.relative_path} to {self.output_file}")
        if not self.output_file.parent.exists():
            self.output_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.input_file.path, self.output_file)
        self.input_file.generated_outputs.add(self.output_file)
