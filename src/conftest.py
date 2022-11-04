from abc import ABC, abstractmethod
from inspect import isabstract
from io import StringIO
from pathlib import PurePosixPath
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

_code_notebook = jupytext.reads(_code_notebook_str, format="md")
_code_cell_keys = [
    "code",
    "en",
    "de",
    "slide",
    "subslide",
    "del",
    "keep",
    "alt",
    "start",
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

_markdown_notebook = jupytext.reads(_markdown_notebook_str, format="md")
_markdown_cell_keys = ["md", "en", "de", "slide", "subslide", "del", "notes", "answer"]
_merged_markdown_cell_keys = [
    "md",
    "en-md",
    "de-md",
    "slide-md",
    "subslide-md",
    "del-md",
    "notes",
    "answer",
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
    return _code_cells["code"]


@pytest.fixture
def english_code_cell() -> Cell:
    return _code_cells["en"]


@pytest.fixture
def german_code_cell() -> Cell:
    return _code_cells["de"]


@pytest.fixture
def code_slide_cell() -> Cell:
    return _code_cells["slide"]


@pytest.fixture
def code_subslide_cell() -> Cell:
    return _code_cells["subslide"]


@pytest.fixture
def kept_cell() -> Cell:
    return _code_cells["keep"]


@pytest.fixture
def alternate_cell() -> Cell:
    return _code_cells["alt"]


@pytest.fixture
def deleted_cell() -> Cell:
    return _code_cells["del"]


@pytest.fixture
def starting_cell() -> Cell:
    return _code_cells["start"]


@pytest.fixture
def markdown_cell() -> Cell:
    return _markdown_cells["md"]


@pytest.fixture
def english_markdown_cell() -> Cell:
    return _markdown_cells["en"]


@pytest.fixture
def german_markdown_cell() -> Cell:
    return _markdown_cells["de"]


@pytest.fixture
def markdown_slide_cell() -> Cell:
    return _markdown_cells["slide"]


@pytest.fixture
def markdown_subslide_cell() -> Cell:
    return _markdown_cells["subslide"]


@pytest.fixture
def deleted_markdown_cell() -> Cell:
    return _markdown_cells["del"]


@pytest.fixture
def markdown_notes_cell() -> Cell:
    return _markdown_cells["notes"]


@pytest.fixture
def answer_cell() -> Cell:
    return _markdown_cells["answer"]


@pytest.fixture
def class_hierarchy() -> tuple[tuple[type, ...], tuple[type, ...]]:
    """Define and return a hierarchy of classes.

    The result hierarchy looks as follows (* means abstract):

    - A*           [abstract_method* | concrete_method]
        - A1*      [                 |                ]
            - A11* [                 |                ]
            - C12  [abstract_method  |                ]
            - C13  [abstract_method  | concrete_method]
        - C2       [abstract_method  |                ]
            - C21  [                 | concrete_method]
            - C22  [abstract_method  |                ]

    :return: A tuple containing tuples of the abstract and concrete classes in
        the hierarchy
    """

    class A(ABC):
        @abstractmethod
        def abstract_method(self):
            ...

        def concrete_method(self):
            ...

    class A1(A, ABC):
        pass

    class A11(A1, ABC):
        pass

    class C12(A1):
        def abstract_method(self):
            ...

    class C13(A1):
        def abstract_method(self):
            ...

        def concrete_method(self):
            ...

    class C2(A):
        def abstract_method(self):
            ...

    class C21(C2):
        def concrete_method(self):
            ...

    class C22(C2):
        def abstract_method(self):
            ...

    return (A, A1, A11), (C2, C12, C13, C21, C22)


T = TypeVar("T")


def _yield_all_matching_subclasses(
    cls: type[T], non_overridden_methods: Iterable[str] = ()
) -> Generator[type[T], None, None]:
    """Generate all (direct and indirect) subclasses of `cls` (including `cls`).

    >>> ((A, A1, A11), (C2, C12, C13, C21, C22)) = getfixture("class_hierarchy")
    >>> set(_yield_all_matching_subclasses(A1)) == {C12, C13}
    True
    >>> set(_yield_all_matching_subclasses(C2)) == {C2, C21, C22}
    True
    >>> set(_yield_all_matching_subclasses(A1, ["concrete_method"])) == {C12}
    True
    >>> set(_yield_all_matching_subclasses(C2, ["concrete_method"])) == {C2, C22}
    True
    >>> set(_yield_all_matching_subclasses(
    ...         C2, ["concrete_method", "another_method"]))== {C2, C22}
    True
    >>> set(_yield_all_matching_subclasses(A)) == {C12, C13, C2, C21, C22}
    True
    """

    if not isabstract(cls):
        yield cls
    for sub in cls.__subclasses__():
        if any(m in sub.__dict__ for m in non_overridden_methods):
            continue
        yield from _yield_all_matching_subclasses(sub, non_overridden_methods)


def concrete_subclass_of(
    cls: type[T], non_overridden_methods: str | Iterable[str] = ()
) -> type[T]:
    """Return any concrete subclass that preserves certain methods.

    >>> ((A, A1, A11), (C2, C12, C13, C21, C22)) = getfixture("class_hierarchy")
    >>> concrete_subclass_of(A1) in {C12, C13}
    True
    >>> concrete_subclass_of(C2) in {C2, C21, C22}
    True
    >>> concrete_subclass_of(A1, ["concrete_method"]) == C12
    True
    >>> concrete_subclass_of(C2, ["concrete_method"]) in {C2, C22}
    True
    >>> concrete_subclass_of(A) in {C12, C13, C2, C21, C22}
    True
    """
    if isinstance(non_overridden_methods, str):
        non_overridden_methods = [non_overridden_methods]
    return next(_yield_all_matching_subclasses(cls, non_overridden_methods))


def concrete_instance_of(
    cls: type[T],
    non_overridden_methods: str | Iterable[str] = (),
    *,
    initargs: Iterable = (),
    kwargs: Mapping[str, Any] | None = None,
) -> T:
    if kwargs is None:
        kwargs = {}
    return concrete_subclass_of(cls, non_overridden_methods)(*initargs, **kwargs)


@pytest.fixture
def course_files():
    return [
        PurePosixPath("/tmp/course/slides/module_10_intro/topic_10_python.py"),
        PurePosixPath("/tmp/course/slides/module_10_intro/ws_10_python.py"),
        PurePosixPath("/tmp/course/slides/module_10_intro/python_file.py"),
        PurePosixPath("/tmp/course/slides/module_10_intro/img/my_img.png"),
        PurePosixPath("/tmp/course/examples/non_affine_file.py"),
        PurePosixPath("/tmp/course/slides/module_20_data_types/topic_10_ints.py"),
        PurePosixPath("/tmp/course/slides/module_20_data_types/ws_10_ints.py"),
        PurePosixPath("/tmp/course/slides/module_20_data_types/topic_20_floats.py"),
        PurePosixPath("/tmp/course/slides/module_20_data_types/ws_20_floats.py"),
        PurePosixPath("/tmp/course/slides/module_20_data_types/topic_30_lists.py"),
        PurePosixPath("/tmp/course/slides/module_20_data_types/ws_30_lists.py"),
    ]


_CSV_SOURCE = """\
Base Dir:,/tmp/course/
Target Dir:,/tmp/output/
Template Dir:,/tmp/other-course/templates/
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
