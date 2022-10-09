from abc import ABC, abstractmethod
from inspect import isabstract
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


_test_notebook = """\
# %% [markdown]
# Some text

# %% [markdown] lang="en" tags=["slide", "other_tag"]
# Some more text

# %%
removed_int = 1 + 1

# %% tags=["keep"]
kept_int = 2 + 2

# %% tags=["alt"]
alternate_int = 3 + 3

# %% tags=["del"]
deleted_int = 3 + 3

# %% [markdown] lang="de"
# Text in Deutsch.

# %% [markdown] tags=["notes"]
# A note.
"""


@pytest.fixture
def test_notebook() -> NotebookNode:
    """Return a notebook for testing purposes."""
    notebook = jupytext.reads(_test_notebook, format="md")
    return notebook


Cell: TypeAlias = Mapping["str", Any]


@pytest.fixture
def markdown_cell(test_notebook) -> Cell:
    return test_notebook.cells[0]


@pytest.fixture
def code_cell(test_notebook) -> Cell:
    return test_notebook.cells[2]


@pytest.fixture
def markdown_slide_cell(test_notebook) -> Cell:
    return test_notebook.cells[1]


@pytest.fixture
def kept_cell(test_notebook) -> Cell:
    return test_notebook.cells[3]


@pytest.fixture
def alternate_cell(test_notebook) -> Cell:
    return test_notebook.cells[4]


@pytest.fixture
def deleted_cell(test_notebook) -> Cell:
    return test_notebook.cells[5]


@pytest.fixture
def german_markdown_cell(test_notebook) -> Cell:
    return test_notebook.cells[6]


@pytest.fixture
def english_markdown_cell(test_notebook) -> Cell:
    return test_notebook.cells[1]


@pytest.fixture
def markdown_notes_cell(test_notebook) -> Cell:
    return test_notebook.cells[7]


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
    >>> set(_yield_all_matching_subclasses(A)) == {C12, C13, C2, C21, C22}
    True
    """

    if not isabstract(cls):
        yield cls
    for sub in cls.__subclasses__():
        if any(m in sub.__dict__ for m in non_overridden_methods):
            continue
        yield from _yield_all_matching_subclasses(sub, *non_overridden_methods)


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
