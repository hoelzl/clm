from typing import TypeAlias

import jupytext
import pytest
from nbformat import NotebookNode

Cell: TypeAlias = NotebookNode


def _cell_from_string(header_line: str, *body_lines: str) -> Cell:
    return jupytext.reads(
        '\n'.join([header_line, *body_lines]), format='py:percent'
    ).cells[0]


@pytest.fixture
def code_cell() -> Cell:
    return _cell_from_string('# %%', 'default_int = 1 + 1')


@pytest.fixture
def english_code_cell() -> Cell:
    return _cell_from_string('# %% lang="en"', 'english_int = 1 + 1')


@pytest.fixture
def german_code_cell() -> Cell:
    return _cell_from_string('# %% lang="de"', 'german_int = 1 + 1')


@pytest.fixture
def code_slide_cell() -> Cell:
    return _cell_from_string('# %% tags=["slide"]', 'slide_int = 123')


@pytest.fixture
def code_subslide_cell() -> Cell:
    return _cell_from_string('# %% tags=["subslide"]', 'subslide_int = 234')


@pytest.fixture
def kept_cell() -> Cell:
    return _cell_from_string('# %% tags=["keep"]', 'kept_int = 2 + 2')


@pytest.fixture
def alternate_cell() -> Cell:
    return _cell_from_string('# %% tags=["alt"]', 'alternate_int = 3 + 3')


@pytest.fixture
def deleted_cell() -> Cell:
    return _cell_from_string('# %% tags=["del"]', 'deleted_int = 3 + 3')


@pytest.fixture
def starting_cell() -> Cell:
    return _cell_from_string('# %% tags=["start"]', 'start_val = 123')


@pytest.fixture
def markdown_cell() -> Cell:
    return _cell_from_string('# %% [markdown]', 'Some text')


@pytest.fixture
def english_markdown_cell() -> Cell:
    return _cell_from_string('# %% [markdown] lang="en"', 'Text in English.')


@pytest.fixture
def german_markdown_cell() -> Cell:
    return _cell_from_string('# %% [markdown] lang="de"', 'Text in Deutsch.')


@pytest.fixture
def markdown_slide_cell() -> Cell:
    return _cell_from_string(
        '# %% [markdown] tags=["slide"]', 'Some more text'
    )


@pytest.fixture
def markdown_subslide_cell() -> Cell:
    return _cell_from_string('# %% [markdown] tags=["subslide"]', 'A note.')


@pytest.fixture
def deleted_markdown_cell() -> Cell:
    return _cell_from_string('# %% [markdown] tags=["del"]', 'A deleted cell.')


@pytest.fixture
def markdown_notes_cell() -> Cell:
    return _cell_from_string('# %% [markdown] tags=["notes"]', 'A note.')


@pytest.fixture
def answer_cell() -> Cell:
    return _cell_from_string('# %% [markdown] tags=["answer"]', 'An answer.')


@pytest.fixture
def code_cells(
    code_cell,
    code_slide_cell,
    code_subslide_cell,
    deleted_cell,
    kept_cell,
    alternate_cell,
    starting_cell,
):
    return [
        code_cell,
        code_slide_cell,
        code_subslide_cell,
        deleted_cell,
        kept_cell,
        alternate_cell,
        starting_cell,
    ]


@pytest.fixture
def markdown_cells(
    markdown_cell,
    markdown_slide_cell,
    markdown_subslide_cell,
    deleted_markdown_cell,
    markdown_notes_cell,
    answer_cell,
):
    return [
        markdown_cell,
        markdown_slide_cell,
        markdown_subslide_cell,
        deleted_markdown_cell,
        markdown_notes_cell,
        answer_cell,
    ]
