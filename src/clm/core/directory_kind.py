import re
from abc import ABC, abstractmethod

from attr import frozen
from clm.utils.location import Location

NOTEBOOK_REGEX = re.compile(
    r"^(nb|lecture|topic|ws|workshop|project)_(.*)\.(py|cpp|ru|md)$"
)

# Constant for commonly used file kinds.
IGNORED_LABEL = "Ignored"
PLAIN_FILE_LABEL = "DataFile"
FOLDER_LABEL = "Folder"
NOTEBOOK_LABEL = "Notebook"
EXAMPLE_SOLUTION_LABEL = "ExampleSolution"
EXAMPLE_STARTER_KIT_LABEL = "ExampleStarterKit"


@frozen
class DirectoryKind(ABC):
    """A classifier for files and directories.

    Assigns a content label to files in this directory. The label is used
    to determine which data-source type to instantiate for this file."""

    @abstractmethod
    def label_for(self, file_or_dir: Location) -> str:
        """Classify a file or directory."""
        ...


@frozen
class IgnoredDirectory(DirectoryKind):
    """A directory that is ignored.

    Both files and subdirectories in this directory are ignored.
    """

    def label_for(self, file_or_dir: Location) -> str:
        return IGNORED_LABEL


@frozen
class GeneralDirectory(DirectoryKind):
    """A directory that has no special properties.

    Files in this directory are copied to the output directory without any
    processing.

    Subdirectories are processed recursively to discover more course materials.
    """

    def label_for(self, file_or_dir: Location) -> str:
        if file_or_dir.is_file():
            return PLAIN_FILE_LABEL
        else:
            return IGNORED_LABEL


directory_kind_registry: dict[str, type[DirectoryKind]] = {
    "IgnoredDirectory": IgnoredDirectory,
    "GeneralDirectory": GeneralDirectory,
}
