from pathlib import Path

from clm.core.document_spec import DocumentSpec
from clm.specs.course_layouts import legacy_python_course_layout
from clm.utils.path_utils import PathOrStr, ensure_relative_path


class DocumentSpecFactory:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir

    def create_document_spec(
        self, source_file: Path, file_num: int
    ) -> 'DocumentSpec':
        classifier = legacy_python_course_layout(self.base_dir)
        kind = classifier.classify(source_file)
        return DocumentSpec(
            ensure_relative_path(source_file, self.base_dir).as_posix(),
            default_path_fragment(source_file),
            kind,
            file_num,
        )


def default_path_fragment(path: PathOrStr) -> str:
    path = Path(path)
    if 'metadata' in path.parts:
        return '$root'
    return '-'
