from clm.core.course_layout import CourseLayout, PathClassifier
from clm.core.data_source_spec import DataSourceSpec
from clm.utils.location import Location, FileSystemLocation


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
            source_file,
            default_path_fragment(source_file),
            kind,
            file_num,
        )


def default_path_fragment(loc: Location) -> str:
    if "metadata" in loc.parts:
        return "$root"
    # Hack for C++ courses
    elif "code" in loc.parts:
        return "$keep"
    return "-"
