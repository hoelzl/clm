from attr import define

from clm.core.course import Course
from clm.core.document import Document
from clm.core.output import Output
from clm.core.output_spec import OutputSpec
from clm.outputs.folder_output import FolderOutput


@define
class Folder(Document):
    def process(self, course, output_spec: OutputSpec) -> Output:
        return FolderOutput(self)

    def get_target_name(self, course: "Course", output_spec: OutputSpec) -> str:
        return self.source_file.name
