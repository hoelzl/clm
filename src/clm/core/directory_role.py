from abc import ABC, abstractmethod
from pathlib import Path


class DirectoryRole(ABC):
    """The role of the directory in the course structure.

    This determines how files in the directory are processed and how they are copied to
    the output directory."""

    def __repr__(self):
        return f'{self.__class__.__name__}()'

    @abstractmethod
    def classify(self, path: Path) -> str | None:
        """Classify a path as belonging to this directory role.

        Args:
            path: The path to classify.

        Returns:
            The document type of the path, or None if the path does not represent a
            document to be included in a course spec.
        """
        return None

    def process_subdirectory(self, path: Path) -> bool:
        return True


class GeneralDirectory(DirectoryRole):
    """A directory that has no special properties.

    Files in this directory are copied to the output directory without any processing.

    Subdirectories are processed recursively to discover more course materials.
    """

    def classify(self, path: Path) -> str | None:
        if path.is_file():
            return 'DataFile'
        return None
