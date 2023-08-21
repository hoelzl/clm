"""
A `Document` is a single file that can be processed into a complete output.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from clm.core.output import Output
from clm.core.output_spec import OutputSpec

if TYPE_CHECKING:
    from clm.core.course import Course


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)


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
    def process(self, course: "Course", output_spec: OutputSpec) -> Output:
        """Process the document and prepare for copying.

        The output spec determines details of the processing, e.g., whether solutions
        for exercises should be included.
        """
        ...

    @abstractmethod
    def get_target_name(self, course: "Course", output_spec: OutputSpec) -> str:
        """Return the name of the document in the target directory."""
        ...
