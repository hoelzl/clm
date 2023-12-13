import re

from attr import frozen

from clm.core.course_layout import CourseLayout
from clm.core.directory_kind import (
    PLAIN_FILE_LABEL,
    DirectoryKind,
    EXAMPLE_SOLUTION_LABEL,
    EXAMPLE_STARTER_KIT_LABEL,
    FOLDER_LABEL,
    IGNORED_LABEL,
    NOTEBOOK_LABEL,
    directory_kind_registry,
)
from clm.utils.location import Location


@frozen
class NotebookDirectory(DirectoryKind):
    """A directory that contains sources for Jupyter notebooks.

    Source files in this directory are converted to Jupyter notebooks if they
    conform to the naming convention for notebooks.

    Other files are copied to the output directory without any processing.

    Directories are ignored.
    """

    notebook_regex: re.Pattern

    @classmethod
    def from_course_layout(cls, course_layout: CourseLayout):
        return cls(course_layout.notebook_regex)

    def label_for(self, file_or_dir: Location) -> str:
        if file_or_dir.is_file():
            name = file_or_dir.name
            if re.match(self.notebook_regex, name):
                return NOTEBOOK_LABEL
            elif file_or_dir.suffix == ".ipynb":
                return NOTEBOOK_LABEL
            else:
                return PLAIN_FILE_LABEL
        elif file_or_dir.is_dir():
            return FOLDER_LABEL
        return IGNORED_LABEL


directory_kind_registry["NotebookDirectory"] = NotebookDirectory


_STARTER_KIT_PATTERN: re.Pattern[str] = re.compile(
    r".*(sk|starter_?kit)$", re.IGNORECASE
)


@frozen
class ExampleDirectory(DirectoryKind):
    """A directory that contains sources for examples.

    Subdirectories in this directory are classified either as
    ExampleStarterKit or ExampleSolution.
    """

    def label_for(self, file_or_dir: Location) -> str:
        if file_or_dir.is_dir():
            if re.match(_STARTER_KIT_PATTERN, file_or_dir.name):
                return EXAMPLE_STARTER_KIT_LABEL
            else:
                return EXAMPLE_SOLUTION_LABEL
        elif file_or_dir.is_file():
            return PLAIN_FILE_LABEL
        else:
            return IGNORED_LABEL


directory_kind_registry["ExampleDirectory"] = ExampleDirectory


@frozen
class LegacyExampleDirectory(DirectoryKind):
    """A directory that contains sources for examples.

    Subdirectories in this directory are always classified as Example.
    """

    def label_for(self, file_or_dir: Location) -> str | None:
        if file_or_dir.is_dir():
            return FOLDER_LABEL
        elif file_or_dir.is_file():
            return PLAIN_FILE_LABEL
        else:
            return IGNORED_LABEL


directory_kind_registry["LegacyExampleDirectory"] = LegacyExampleDirectory
