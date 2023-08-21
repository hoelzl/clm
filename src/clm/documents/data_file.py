import logging
import shutil
from dataclasses import dataclass

from clm.core.course import Course
from clm.core.document import Document
from clm.core.output_spec import OutputSpec


@dataclass
class DataFile(Document):
    def process(self, course, output_spec: OutputSpec):
        pass

    def get_target_name(self, course: "Course", output_spec: OutputSpec) -> str:
        return self.source_file.name

    def write_to_target(self, course, output_spec: OutputSpec):
        target_path = self.get_full_target_path(course=course, output_spec=output_spec)
        logging.info(
            f"Copying file {self.source_file.as_posix()!r} "
            f"to {target_path.as_posix()!r}."
        )
        target_path.parent.mkdir(exist_ok=True, parents=True)
        shutil.copy(self.source_file, target_path)
