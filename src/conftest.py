from abc import ABC, abstractmethod
from inspect import isabstract
from io import StringIO
from pathlib import Path, PurePosixPath
from typing import Any, TYPE_CHECKING
from typing import Generator, Iterable, Mapping, TypeAlias, TypeVar

import jupytext
import pytest
from nbformat import NotebookNode

# %%
if TYPE_CHECKING:
    # Make PyCharm happy, since it doesn't understand the pytest extensions to doctests.
    def getfixture(_name: str) -> Any:
        ...


_code_notebook_str = """\
# %%
default_int = 1 + 1

# %% lang="en"
english_int = 1 + 1

# %% lang="de"
german_int = 1 + 1

# %% tags=["slide"]
slide_int = 123

# %% tags=["subslide"]
subslide_int = 234

# %% tags=["del"]
deleted_int = 3 + 3

# %% tags=["keep"]
kept_int = 2 + 2

# %% tags=["alt"]
alternate_int = 3 + 3

# %% tags=["start"]
start_val = 123
"""

_code_notebook = jupytext.reads(_code_notebook_str, format='py:percent')
_code_cell_keys = [
    'code',
    'en',
    'de',
    'slide',
    'subslide',
    'del',
    'keep',
    'alt',
    'start',
]

_code_cells = dict(zip(_code_cell_keys, _code_notebook.cells))

_markdown_notebook_str = """\
# %% [markdown]
# Some text

# %% [markdown] lang="en"
# Text in English.

# %% [markdown] lang="de"
# Text in Deutsch.

# %% [markdown] tags=["slide", "other_tag"]
# Some more text

# %% [markdown] tags=["subslide"]
# A note.

# %% [markdown] tags=["del"]
# A note.

# %% [markdown] tags=["notes"]
# A note.

# %% [markdown] tags=["answer"]
# A note.
"""

_markdown_notebook = jupytext.reads(
    _markdown_notebook_str, format='py:percent'
)
_markdown_cell_keys = [
    'md',
    'en',
    'de',
    'slide',
    'subslide',
    'del',
    'notes',
    'answer',
]
_merged_markdown_cell_keys = [
    'md',
    'en-md',
    'de-md',
    'slide-md',
    'subslide-md',
    'del-md',
    'notes',
    'answer',
]

_markdown_cells = dict(zip(_markdown_cell_keys, _markdown_notebook.cells))

_all_cells = dict(
    zip(
        _code_cell_keys + _merged_markdown_cell_keys,
        _code_notebook.cells + _markdown_notebook.cells,
    )
)

Cell: TypeAlias = NotebookNode


@pytest.fixture
def code_cells() -> dict[str, Cell]:
    return _code_cells


@pytest.fixture
def markdown_cells() -> dict[str, Cell]:
    return _markdown_cells


@pytest.fixture
def all_cells() -> dict[str, Cell]:
    return _all_cells


@pytest.fixture
def code_cell() -> Cell:
    return _code_cells['code']


@pytest.fixture
def english_code_cell() -> Cell:
    return _code_cells['en']


@pytest.fixture
def german_code_cell() -> Cell:
    return _code_cells['de']


@pytest.fixture
def code_slide_cell() -> Cell:
    return _code_cells['slide']


@pytest.fixture
def code_subslide_cell() -> Cell:
    return _code_cells['subslide']


@pytest.fixture
def kept_cell() -> Cell:
    return _code_cells['keep']


@pytest.fixture
def alternate_cell() -> Cell:
    return _code_cells['alt']


@pytest.fixture
def deleted_cell() -> Cell:
    return _code_cells['del']


@pytest.fixture
def starting_cell() -> Cell:
    return _code_cells['start']


@pytest.fixture
def markdown_cell() -> Cell:
    return _markdown_cells['md']


@pytest.fixture
def english_markdown_cell() -> Cell:
    return _markdown_cells['en']


@pytest.fixture
def german_markdown_cell() -> Cell:
    return _markdown_cells['de']


@pytest.fixture
def markdown_slide_cell() -> Cell:
    return _markdown_cells['slide']


