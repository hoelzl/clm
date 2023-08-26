import logging
import shutil
from attr import define
from typing import TYPE_CHECKING

from clm.core.course import Course
from clm.core.document_paths import full_target_path_for_document
from clm.core.output import Output
from clm.core.output_spec import OutputSpec

if TYPE_CHECKING:
    from clm.documents.data_file import DataFile


@define
class DataFileOutput(Output):
    doc: "DataFile"

    def write_to_target(self, course: Course, output_spec: OutputSpec) -> None:
        target_path = full_target_path_for_document(
            self.doc, course=course, output_spec=output_spec
        )
        logging.info(
            f"Copying file {self.doc.source_file.as_posix()!r} "
            f"to {target_path.as_posix()!r}."
        )
        target_path.parent.mkdir(exist_ok=True, parents=True)
        shutil.copy(self.doc.source_file, target_path)
