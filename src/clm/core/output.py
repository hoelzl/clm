from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clm.core.course import Course
from clm.core.output_spec import OutputSpec


@dataclass
class Output(ABC):
    @abstractmethod
    def write_to_target(self, course: "Course", output_spec: OutputSpec) -> None:
        """Copy the document to its destination."""
