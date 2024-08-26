"""
The `OutputSpec` is passed to the data source/document when it is processed to determine the
output that should be generated.

## Classes

- `OutputSpec`: The abstract base class of all output types.
- `CompletedOutput`: The output type for artefacts that contain all public contents.
- `CodeAlongOutput`: The output type for artefacts meant for live coding or workshops.
- `SpeakerOutput`: Private outputs that are for the speaker/trainer.
- `EditScriptOutput`: Output type that generates an edit script to update from codealong to completed notebook.
"""

import logging
import re
from abc import ABC, abstractmethod

from attr import define

from .utils.jupyter_utils import (
    Cell,
    get_tags,
    is_cell_included_for_language,
    is_code_cell,
)
from .utils.prog_lang_utils import jupytext_format_for, suffix_for


@define
class OutputSpec(ABC):
    """Description of the kind of output that should be created.

    Outputs can either be public or private.  In public data_sinks some data is not
    included, e.g., speaker notes. Private data_sinks can potentially contain all
    data.

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
    - `path_fragment`: A directory fragment that can be inserted into the output
      path.
    - `tags_to_delete_cell`: If any of these tags is on a cell it is completely deleted
      from the output.
    - `delete_tags`: If true, the tags of the cell are deleted in the output.
    """

    language: str = "en"
    """The desired language of the output."""

    prog_lang: str = "python"
    """The programming language of the notebook."""

    format: str = "code"
    """The format for the generated notebooks."""

    tags_to_delete_cell = {"del", "start"}
    """Tags that cause the whole cell to be deleted."""

    delete_any_cell_contents = False
    """Whether we want to delete the contents of any cell."""

    tags_to_retain_code_cell_contents = set()
    """Contents of cells with these tags is retained even if we delete cell contents."""

    tags_to_delete_markdown_cell_contents = set()
    """Markdown cells with these tags are cleared if we delete cell contents."""

    delete_tags_in_output = False
    """Whether we want to delete the tags of the cell in the output."""

    evaluate_for_html = False
    """Whether we want to evaluate the notebook before generating HTML."""

    _suffix_re = re.compile(r"([^:]*)(:.*)?")
    """Regular expression to extract the file extension from a jupytext format."""

    @abstractmethod
    def get_target_subdir_fragment(self) -> str:
        """Return the subdirectory fragment for the target directory."""
        ...

    @property
    def path_fragment(self):
        return f"{self.language}/{self.format}/{self.get_target_subdir_fragment()}"

    @property
    def file_suffix(self):
        """Return the file suffix for the spec's notebook format.

        >>> os = SpeakerOutput(format="notebook")
        >>> os.file_suffix
        'ipynb'

        >>> os = SpeakerOutput(format="code")
        >>> os.file_suffix
        'py'

        >>> os = SpeakerOutput(format="html")
        >>> os.file_suffix
        'html'
        """
        match self.format:
            case "notebook":
                return ".ipynb"
            case "html":
                return ".html"
            case "code":
                return suffix_for(self.prog_lang)
            case "edit_script":
                return ".ahk"
            case _:
                raise ValueError(
                    f"Could not extract file suffix from format {self.format}."
                )

    @property
    def jupytext_format(self):
        """Return the jupytext format for the spec's notebook format.

        >>> os = SpeakerOutput(format="notebook")
        >>> os.jupytext_format
        'ipynb'

        >>> os = SpeakerOutput(format="code")
        >>> os.jupytext_format
        'py:light'

        >>> os = SpeakerOutput(format="html")
        >>> os.jupytext_format
        'html'
        """
        match self.format:
            case "notebook":
                return "ipynb"
            case "html":
                return "html"
            case "code":
                return jupytext_format_for(self.prog_lang)
            case "edit_script":
                return "py:percent"
            case _:
                raise ValueError(
                    f"Could not extract jupytext format from format "
                    f"{self.format}."
                )

    def is_cell_included(self, cell: Cell) -> bool:
        """Return whether the cell should be included or completely removed.

        If this method returns false the complete cell is removed from the
        output. This is used to, e.g., remove speaker notes or alternate
        solutions from public data_sinks.
        """
        tags_to_delete = self.tags_to_delete_cell.intersection(get_tags(cell))
        if tags_to_delete:
            # logging.debug(
            #     f"Deleting cell '{cell.source[:20]}' because of tags {tags_to_delete}"
            # )
            return False
        return is_cell_included_for_language(cell, self.language)

    def is_cell_contents_included(self, cell: Cell) -> bool:
        """Return whether the cell contents should be included or cleared.

        If this method returns false the contents of the cell is cleared, the
        cell itself is still included. This is used to, e.g., remove code from
        most code cells in codealong notebooks.
        """
        if self.delete_any_cell_contents:
            if is_code_cell(cell):
                tags_to_retain = self.tags_to_retain_code_cell_contents.intersection(
                    get_tags(cell)
                )
                if tags_to_retain:
                    logging.debug(
                        f"Retaining code cell '{cell.source[:20]}' because of tags {tags_to_retain}"
                    )
                    return True
                else:
                    return False
            else:
                tags_to_delete = (
                    self.tags_to_delete_markdown_cell_contents.intersection(
                        get_tags(cell)
                    )
                )
                if tags_to_delete:
                    logging.debug(
                        f"Deleting markdown cell '{cell.source[:20]}' because of tags {tags_to_delete}"
                    )
                    return False
                else:
                    return True
        else:
            return True


