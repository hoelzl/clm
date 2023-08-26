from pathlib import Path

from clm.core.course_layout import CourseLayout
from clm.core.document_spec import DocumentSpec
from clm.utils.path_utils import PathOrStr, ensure_relative_path


class DocumentSpecFactory:
    def __init__(self, course_layout: CourseLayout, base_dir: Path):
        self.course_layout = course_layout
        self.base_dir = base_dir

    def create_document_spec(self, source_file: Path, file_num: int) -> "DocumentSpec":
        layout = self.course_layout
        kind = layout.classify(source_file)
        return DocumentSpec(
            ensure_relative_path(source_file, self.base_dir).as_posix(),
            default_path_fragment(source_file),
            kind,
            file_num,
        )


def default_path_fragment(path: PathOrStr) -> str:
    path = Path(path)
    if "metadata" in path.parts:
        return "$root"
    return "-"
