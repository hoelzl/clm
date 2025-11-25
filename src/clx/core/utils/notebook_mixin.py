"""Mixin for classes that contain notebook files."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from clx.core.course_files.notebook_file import NotebookFile


class NotebookMixin:
    """Mixin for classes that contain notebook files.

    Classes using this mixin must have a `files` property that returns
    a list of CourseFile objects.
    """

    # Declare that subclasses must provide a files property
    files: Any

    @property
    def notebooks(self) -> list["NotebookFile"]:
        """Return all notebook files from the files property."""
        from clx.core.course_files.notebook_file import NotebookFile

        return [file for file in self.files if isinstance(file, NotebookFile)]
