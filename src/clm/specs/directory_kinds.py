import re
from pathlib import Path

from clm.core.directory_kind import (
    DirectoryKind,
    DATA_FILE_KIND,
    EXAMPLE_SOLUTION_KIND,
    EXAMPLE_STARTER_KIT_KIND,
    FOLDER_KIND,
    IGNORED_KIND,
    NOTEBOOK_KIND,
)

NOTEBOOK_REGEX = re.compile(
    r'^(nb|lecture|topic|ws|workshop|project)_(.*)\.(py|cpp|ru|md|java)$'
)


class NotebookDirectory(DirectoryKind):
    """A directory that contains sources for Jupyter notebooks.

    Source files in this directory are converted to Jupyter notebooks if they
    conform to the naming convention for notebooks.

    Other files are copied to the output directory without any processing.

    Directories are ignored.
    """

    def classify(self, file_or_dir: Path) -> str:
        if file_or_dir.is_file():
            name = file_or_dir.name
            if re.match(NOTEBOOK_REGEX, name):
                return NOTEBOOK_KIND
            else:
                return DATA_FILE_KIND
        return IGNORED_KIND


_STARTER_KIT_PATTERN: re.Pattern[str] = re.compile(
    r'.*(sk|starter_?kit)$', re.IGNORECASE
)


class ExampleDirectory(DirectoryKind):
    """A directory that contains sources for examples.

    Subdirectories in this directory are classified either as
    ExampleStarterKit or ExampleSolution.
    """

    def classify(self, file_or_dir: Path) -> str:
        if file_or_dir.is_dir():
            if re.match(_STARTER_KIT_PATTERN, file_or_dir.name):
                return EXAMPLE_STARTER_KIT_KIND
            else:
                return EXAMPLE_SOLUTION_KIND
        elif file_or_dir.is_file():
            return DATA_FILE_KIND
        else:
            return IGNORED_KIND


class LegacyExampleDirectory(DirectoryKind):
    """A directory that contains sources for examples.

    Subdirectories in this directory are always classified as Example.
    """

    def classify(self, file_or_dir: Path) -> str | None:
        if file_or_dir.is_dir():
            return FOLDER_KIND
        elif file_or_dir.is_file():
            return DATA_FILE_KIND
        else:
            return IGNORED_KIND
