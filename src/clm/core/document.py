"""
A `Document` is a single file that can be processed into a complete output.
"""

# %%
import logging
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

from clm.utils.path import PathOrStr
from clm.core.output_spec import OutputSpec

# %%
if TYPE_CHECKING:
    # Make PyCharm happy, since it doesn't understand the pytest extensions to doctests.
    def getfixture(_name: str) -> Any:
        ...


# %%
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)


# %%
@dataclass
class Document(ABC):
    """Representation of a document existing as file."""

    source_path: Path
    target_dir_fragment: str

    @abstractmethod
    def process(self, output_spec: OutputSpec, target_path: PathOrStr):
        """Process the document and prepare for copying.

        We pass the path to which the document will later be copied, since some
        processors might want to incorporate parts of this path into the document
        (e.g., into the title slide of lectures).
        """
        ...

    @abstractmethod
    def copy_to_target(self, output_spec: OutputSpec, target_path: PathOrStr):
        """Copy the document to its destination."""


# %%
class Notebook(Document):
    def process(self, output_spec: OutputSpec, target_path: PathOrStr):
        pass

    def copy_to_target(self, output_spec: OutputSpec, target_path: PathOrStr):
        pass


# %%
class DataFile(Document):
    def process(self, output_spec: OutputSpec, target_path: PathOrStr):
        pass

    def copy_to_target(self, output_spec: OutputSpec, target_path: PathOrStr):
        target_path = (
            Path(target_path)
            / output_spec.target_dir_fragment
            / self.target_dir_fragment
            / self.source_path.name
        )
        print(f"Copying from {self.source_path} to {target_path}")
        target_path.parent.mkdir(exist_ok=True, parents=True)
        shutil.copy(
            self.source_path,
            target_path,
        )
