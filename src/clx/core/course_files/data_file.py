from pathlib import Path

from attrs import define

from clx.core.course_file import CourseFile
from clx.core.utils.execution_utils import LAST_EXECUTION_STAGE
from clx.infrastructure.operation import Concurrently, Operation
from clx.infrastructure.utils.path_utils import output_specs


@define
class DataFile(CourseFile):
    @property
    def execution_stage(self) -> int:
        return LAST_EXECUTION_STAGE

    async def get_processing_operation(self, target_dir: Path) -> Operation:
        from clx.core.operations.copy_file import CopyFileOperation

        return Concurrently(
            CopyFileOperation(
                input_file=self,
                output_file=self.output_dir(output_dir, lang) / self.relative_path,
            )
            for lang, _, _, output_dir in output_specs(
                self.course,
                target_dir,
                languages=self.course.output_languages,
                kinds=self.course.output_kinds,
            )
        )
