from abc import ABC
from pathlib import Path

from attrs import frozen

from clx.course_file import CourseFile
from clx.operation import Operation


@frozen
class ConvertFileOperation(Operation, ABC):
    input_file: "CourseFile"
    output_file: Path