@pytest.fixture
def markdown_subslide_cell() -> Cell:
    return _markdown_cells['subslide']


@pytest.fixture
def deleted_markdown_cell() -> Cell:
    return _markdown_cells['del']


@pytest.fixture
def markdown_notes_cell() -> Cell:
    return _markdown_cells['notes']


@pytest.fixture
def answer_cell() -> Cell:
    return _markdown_cells['answer']


T = TypeVar('T')


@pytest.fixture
def course_files():
    return [
        PurePosixPath('/tmp/course/slides/module_10_intro/topic_10_python.py'),
        PurePosixPath('/tmp/course/slides/module_10_intro/ws_10_python.py'),
        PurePosixPath('/tmp/course/slides/module_10_intro/python_file.py'),
        PurePosixPath('/tmp/course/slides/module_10_intro/img/my_img.png'),
        PurePosixPath('/tmp/course/examples/non_affine_file.py'),
        PurePosixPath(
            '/tmp/course/slides/module_20_data_types/topic_10_ints.py'
        ),
        PurePosixPath('/tmp/course/slides/module_20_data_types/ws_10_ints.py'),
        PurePosixPath(
            '/tmp/course/slides/module_20_data_types/topic_20_floats.py'
        ),
        PurePosixPath(
            '/tmp/course/slides/module_20_data_types/ws_20_floats.py'
        ),
        PurePosixPath(
            '/tmp/course/slides/module_20_data_types/topic_30_lists.py'
        ),
        PurePosixPath(
            '/tmp/course/slides/module_20_data_types/ws_30_lists.py'
        ),
    ]


_CSV_SOURCE = """\
Base Dir:,course/
Target Dir:,output/
Template Dir:,other-course/templates/
Language:,de

/tmp/course/slides/module_10_intro/topic_10_python.py,my_dir,Notebook
/tmp/course/slides/module_10_intro/ws_10_python.py,my_dir,Notebook
/tmp/course/slides/module_10_intro/python_file.py,my_dir,Notebook
/tmp/course/slides/module_10_intro/img/my_img.png,my_dir,DataFile
/tmp/course/examples/non_affine_file.py,my_dir,DataFile
/tmp/course/slides/module_20_data_types/topic_10_ints.py,my_dir,Notebook
/tmp/course/slides/module_20_data_types/ws_10_ints.py,my_dir,Notebook
/tmp/course/slides/module_20_data_types/topic_20_floats.py,my_dir,Notebook
/tmp/course/slides/module_20_data_types/ws_20_floats.py,my_dir,Notebook
/tmp/course/slides/module_20_data_types/topic_30_lists.py,my_dir,Notebook
/tmp/course/slides/module_20_data_types/ws_30_lists.py,my_dir,Notebook
"""


@pytest.fixture
def course_spec_csv_stream():
    return StringIO(_CSV_SOURCE)


def _create_document_spec_data(
    start_index, end_index, part_index, doc_number=1
):
    """Create a list of triples representing args for `DocumentSpec`.

    >>> _create_document_spec_data(1, 3, 1)
    [('/a/b/topic_1.py', 'part-1', 'Notebook', 1),
    ('/a/b/topic_2.py', 'part-1', 'Notebook', 1),
    ('/a/b/topic_3.py', 'part-1', 'Notebook', 1)]
    """
    return [
        (f'/a/b/topic_{index}.py', f'part-{part_index}', 'Notebook', 1)
        for index in range(start_index, end_index + 1)
    ]


@pytest.fixture
def course_spec_1():
    from clm.core.course_specs import CourseSpec, DocumentSpec

    document_specs = [
        DocumentSpec(*args) for args in _create_document_spec_data(1, 4, 1, 1)
    ]
    return CourseSpec(
        Path('/a'), Path('/out/dir'), document_specs=document_specs
    )


@pytest.fixture
def course_spec_2():
    from clm.core.course_specs import CourseSpec, DocumentSpec

    document_specs = [
        DocumentSpec(*args) for args in _create_document_spec_data(3, 6, 2)
    ]
    return CourseSpec(
        Path('/a'), Path('/out/dir'), document_specs=document_specs
    )
