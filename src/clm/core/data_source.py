"""
A `DataSource` is a file that can be processed into an output.
"""

import logging
from abc import ABC, abstractmethod
from attr import define
from pathlib import Path
from typing import TYPE_CHECKING

from clm.core.data_sink import DataSink
from clm.core.output_spec import OutputSpec

if TYPE_CHECKING:
    from clm.core.course import Course


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)


@define(init=False)
class DataSource(ABC):
    """Representation of a data source existing as file."""

    source_file: Path
    target_dir_fragment: str
    prog_lang: str
    file_num: int

    def __init__(
        self,
        source_file: Path | str,
        target_dir_fragment: str,
        prog_lang: str,
        file_num: int,
    ):
        super().__init__()

        if not isinstance(source_file, Path):
            self.source_file = Path(self.source_file)
        else:
            self.source_file = source_file

        if not self.source_file.is_absolute():
            raise ValueError("Source file for a course must be absolute.")

        self.target_dir_fragment = target_dir_fragment
        self.prog_lang = prog_lang
        self.file_num = file_num

    @abstractmethod
    def process(self, course: "Course", output_spec: OutputSpec) -> DataSink:
        """Process the data source and prepare for copying.

        The output spec determines details of the processing, e.g., whether solutions
        for exercises should be included.
        """
        ...

    @abstractmethod
    def get_target_name(self, course: "Course", output_spec: OutputSpec) -> str:
        """Return the name of the data source in the target directory."""
        ...
