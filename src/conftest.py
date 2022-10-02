import pytest
import jupytext
from nbformat import NotebookNode
from abc import ABC, abstractmethod


_test_notebook = """\
# %% [markdown]
# Some text

# %% [markdown] lang="en" tags=["slide", "other_tag"]
# Some more text

# %%
removed_int = 1 + 1

# %% tags=["keep"]
kept_int = 2 + 2

# %% tags=["alt]
deleted_int = 3 + 3
"""


@pytest.fixture
def test_notebook() -> NotebookNode:
    """Return a notebook for testing purposes."""
    notebook = jupytext.reads(_test_notebook, format="md")
    return notebook


@pytest.fixture
def class_hierarchy() -> tuple[tuple[type, ...], tuple[type, ...]]:
    """Define and return a hierarchy of classes.

    :return: A tuple containing tuples of the abstract and concrete classes in
        the hierarchy
    """
    class A(ABC):
        @abstractmethod
        def method(self):
            ...

    class A1(A):
        pass

    class A2(A):
        def method(self):
            ...

    class A11(A1):
        pass

    class A12(A1):
        def method(self):
            ...

    class A21(A2):
        def method(self):
            ...

    class A22(A2):
        def method(self):
            ...

    return (A, A1, A11), (A2, A12, A21, A22)
