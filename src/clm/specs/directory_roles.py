import re
from pathlib import Path

from clm.core.directory_role import DirectoryRole


NOTEBOOK_REGEX = re.compile(
    r'^(nb|lecture|topic|ws|workshop|project)_(.*)\.(py|cpp|ru|md|java)$'
)


class NotebookDirectory(DirectoryRole):
    """A directory that contains sources for Jupyter notebooks.

    Source files in this directory are converted to Jupyter notebooks if they
    conform to the naming convention for notebooks.

    Other files are copied to the output directory without any processing.
    """

    def classify(self, path: Path) -> str | None:
        if path.is_file():
            name = path.name
            if re.match(NOTEBOOK_REGEX, name):
                return 'Notebook'
        return None


_STARTER_KIT_PATTERN: re.Pattern[str] = re.compile(
    r'.*(sk|starter_?kit)$', re.IGNORECASE
)


class ExampleDirectory(DirectoryRole):
    """A directory that contains sources for examples.

    Subdirectories in this directory are classified either as
    ExampleStarterKit or ExampleSolution.
    """

    def classify(self, path: Path) -> str | None:
        if path.is_dir():
            if re.match(_STARTER_KIT_PATTERN, path.name):
                return 'ExampleStarterKit'
            else:
                return 'ExampleSolution'
        else:
            return None

    def process_subdirectory(self, path: Path) -> bool:
        return False


class LegacyExampleDirectory(DirectoryRole):
    """A directory that contains sources for examples.

    Subdirectories in this directory are always classified as Example.
    """

    def classify(self, path: Path) -> str | None:
        if path.is_dir():
            return 'Example'
        else:
            return None

    def process_subdirectory(self, path: Path) -> bool:
        return False


class ExampleSolution(DirectoryRole):
    """A directory that contains a solution for an example.

    This directory is copied verbatim to the output directory containing
    example solutions. Therefore, no files or subdirectories are processed.
    """

    def classify(self, path: Path) -> str | None:
        return None

    def process_subdirectory(self, path: Path) -> bool:
        return False


class ExampleStarterKit(DirectoryRole):
    """A directory that contains a starter kit for an example.

    This directory is copied verbatim to the output directory containing
    example starter kits. Therefore, no files or subdirectories are processed.
    """

    def classify(self, path: Path) -> str | None:
        return None

    def process_subdirectory(self, path: Path) -> bool:
        return False
