from pathlib import Path

from attrs import define

from clx.core.course_files.image_file import ImageFile
from clx.infrastructure.operation import Operation


@define
class DrawIoFile(ImageFile):
    """Draw.io diagram file that converts to PNG images."""

    async def get_processing_operation(self, target_dir: Path) -> Operation:
        from clx.core.operations.convert_drawio_file import ConvertDrawIoFileOperation

        return ConvertDrawIoFileOperation(
            input_file=self,
            output_file=self.img_path,
        )