@define
class CompletedOutput(OutputSpec):
    """Output spec for data_sources containing all data shared with the public.

    This means they contain everything except speaker notes.
    """

    def get_target_subdir_fragment(self) -> str:
        return "completed"

    tags_to_delete_cell = {"del", "notes", "start"}
    """Tags that cause the whole cell to be deleted."""

    evaluate_for_html = True
    """We want to evaluate completed notebooks before generating HTML."""


@define
class CodeAlongOutput(OutputSpec):
    """Output spec for public data_sources that can be completed during the course.

    Only code cells marked with the "keep" tag have contents in them, all other
    code cells are empty.
    """

    def get_target_subdir_fragment(self) -> str:
        return "code_along"

    tags_to_delete_cell = {"alt", "del", "notes"}
    """Tags that cause the whole cell to be deleted."""

    delete_any_cell_contents = True

    tags_to_retain_code_cell_contents = {"keep", "start"}
    """Contents of cells with these tags is retained even if we delete cell contents."""

    tags_to_delete_markdown_cell_contents = {"answer"}
    """Markdown cells with these tags are cleared if we delete cell contents."""


@define
class SpeakerOutput(OutputSpec):
    """Output spec for data_sources containing all public and private data."""

    def get_target_subdir_fragment(self) -> str:
        return "speaker"

    tags_to_delete_cell = {"del", "start"}
    """Tags that cause the whole cell to be deleted."""

    evaluate_for_html = True
    """If we generate HTML for speakers we want to evaluate code cells."""


def create_output_spec(kind: str, *args, **kwargs):
    """Create a spec given a name and init data.

    >>> create_output_spec("completed", "de", "public", "De", "py")
    CompletedOutput(lang='de', target_root_fragment='public',
                    get_target_subdir_fragment='De', notebook_format='py')
    >>> create_output_spec("CodeAlong")
    CodeAlongOutput(lang='en', target_root_fragment='',
                    get_target_subdir_fragment='', notebook_format='ipynb')
    >>> create_output_spec('speaker')
    SpeakerOutput(lang='en', target_root_fragment='',
                  get_target_subdir_fragment='', notebook_format='ipynb')
    >>> create_output_spec('MySpecialSpec')
    Traceback (most recent call last):
    ...
    ValueError: Unknown spec type: 'MySpecialSpec'.
    Valid spec types are 'completed', 'codealong' or 'speaker'.
    """
    match kind.lower():
        case "completed":
            spec_type = CompletedOutput
        case "code-along":
            spec_type = CodeAlongOutput
        case "speaker":
            spec_type = SpeakerOutput
        case _:
            raise ValueError(
                f"Unknown spec type: {kind!r}.\n"
                "Valid spec types are 'completed', 'codealong' or 'speaker'."
            )
    spec = spec_type(*args, **kwargs)
    return spec


def create_output_specs(
    prog_lang="python",
    languages=("de", "en"),
    notebook_formats=("notebook", "code", "html"),
    kinds=("completed", "code-along", "speaker"),
):
    result = []
    for lang in languages:
        for notebook_format in notebook_formats:
            for kind in kinds:
                result.append(
                    create_output_spec(
                        kind=kind,
                        lang=lang,
                        notebook_format=notebook_format,
                        prog_lang=prog_lang,
                    )
                )
    return result
