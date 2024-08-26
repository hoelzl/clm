from pathlib import Path

from attrs import define

from clx.course_file import CourseFile
from clx_common.operation import Concurrently, Operation
from clx.utils.div_uils import LAST_EXECUTION_STAGE
from clx.utils.path_utils import output_specs


@define
class DataFile(CourseFile):

    @property
    def execution_stage(self) -> int:
        return LAST_EXECUTION_STAGE

    async def get_processing_operation(self, target_dir: Path) -> Operation:
        from clx.operations.copy_file import CopyFileOperation

        return Concurrently(
            CopyFileOperation(
                input_file=self,
                output_file=self.output_dir(output_dir, lang) / self.relative_path,
            )
            for lang, _, _, output_dir in output_specs(self.course, target_dir)
        )
