"""DuplicatedImageFile - Image file that copies to each output variant folder.

This module provides the DuplicatedImageFile class which handles image files
by copying them to each output variant folder (HTML/Notebooks/Code x kinds x
languages). This is the default behavior and is compatible with VS Code
notebook viewing.

Output structure:
    output/public/De/Kurs/Folien/Html/Code-Along/Section/img/image.png
    output/public/De/Kurs/Folien/Html/Completed/Section/img/image.png
    output/public/De/Kurs/Folien/Notebooks/Code-Along/Section/img/image.png
    ... (repeated for all format/kind/language combinations)
"""

from pathlib import Path
from typing import TYPE_CHECKING

from attrs import define

from clx.core.course_file import CourseFile
from clx.core.utils.execution_utils import FIRST_EXECUTION_STAGE
from clx.infrastructure.operation import Concurrently, NoOperation, Operation
from clx.infrastructure.utils.path_utils import output_specs

if TYPE_CHECKING:
    from clx.core.output_target import OutputTarget


@define
class DuplicatedImageFile(CourseFile):
    """Image file that is copied to each output variant folder.

    Unlike SharedImageFile which copies to a central img/ folder per course,
    DuplicatedImageFile copies images to each output variant (HTML/Notebooks/Code x
    Code-Along/Completed/Speaker x De/En). This is compatible with VS Code
    notebook viewing but uses more storage.

    This is the default image storage mode.
    """

    @property
    def execution_stage(self) -> int:
        return FIRST_EXECUTION_STAGE

    async def get_processing_operation(
        self,
        target_dir: Path,
        stage: int | None = None,
        target: "OutputTarget | None" = None,
        implicit_executions: set[tuple[str, str, str]] | None = None,
    ) -> Operation:
        """Create copy operations for each output variant.

        Creates one copy operation for each language/format/kind combination,
        placing images in the same relative path as in the source.

        Args:
            target_dir: Root output directory
            stage: If specified, only return operations for this stage
            target: Output target for filtering (if provided)
            implicit_executions: Not used for image files, but kept for interface

        Returns:
            Concurrently operation containing copy operations, or NoOperation
            if this isn't the right stage.
        """
        from clx.core.operations.copy_file import CopyFileOperation

        if stage is not None and stage != self.execution_stage:
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
