from pathlib import Path

from clm.core.course_spec import CourseSpec
from clm.core.document import Document
from clm.core.document_spec import DocumentSpec
from clm.documents.data_file import DataFile
from clm.documents.folder import Folder
from clm.documents.notebook import Notebook


def document_from_spec(
    course_spec: CourseSpec, document_spec: DocumentSpec
) -> "Document":
    """Return the document for this spec."""

    document_type: type[Document] = DOCUMENT_TYPES[document_spec.label]
    source_file = Path(document_spec.source_file)
    prog_lang = course_spec.prog_lang
    if not source_file.is_absolute():
        source_file = course_spec.base_dir / source_file
    # noinspection PyArgumentList
    return document_type(
        source_file=source_file,
        target_dir_fragment=document_spec.target_dir_fragment,
        prog_lang=prog_lang,
        file_num=document_spec.file_num,
    )


DOCUMENT_TYPES = {
    "Notebook": Notebook,
    "DataFile": DataFile,
    "Folder": Folder,
}
