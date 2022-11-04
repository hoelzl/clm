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
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from clm.utils.jupyter_utils import (
    Cell,
    is_alternate_solution,
    is_cell_contents_included_in_codealongs,
    is_deleted_cell,
    is_public_cell,
    is_starting_cell,
    should_cell_be_retained_for_language,
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
    - `are_any_cell_contents_cleared()`: Returns whether the contents of any cell is
      cleared by this spec.
    - `should_cell_be_retained()`: Returns whether a given cell should be retained.

    ## Properties:

    - `is_public`: Is the output meant for public consumption?
    - `is_private`: Is the output for the lecturer only?

    ## Attributes:
    - `lang`: The language of the output document.
    - `notebook_format`: The format in which notebooks should be output.
    - `target_dir_fragment`: A directory fragment that can be inserted into the output
      path.
    """

    lang: str = "en"
    """The desired language of the output."""

    target_dir_fragment: str = ""
    """A string that may be inserted in the output path"""

    notebook_format: str = "ipynb"
    """The output format for notebooks. Ignored by other file types."""

    suffix_re = re.compile(r"([^:]*)(:.*)?")
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
        suffix = self.suffix_re.match(self.notebook_format)[1]
        if not suffix:
            raise ValueError(
                f"Could not extract file suffix from format {self.notebook_format}."
            )
        return suffix

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

    def should_cell_be_retained(self, cell: Cell):
        """Return whether a cell should be retained in an output document.

        Subclasses may override this to remove more cells. Any cell for which the
        default implementation returns a false value should also return false in any
        subclass.

        <<< from conftest import concrete_instance_of
        <<< ok = concrete_instance_of(OutputSpec, "should_cell_be_retained")
        <<< ok.should_cell_be_retained(getfixture("code_cell"))
        True
        <<< ok.should_cell_be_retained(getfixture("alternate_cell"))
        True
        <<< ok.should_cell_be_retained(getfixture("markdown_notes_cell"))
        True
        <<< ok.should_cell_be_retained(getfixture("english_markdown_cell"))
        True
        <<< ok.should_cell_be_retained(getfixture("starting_cell"))
        True
        <<< ok.should_cell_be_retained(getfixture("german_markdown_cell"))
        False
        <<< ok.should_cell_be_retained(getfixture("deleted_cell"))
        False
        """
        if is_deleted_cell(cell):
            return False
        return should_cell_be_retained_for_language(cell, self.lang)


# %%
@dataclass
class PublicOutput(OutputSpec, ABC):
    """Superclass for output specs for documents shared with the public."""

    def should_cell_be_retained(self, cell: Cell):
        """Retrun whether a cell should be retained in a public output document.

        >>> from conftest import concrete_instance_of
        >>> ok = concrete_instance_of(PublicOutput, "should_cell_be_retained")
        >>> ok.should_cell_be_retained(getfixture("code_cell"))
        True
        >>> ok.should_cell_be_retained(getfixture("alternate_cell"))
        True
        >>> ok.should_cell_be_retained(getfixture("english_markdown_cell"))
        True
        >>> ok.should_cell_be_retained(getfixture("starting_cell"))
        True
        >>> ok.should_cell_be_retained(getfixture("german_markdown_cell"))
        False
        >>> ok.should_cell_be_retained(getfixture("deleted_cell"))
        False
        >>> ok.should_cell_be_retained(getfixture("markdown_notes_cell"))
        False
        """
        return super().should_cell_be_retained(cell) and is_public_cell(cell)

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

    def should_cell_be_retained(self, cell: Cell):
        """Retrun whether a cell should be retained in a public output document.

        >>> from conftest import concrete_instance_of
        >>> ok = CompletedOutput()
        >>> ok.should_cell_be_retained(getfixture("code_cell"))
        True
        >>> ok.should_cell_be_retained(getfixture("alternate_cell"))
        True
        >>> ok.should_cell_be_retained(getfixture("english_markdown_cell"))
        True
        >>> ok.should_cell_be_retained(getfixture("starting_cell"))
        False
        >>> ok.should_cell_be_retained(getfixture("german_markdown_cell"))
        False
        >>> ok.should_cell_be_retained(getfixture("deleted_cell"))
        False
        >>> ok.should_cell_be_retained(getfixture("markdown_notes_cell"))
        False
        """
        return super().should_cell_be_retained(cell) and (not is_starting_cell(cell))


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
        >>> ok.is_cell_contents_included(getfixture("kept_cell"))
        True
        >>> ok.is_cell_contents_included(getfixture("markdown_cell"))
        True
        >>> ok.is_cell_contents_included(getfixture("code_cell"))
        False
        """
        is_included_by_super = super().is_cell_contents_included(cell)
        return is_included_by_super and is_cell_contents_included_in_codealongs(cell)

    def should_cell_be_retained(self, cell: Cell):
        """Retrun whether a cell should be retained in a public output document.

        >>> ok = CodeAlongOutput()
        >>> ok.should_cell_be_retained(getfixture("code_cell"))
        True
        >>> ok.should_cell_be_retained(getfixture("english_markdown_cell"))
        True
        >>> ok.should_cell_be_retained(getfixture("starting_cell"))
        True
        >>> ok.should_cell_be_retained(getfixture("german_markdown_cell"))
        False
        >>> ok.should_cell_be_retained(getfixture("alternate_cell"))
        False
        >>> ok.should_cell_be_retained(getfixture("deleted_cell"))
        False
        >>> ok.should_cell_be_retained(getfixture("markdown_notes_cell"))
        False
        """
        return super().should_cell_be_retained(cell) and not is_alternate_solution(cell)

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

    def should_cell_be_retained(self, cell: Cell):
        """Retrun whether a cell should be retained in a public output document.

        >>> from conftest import concrete_instance_of
        >>> ok = SpeakerOutput()
        >>> ok.should_cell_be_retained(getfixture("code_cell"))
        True
        >>> ok.should_cell_be_retained(getfixture("alternate_cell"))
        True
        >>> ok.should_cell_be_retained(getfixture("english_markdown_cell"))
        True
        >>> ok.should_cell_be_retained(getfixture("markdown_notes_cell"))
        True
        >>> ok.should_cell_be_retained(getfixture("starting_cell"))
        False
        >>> ok.should_cell_be_retained(getfixture("german_markdown_cell"))
        False
        >>> ok.should_cell_be_retained(getfixture("deleted_cell"))
        False
        """
        return super().should_cell_be_retained(cell) and not is_starting_cell(cell)
