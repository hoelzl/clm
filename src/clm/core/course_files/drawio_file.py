from pathlib import Path
from typing import TYPE_CHECKING

from attrs import define

from clm.core.course_files.image_file import ImageFile
from clm.infrastructure.operation import Operation

if TYPE_CHECKING:
    from clm.core.output_target import OutputTarget


@define
class DrawIoFile(ImageFile):
    """Draw.io diagram file that converts to images (PNG or SVG)."""

    async def get_processing_operation(
        self,
        target_dir: Path,
        stage: int | None = None,
        target: "OutputTarget | None" = None,
        implicit_executions: set[tuple[str, str, str]] | None = None,
    ) -> Operation:
        from clm.core.operations.convert_drawio_file import ConvertDrawIoFileOperation

        # DrawIoFile runs in FIRST_EXECUTION_STAGE (default), return NoOperation for other stages
        if stage is not None and stage != self.execution_stage:
            from clm.infrastructure.operation import NoOperation

            return NoOperation()

        return ConvertDrawIoFileOperation(
            input_file=self,
            output_file=self.img_path,
        )
