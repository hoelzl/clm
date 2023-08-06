import re
from pathlib import Path

from clm.core.directory_role import DirectoryRole


class NotebookDirectory(DirectoryRole):
    """A directory that contains sources for Jupyter notebooks.

    Source files in this directory are converted to Jupyter notebooks if they
    conform to the naming convention for notebooks.

    Other files are copied to the output directory without any processing.
    """

    def classify(self, path: Path) -> str | None:
        raise NotImplementedError(
            'TODO: Implement NotebookDirectory.classify()'
        )


_STARTER_KIT_PATTERN = re.compile(r'(sk|starter_?kit)$', re.IGNORECASE)


class ExampleDirectory(DirectoryRole):
    """A directory that contains sources for examples.

    Subdirectories in this directory are classified either as ExampleStarterKit or
    ExampleSolution.
    """

    def classify(self, path: Path) -> str | None:
        if path.is_dir():
            if path.name.matches(_STARTER_KIT_PATTERN):
                return 'ExampleStarterKit'
            else:
                return 'ExampleSolution'
        else:
            return None

    def process_subdirectory(self, path: Path) -> bool:
        return False


class ExampleSolution(DirectoryRole):
    """A directory that contains a solution for an example.

    This directory is copied verbatim to the output directory containing example
    solutions. Therefore, no files or subdirectories are processed.
    """

    def classify(self, path: Path) -> str | None:
        return None

    def process_subdirectory(self, path: Path) -> bool:
        return False


class ExampleStarterKit(DirectoryRole):
    """A directory that contains a starter kit for an example.

    This directory is copied verbatim to the output directory containing example
    starter kits. Therefore, no files or subdirectories are processed.
    """

    def classify(self, path: Path) -> str | None:
        return None

    def process_subdirectory(self, path: Path) -> bool:
        return False
