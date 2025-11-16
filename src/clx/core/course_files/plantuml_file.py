from pathlib import Path

from attrs import define

from clx.core.course_file import CourseFile
from clx.infrastructure.operation import Operation


@define
class PlantUmlFile(CourseFile):
    async def get_processing_operation(self, target_dir: Path) -> Operation:
        from clx.core.operations.convert_plantuml_file import ConvertPlantUmlFileOperation

        return ConvertPlantUmlFileOperation(
            input_file=self,
            output_file=self.img_path,
        )

    @property
    def img_path(self) -> Path:
        from clx.core.utils.text_utils import sanitize_path

        unsanitized = (self.path.parents[1] / "img" / self.path.stem).with_suffix(".png")
        return sanitize_path(unsanitized)

    @property
    def source_outputs(self) -> frozenset[Path]:
        return frozenset({self.img_path})
