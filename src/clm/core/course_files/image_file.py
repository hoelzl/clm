"""Base class for course files that generate images."""

from pathlib import Path

from attrs import define

from clm.core.course_file import CourseFile


@define
class ImageFile(CourseFile):
    """Base class for files that convert to images (PNG or SVG).

    This class provides common functionality for diagram files that
    generate images, such as PlantUML and Draw.io files. The output
    format is determined by the course's image_format setting.
    """

    @property
    def img_path(self) -> Path:
        """Path to the generated image.

        Images are generated in an 'img' subdirectory relative to the
        file's parent directory, with the same stem but format-appropriate extension.
        """
        from clm.core.utils.text_utils import sanitize_path

        ext = f".{self.course.image_format}"
        unsanitized = (self.path.parents[1] / "img" / self.path.stem).with_suffix(ext)
        return sanitize_path(unsanitized)

    @property
    def source_outputs(self) -> frozenset[Path]:
        """Image files produce a single image output."""
        return frozenset({self.img_path})
