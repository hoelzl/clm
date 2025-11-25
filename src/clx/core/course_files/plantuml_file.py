from pathlib import Path

from attrs import define

from clx.core.course_files.image_file import ImageFile
from clx.infrastructure.operation import Operation


@define
class PlantUmlFile(ImageFile):
    """PlantUML diagram file that converts to PNG images."""

    async def get_processing_operation(
        self, target_dir: Path, stage: int | None = None
    ) -> Operation:
        from clx.core.operations.convert_plantuml_file import (
            ConvertPlantUmlFileOperation,
        )

        # PlantUmlFile runs in FIRST_EXECUTION_STAGE (default), return NoOperation for other stages
        if stage is not None and stage != self.execution_stage:
            from clx.infrastructure.operation import NoOperation

            return NoOperation()

        return ConvertPlantUmlFileOperation(
            input_file=self,
            output_file=self.img_path,
        )
