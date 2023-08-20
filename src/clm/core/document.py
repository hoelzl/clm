"""
A `Document` is a single file that can be processed into a complete output.
"""

# %%
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from clm.core.output_spec import OutputSpec

# %%
if TYPE_CHECKING:
    from clm.core.course import Course


# %%
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)


# %%
@dataclass
class Document(ABC):
    """Representation of a document existing as file."""

    source_file: Path
    target_dir_fragment: str
    prog_lang: str
    file_num: int

    def __post_init__(self):
        super().__init__()
        if not isinstance(self.source_file, Path):
            self.source_file = Path(self.source_file)
        if not self.source_file.is_absolute():
            raise ValueError("Source file for a course must be absolute.")

    @abstractmethod
    def process(self, course, output_spec: OutputSpec):
        """Process the document and prepare for copying.

        We pass the path to which the document will later be copied, since some
        processors might want to incorporate parts of this path into the document
        (e.g., into the title slide of lectures).
        """
        ...

    @abstractmethod
    def get_target_name(self, course: "Course", output_spec: OutputSpec):
        ...

    def get_full_target_path(self, course: "Course", output_spec: OutputSpec):
        target_base_path = course.target_dir
        if not target_base_path.is_absolute():
            raise ValueError(f"Base path {target_base_path} is not absolute.")

        if self._is_special_target_dir_fragment(self.target_dir_fragment):
            return self._process_special_target_dir(course, output_spec)
        else:
            return (
                target_base_path
                / output_spec.target_dir_fragment
                / self.target_dir_fragment
                / self.get_target_name(course, output_spec)
            )

    @abstractmethod
    def copy_to_target(self, course: "Course", output_spec: OutputSpec):
        """Copy the document to its destination."""

    @staticmethod
    def _is_special_target_dir_fragment(target_dir_fragment: str):
        """Checks whether a target dir fragment needs special processing.
        >>> Document._is_special_target_dir_fragment("$root")
        True
        >>> Document._is_special_target_dir_fragment("Base")
        False
        """
        return target_dir_fragment.startswith("$")

    def _process_special_target_dir(self, course: "Course", output_spec: OutputSpec):
        match self.target_dir_fragment:
            case "$keep":
                relative_source_path = self.source_file.relative_to(course.source_dir)
                result_path = (
                    course.target_dir
                    / output_spec.target_root_fragment
                    / relative_source_path
                )
                return result_path
            case "$parent":
                relative_source_path = self.source_file.relative_to(course.source_dir)
                result_path = (
                    course.target_dir
                    / output_spec.target_root_fragment
                    / "/".join(relative_source_path.parts[1:])
                )
                return result_path
            case "$root":
                return (
                    course.target_dir
                    / output_spec.target_root_fragment
                    / self.get_target_name(course, output_spec)
                )
            case "$target":
                return (
                    course.target_dir
                    / output_spec.target_root_fragment
                    / output_spec.target_subdir_fragment
                    / self.get_target_name(course, output_spec)
                )
        raise ValueError(f"Unknown special target dir: {self.target_dir_fragment}")
