"""Base class for course files that generate images."""

from pathlib import Path

from attrs import define

from clx.core.course_file import CourseFile


@define
class ImageFile(CourseFile):
    """Base class for files that convert to images (PNG format).

    This class provides common functionality for diagram files that
    generate PNG images, such as PlantUML and Draw.io files.
    """

    @property
    def img_path(self) -> Path:
        """Path to the generated PNG image.

        Images are generated in an 'img' subdirectory relative to the
        file's parent directory, with the same stem but .png extension.
        """
        from clx.core.utils.text_utils import sanitize_path

        unsanitized = (self.path.parents[1] / "img" / self.path.stem).with_suffix(
            ".png"
        )
        return sanitize_path(unsanitized)

    @property
    def source_outputs(self) -> frozenset[Path]:
        """Image files produce a single PNG output."""
        return frozenset({self.img_path})
