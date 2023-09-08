from pathlib import Path, PurePath

from clm.core.course_layout import CourseLayout, PathClassifier
from clm.core.data_source_spec import DataSourceSpec
from clm.utils.location import Location, FileSystemLocation
from clm.utils.path_utils import PathOrStr, ensure_relative_path


class DataSourceSpecFactory:
    def __init__(
        self,
        course_layout: CourseLayout,
        base_loc: Location,
        location_type: type[Location] = FileSystemLocation,
    ):
        self.classifier = PathClassifier(course_layout)
        self.base_loc = base_loc
        self.location_type = location_type

    def create_data_source_spec(
        self, source_file: Location, file_num: int
    ) -> "DataSourceSpec":
        classifier = self.classifier
        kind = classifier.classify(source_file)
        # noinspection PyArgumentList
        return DataSourceSpec(
            self.location_type(base_dir=self.base_loc, relative_path=source_file),
            default_path_fragment(source_file),
            kind,
            file_num,
        )


def default_path_fragment(path: PathOrStr) -> str:
    path = Path(path)
    if "metadata" in path.parts:
        return "$root"
    return "-"
