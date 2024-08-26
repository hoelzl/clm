from pathlib import Path

from attrs import define

from clx.course_file import CourseFile
from clx_common.operation import Operation


@define
class PlantUmlFile(CourseFile):
    async def get_processing_operation(self, target_dir: Path) -> Operation:
        from clx.operations.convert_plantuml_file import ConvertPlantUmlFileOperation

        return ConvertPlantUmlFileOperation(
            input_file=self,
            output_file=self.img_path,
        )

    @property
    def img_path(self) -> Path:
        return (self.path.parents[1] / "img" / self.path.stem).with_suffix(".png")

    @property
    def source_outputs(self) -> frozenset[Path]:
        return frozenset({self.img_path})
