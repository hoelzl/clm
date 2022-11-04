"""
The `OutputKind` is passed to the document when it is processed to determine the
output that should be generated.

## Classes

- `OutputSpec`: The abstract base class of all output types.
- `CompletedOutput`: The output type for artefacts that contain all public contents.
- `CodeAlongOutput`: The output type for artefacts meant for live coding or workshops.
- `SpeakerOutput`: Private outputs that are for the speaker/trainer.
"""

# %%
import logging
import re
from abc import ABC
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from clm.utils.jupyter_utils import (
    Cell,
    get_tags,
    is_cell_contents_included_in_codealongs,
    is_cell_included_for_language,
)

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

    ## Static Methods:
    - `create`: Factory method to create a spec.

    ## Methods:

    - `is_cell_included()`: Returns whether a cell should be included in the output.
    - `is_cell_contents_included()`: Returns whether the contents of a cell should be
      included or cleared.

    ## Properties:

    - `file_suffix`: The suffix of a file generated with this spec. Derived from the
      notebook format.

    ## Attributes:
    - `lang`: The language of the output document.
    - `notebook_format`: The format in which notebooks should be output.
    - `target_dir_fragment`: A directory fragment that can be inserted into the output
      path.
    - `tags_to_delete_cell`: If any of these tags is on a cell it is completely deleted
      from the output.
    """

    lang: str = "en"
    """The desired language of the output."""

    target_dir_fragment: str = ""
    """A string that may be inserted in the output path"""

    notebook_format: str = "ipynb"
    """The output format for notebooks. Ignored by other file types."""

    tags_to_delete_cell = {"del", "start"}
    """Tags that cause the whole cell to be deleted."""

    _suffix_re = re.compile(r"([^:]*)(:.*)?")
    """Regular expression to extract the file extension from a jupytext format."""

    @staticmethod
    def create(spec_name: str, *args, **kwargs):
        """Create a spec given a name and init data.

        >>> OutputSpec.create("completed", "de", "De", "py")
        CompletedOutput(lang='de', target_dir_fragment='De', notebook_format='py')
        >>> OutputSpec.create("CodeAlong")
        CodeAlongOutput(lang='en', target_dir_fragment='', notebook_format='ipynb')
        >>> OutputSpec.create('speaker')
        SpeakerOutput(lang='en', target_dir_fragment='', notebook_format='ipynb')
        >>> OutputSpec.create('MySpecialSpec')
        Traceback (most recent call last):
        ...
        ValueError: Unknown spec type: 'MySpecialSpec'.
        Valid spec types are 'completed', 'codealong' or 'speaker'.
        """
        match spec_name.lower():
            case "completed":
                spec_type = CompletedOutput
            case "codealong":
                spec_type = CodeAlongOutput
            case "speaker":
                spec_type = SpeakerOutput
            case _:
                raise ValueError(
                    f"Unknown spec type: {spec_name!r}.\n"
                    "Valid spec types are 'completed', 'codealong' or 'speaker'."
                )
        return spec_type(*args, **kwargs)

    @property
    def file_suffix(self):
        """Return the file suffix for the spec's notebook format.

        >>> os = OutputSpec.create("speaker", notebook_format="ipynb")
        >>> os.file_suffix
        'ipynb'

        >>> os = OutputSpec.create("speaker", notebook_format="py:percent")
        >>> os.file_suffix
        'py'
        """
        suffix = self._suffix_re.match(self.notebook_format)[1]
        if not suffix:
            raise ValueError(
                f"Could not extract file suffix from format {self.notebook_format}."
            )
        return suffix

    def is_cell_included(self, cell: Cell) -> bool:
        """Return whether the cell should be included or completely removed.

        If this method returns false the complete cell is removed from the
        output. This is used to, e.g., remove speaker notes or alternate
        solutions from public outputs.
        """
        if self.tags_to_delete_cell.intersection(get_tags(cell)):
            return False
        return is_cell_included_for_language(cell, self.lang)

    def is_cell_contents_included(self, cell: Cell) -> bool:
        """Return whether the cell contents should be included or cleared.

        If this method returns false the contents of the cell is cleared, the
        cell itself is still included. This is used to, e.g., remove code from
        most code cells in codealong notebooks.

        The default implementation of this method returns true for all cells.

        If this method is overridden, `self.are_any_cell_contents_cleared` has
        to be overridden as well to return true.

        >>> from conftest import concrete_instance_of
        >>> os = concrete_instance_of(OutputSpec, "is_cell_contents_included")

        >>> markdown_cell = getfixture("markdown_cell")
        >>> os.is_cell_contents_included(markdown_cell)
        True

        >>> code_cell = getfixture("code_cell")
        >>> os.is_cell_contents_included(code_cell)
        True
        """
        return True


# %%
@dataclass
class CompletedOutput(OutputSpec):
    """Output spec for documents containing all data shared with the public.

    This means they contain everything except speaker notes.
    """

    tags_to_delete_cell = {"del", "notes", "start"}
    """Tags that cause the whole cell to be deleted."""


# %%
@dataclass
class CodeAlongOutput(OutputSpec):
    """Output spec for public documents that can be completed during the course.

    Only code cells marked with the "keep" tag have contents in them, all other
    code cells are empty.
    """

    tags_to_delete_cell = {"alt", "del", "notes"}
    """Tags that cause the whole cell to be deleted."""

    code_tags_to_keep = {"keep", "start"}
    """Tags that cause the contents of code cells to be retained."""

    def is_cell_contents_included(self, cell: Cell) -> bool:
        """Return whether the cell contents should be included or cleared.

        Returns true for non-code cells and for code cells marked with the
        `keep` tag.

        >>> os = CodeAlongOutput()
        >>> os.is_cell_contents_included(getfixture("kept_cell"))
        True
        >>> os.is_cell_contents_included(getfixture("markdown_cell"))
        True
        >>> os.is_cell_contents_included(getfixture("code_cell"))
        False
        """
        is_included_by_super = super().is_cell_contents_included(cell)
        return is_included_by_super and is_cell_contents_included_in_codealongs(cell)


# %%
@dataclass
class SpeakerOutput(OutputSpec):
    """Output spec for documents containing all public and private data."""

    tags_to_delete_cell = {"del", "start"}
    """Tags that cause the whole cell to be deleted."""
