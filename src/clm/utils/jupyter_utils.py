# %%
import logging
import re
from typing import Any, TYPE_CHECKING, TypeAlias

from nbformat import NotebookNode

from clm.utils.path_utils import sanitize_file_name

# %%
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)

# %%
Cell: TypeAlias = NotebookNode


# %%
def get_cell_type(cell: Cell) -> str:
    """Return the type of `cell`."""
    return cell['cell_type']


def is_code_cell(cell):
    """Returns whether a cell is a code cell."""
    return get_cell_type(cell) == 'code'


def is_markdown_cell(cell):
    """Returns whether a cell is a code cell."""
    return get_cell_type(cell) == 'markdown'


# %%
def get_tags(cell: Cell) -> list[str]:
    """Return the tags for `cell`."""
    return cell['metadata'].get('tags', [])


def set_tags(cell: Cell, tags: list['str']) -> None:
    """Set the tags for `cell`."""
    if tags:
        cell['metadata']['tags'] = tags
    elif cell['metadata'].get('tags') is not None:
        del cell['metadata']['tags']


# %%
def has_tag(cell: Cell, tag: str) -> bool:
    """Returns whether a cell has a specific tag."""

    return tag in get_tags(cell)


# %%
def get_cell_language(cell) -> str:
    """Return the language code for a cell.

    An empty language code means the cell has no specified language and should therefore
    be included in all language outputs."""

    return cell['metadata'].get('lang', '')


# %%
# Tags that control the behavior of this cell in a slideshow
_SLIDE_TAGS = {'slide', 'subslide', 'notes'}
# Tags that prevent this cell from being publicly visible
_PRIVATE_TAGS = {'notes', 'private'}
# Tags that may appear in any kind of cell
_EXPECTED_GENERIC_TAGS = _SLIDE_TAGS | _PRIVATE_TAGS | {'alt', 'del'}
# Tags that may appear in code cells (in addition to generic tags)
_EXPECTED_CODE_TAGS = {'keep', 'start'} | _EXPECTED_GENERIC_TAGS
# Tags that may appear in markdown cells (in addition to generic tags)
_EXPECTED_MARKDOWN_TAGS = {'notes', 'answer'} | _EXPECTED_GENERIC_TAGS


# %%
def is_deleted_cell(cell: Cell):
    """Return whether a cell has been deleted."""
    return 'del' in get_tags(cell)


# %%
def is_private_cell(cell: Cell):
    """Return whether a cell is only visible in private documents."""
    return bool(_PRIVATE_TAGS.intersection(get_tags(cell)))


# %%
def is_public_cell(cell: Cell):
    """Return whether a cell is visible in public documents."""
    return not is_private_cell(cell)


# %%
def is_starting_cell(cell: Cell):
    """Return whether a cell is a starting point for completions.

    Starting points should be completely removed from completed notebooks, but they
    cells should be included in codealongs."""
    return 'start' in get_tags(cell)


# %%
def is_alternate_solution(cell: Cell):
    """Return whether a cell is an alternate solution.

    Alternate solutions should be present in completed notebooks, but their cells should
    be completely removed from codealongs (not included as empty cells)."""
    return 'alt' in get_tags(cell)


# %%
def is_answer_cell(cell: Cell):
    """Return whether a cell is an answer to a question.

    Answers should be present in completed notebooks, but their cells should
    be empty in codealongs.

    - Code cells are answers unless they have the `keep` tag.
    - Markdown cells are only answers if they have the `answer` tag."""

    if is_code_cell(cell):
        return not {'keep', 'start'}.intersection(get_tags(cell))
    else:
        return 'answer' in get_tags(cell)


# %%
def get_slide_tag(cell: Cell) -> str | None:
    """Return the slide tag of cell or `None` if it doesn't have one.

    Raises an error if the slide has multiple slide tags."""

    tags = get_tags(cell)
    slide_tags = _SLIDE_TAGS.intersection(tags)
    if slide_tags:
        if len(slide_tags) > 1:
            logging.warning(
                f'Found more than one slide tag: {slide_tags}. Picking one at random.'
            )
        return slide_tags.pop()
    else:
        return None


# %%
def is_cell_included_for_language(cell: Cell, lang: str) -> bool:
    """Return whether a cell should be retained for a particular language.

    Cells without language metadata should be retained for all languages, cells with
    language metadata should obviously only be included in their language."""

    cell_lang = get_cell_language(cell)
    return not cell_lang or cell_lang == lang


# %%
def warn_on_invalid_code_tags(tags):
    for tag in tags:
        if tag not in _EXPECTED_CODE_TAGS:
            logging.warning(f'Unknown tag for code cell: {tag!r}.')


# %%
def warn_on_invalid_markdown_tags(tags):
    for tag in tags:
        if tag not in _EXPECTED_MARKDOWN_TAGS:
            logging.warning(f'Unknown tag for markdown cell: {tag!r}.')


# %%
TITLE_REGEX = re.compile(
    r"{{\s*header\s*\(\s*[\"'](.*)[\"']\s*,\s*[\"'](.*)[\"']\s*\)\s*}}"
)


# %%
def find_notebook_titles(
    text: str, default: str = 'unnamed'
) -> dict[str, str]:
    """Find the titles from the source text of a notebook."""
    match = TITLE_REGEX.search(text)
    if match:
        return {
            'en': sanitize_file_name(match[2]),
            'de': sanitize_file_name(match[1]),
        }
    else:
        return {'en': default, 'de': default}
