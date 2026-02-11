from pathlib import Path
from typing import TYPE_CHECKING

from attrs import define

from clm.core.course_file import CourseFile
from clm.core.utils.execution_utils import LAST_EXECUTION_STAGE
from clm.infrastructure.operation import Concurrently, Operation
from clm.infrastructure.utils.path_utils import output_specs

if TYPE_CHECKING:
    from clm.core.output_target import OutputTarget


@define
class DataFile(CourseFile):
    @property
    def execution_stage(self) -> int:
        return LAST_EXECUTION_STAGE

    async def get_processing_operation(
        self,
        target_dir: Path,
        stage: int | None = None,
        target: "OutputTarget | None" = None,
        implicit_executions: set[tuple[str, str, str]] | None = None,
    ) -> Operation:
        from clm.core.operations.copy_file import CopyFileOperation

        # DataFile always runs in LAST_EXECUTION_STAGE, return NoOperation for other stages
        if stage is not None and stage != self.execution_stage:
            from clm.infrastructure.operation import NoOperation

            return NoOperation()

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
                target=target,
            )
        )
