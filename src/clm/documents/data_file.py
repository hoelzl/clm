from dataclasses import dataclass

from clm.core.course import Course
from clm.core.document import Document
from clm.core.output import Output
from clm.core.output_spec import OutputSpec
from clm.outputs.data_file_output import DataFileOutput


@dataclass
class DataFile(Document):
    def process(self, course, output_spec: OutputSpec) -> Output:
        return DataFileOutput(self)

    def get_target_name(self, course: "Course", output_spec: OutputSpec) -> str:
        return self.source_file.name
