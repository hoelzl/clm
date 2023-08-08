import re
from abc import ABC, abstractmethod
from pathlib import Path, PurePath

NOTEBOOK_REGEX = re.compile(
    r'^(nb|lecture|topic|ws|workshop|project)_(.*)\.(py|cpp|ru|md)$'
)

# Constant for commonly used file kinds.
IGNORED_KIND = 'Ignored'
DATA_FILE_KIND = 'DataFile'
FOLDER_KIND = 'Folder'
NOTEBOOK_KIND = 'Notebook'
EXAMPLE_SOLUTION_KIND = 'ExampleSolution'
EXAMPLE_STARTER_KIT_KIND = 'ExampleStarterKit'


class DirectoryKind(ABC):
    """A classifier for files and directories.

    Assigns a content label to files in this directory. The label is used
    to determine which document type to instantiate for this file."""

    def __repr__(self):
        # return f'{self.__class__.__name__}({self.path})'
        return f'{self.__class__.__name__}()'

    def __eq__(self, other):
        # Check actual types, not subclasses.
        # pylint: disable=unidiomatic-typecheck
        return type(other) is type(self)

    @abstractmethod
    def classify(self, file_or_dir: Path) -> str:
        """Classify a file or directory."""
        ...


class IgnoredDirectory(DirectoryKind):
    """A directory that is ignored.

    Both files and subdirectories in this directory are ignored.
    """

    def classify(self, file_or_dir: Path) -> str:
        return IGNORED_KIND


class GeneralDirectory(DirectoryKind):
    """A directory that has no special properties.

    Files in this directory are copied to the output directory without any
    processing.

    Subdirectories are processed recursively to discover more course materials.
    """

    def classify(self, file_or_dir: Path) -> str:
        if file_or_dir.is_file():
            return DATA_FILE_KIND
        else:
            return IGNORED_KIND
