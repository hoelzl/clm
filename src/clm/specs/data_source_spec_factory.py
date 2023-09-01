from pathlib import Path

from clm.core.course_layout import CourseLayout, PathClassifier
from clm.core.data_source_spec import DataSourceSpec
from clm.utils.path_utils import PathOrStr, ensure_relative_path


class DataSourceSpecFactory:
    def __init__(self, course_layout: CourseLayout, base_dir: Path):
        self.classifier = PathClassifier(course_layout)
        self.base_dir = base_dir

    def create_data_source_spec(
        self, source_file: Path, file_num: int
    ) -> "DataSourceSpec":
        classifier = self.classifier
        kind = classifier.classify(source_file)
        return DataSourceSpec(
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
