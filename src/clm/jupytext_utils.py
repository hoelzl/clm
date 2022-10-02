import logging
from typing import Any, Mapping, TypeAlias
import jupytext.formats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)


Cell: TypeAlias = Mapping['str', Any]


def get_cell_type(cell: Cell) -> str:
    """Return the type of `cell`.

    >>> nb = getfixture("test_notebook")
    >>> get_cell_type(nb.cells[0])
    'markdown'
    >>> get_cell_type(nb.cells[1])
    'markdown'
    >>> get_cell_type(nb.cells[2])
    'code'
    >>> get_cell_type(nb.cells[2])
    'code'
    """

    return cell["cell_type"]


def get_tags(cell: Cell) -> list[str]:
    """Return the tags for `cell`.

    >>> nb = getfixture("test_notebook")
    >>> get_tags(nb.cells[0])
    []
    >>> get_tags(nb.cells[1])
    ['slide', 'other_tag']
    >>> get_tags(nb.cells[2])
    []
    >>> get_tags(nb.cells[3])
    ['keep']
    """

    return cell["metadata"].get("tags", [])


def set_tags(cell: Cell, tags: list["str"]) -> None:
    """Set the tags for `cell`.

    >>> nb = getfixture("test_notebook")
    >>> get_tags(nb.cells[0])
    []
    >>> set_tags(nb.cells[0], ["tag1", "tag2"])
    >>> get_tags(nb.cells[0])
    ['tag1', 'tag2']
    >>> set_tags(nb.cells[0], [])
    >>> get_tags(nb.cells[0])
    []
    """

    if tags:
        cell["metadata"]["tags"] = tags
    elif cell["metadata"].get("tags") is not None:
        del cell["metadata"]["tags"]


def has_tag(cell: Cell, tag: str) -> bool:
    """Returns whether a cell has a specific tag.

    >>> nb = getfixture("test_notebook")
    >>> has_tag(nb.cells[0], "tag1")
    False
    >>> has_tag(nb.cells[1], "slide")
    True
    >>> has_tag(nb.cells[1], "other_tag")
    True
    >>> has_tag(nb.cells[1], "tag1")
    False
    """

    return tag in get_tags(cell)


def get_cell_language(cell) -> str:
    """Return the language code for a cell.

    An empty language code means the cell has no specified language.

    >>> nb = getfixture("test_notebook")
    >>> get_cell_language(nb.cells[0])
    ''
    >>> get_cell_language(nb.cells[1])
    'en'
    """

    return cell["metadata"].get("lang", "")
