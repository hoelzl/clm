"""
The `OutputKind` is passed to the document when it is processed to determine the
output that should be generated.

## Classes

- `OutputKind`: The abstract base class of all output types.
- `PublicOutput`: The base class for artefacts that should be publicly available.
- `CompletedOutput`: The output type for artefacts that contain all public contents.
- `CodeAlongOutput`: The output type for artefacts meant for live coding or workshops.
- `SpeakerOutput`: Private outputs that are for the speaker/trainer.
"""

# %%
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from clm.utils.jupytext import Cell, get_cell_type, get_tags, set_tags

# %%
# Make PyCharm happy, since it doesn't understand the pytest extensions to doctests.
if TYPE_CHECKING:

    def getfixture(_name: str) -> Any:
        ...


# %%
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)


# %%
@dataclass
class OutputSpec(ABC):
    """Description of the kind of output that should be contained for a document.

    Outputs can either be public or private.  In public outputs some data is not
    included, e.g., speaker notes. Private outputs can potentially contain all
    data.

    ## Methods:

    - `is_cell_included()`: Returns whether a cell should be included in the
      output.
    - `is_cell_contents_included()`: Returns whether the contents of a cell
      should be included or cleared.
    - `target_dir_fragment()`: A path fragment that can be used to place
      different outputs into a folder hierarchy

    ## Properties:

    - `is_public`: Is the output meant for public consumption?
    - `is_private`: Is the output for the lecturer only?
    """

    lang: str = "en"
    """The desired language of the output."""

    target_dir_fragment: str = ""
    """A string that may be inserted in the output path"""

    @property
    @abstractmethod
    def is_public(self) -> bool:
        """Return whether the document is public or private."""
        ...

    @property
    def is_private(self) -> bool:
        """Return whether the document is private or public.

        >>> from conftest import concrete_instance_of
        >>> ok = concrete_instance_of(OutputSpec)

        >>> ok.is_public != ok.is_private
        True
        """
        return not self.is_public

    @classmethod
    def is_cell_included(cls, cell: Cell) -> bool:
        """Return whether the cell should be included or completely removed.

        If this method returns false the complete cell is removed from the
        output. This is used to, e.g., remove speaker notes or alternate
        solutions from public outputs.

        The default implementation of this method returns true.

        >>> from conftest import concrete_instance_of
        >>> ok = concrete_instance_of(OutputSpec, "is_cell_included")

        >>> cell = getfixture("code_cell")
        >>> ok.is_cell_included(cell)
        True
        """
        return True

    def is_cell_contents_included(self, cell: Cell) -> bool:
        """Return whether the cell contents should be included or cleared.

        If this method returns false the contents of the cell is cleared, the
        cell itself is still included. This is used to, e.g., remove code from
        most code cells in codealong notebooks.

        The default implementation of this method returns true for all cells.

        If this method is overridden, `self.are_any_cell_contents_cleared` has
        to be overridden as well to return true.

        >>> from conftest import concrete_instance_of
        >>> ok = concrete_instance_of(OutputSpec, "is_cell_contents_included")

        >>> markdown_cell = getfixture("markdown_cell")
        >>> ok.is_cell_contents_included(markdown_cell)
        True

        >>> code_cell = getfixture("code_cell")
        >>> ok.is_cell_contents_included(code_cell)
        True
        """
        return True

    @property
    def are_any_cell_contents_cleared(self) -> bool:
        """Return whether the contents of any type of cell is cleared.

        This is false by default; it is true for outputs such as codealong
        notebooks where most code cells are cleared.

        >>> from conftest import concrete_instance_of
        >>> ok = concrete_instance_of(OutputSpec, "are_any_cell_contents_cleared")

        >>> ok.are_any_cell_contents_cleared
        False
        """
        return False


# %%
@dataclass
class PublicOutput(OutputSpec):
    """Superclass for output specs for documents shared with the public."""

    @property
    def is_public(self) -> bool:
        """Always returns true."""
        return True


# %%
@dataclass
class CompletedOutput(PublicOutput):
    """Output spec for documents containing all data shared with the public.

    This means they contain everything except speaker notes.
    """


# %%
@dataclass
class CodeAlongOutput(PublicOutput):
    """Output spec for public documents that can be completed during the course.

    Only code cells marked with the "keep" tag have contents in them, all other
    code cells are empty.
    """

    code_tags_to_keep = {"keep"}

    def is_cell_contents_included(self, cell: Cell) -> bool:
        """Return whether the cell contents should be included or cleared.

        Returns true for non-code cells and for code cells marked with the
        `keep` tag.

        >>> ok = CodeAlongOutput()

        >>> markdown_cell = getfixture("markdown_cell")
        >>> ok.is_cell_contents_included(markdown_cell)
        True

        >>> code_cell = getfixture("code_cell")
        >>> ok.is_cell_contents_included(code_cell)
        False

        >>> set_tags(code_cell, ["keep"])
        >>> ok.is_cell_contents_included(code_cell)
        True
        """
        if get_cell_type(cell) == "code":
            return len(self.code_tags_to_keep & set(get_tags(cell))) != 0
        else:
            return True

    @property
    def are_any_cell_contents_cleared(self) -> bool:
        """Return true, since some cells are cleared.

        >>> ok = CodeAlongOutput()
        >>> ok.are_any_cell_contents_cleared
        True
        """
        return True


# %%
@dataclass
class SpeakerOutput(OutputSpec):
    """Output spec for documents containing all public and private data."""

    @property
    def is_public(self) -> bool:
        """Return false since this is a private document."""
        return False
