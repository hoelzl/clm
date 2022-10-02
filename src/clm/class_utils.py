"""
Some metaprogramming utilities.
"""

from typing import Generator, TypeVar
from inspect import isabstract


T = TypeVar("T")


def yield_all_subclasses(cls: type[T]) -> Generator[type[T], None, None]:
    """Compute all subclasses of a class.

    >>> ((A, A1, A11), (A2, A12, A21, A22)) = getfixture("class_hierarchy")
    >>> len(set(yield_all_subclasses(A)))
    6
    >>> len(set(yield_all_subclasses(A1)))
    2
    >>> set(yield_all_subclasses(A1)) == {A11, A12}
    True
    """

    for sub in cls.__subclasses__():
        yield sub
        for sub_of_sub in yield_all_subclasses(sub):
            yield sub_of_sub


def all_subclasses(cls: type[T]) -> set[type[T]]:
    """Compute all subclasses of a class.

    >>> ((A, A1, A11), (A2, A12, A21, A22)) = getfixture("class_hierarchy")
    >>> len(all_subclasses(A))
    6
    >>> len(all_subclasses(A1))
    2
    >>> all_subclasses(A1) == {A11, A12}
    True
    """

    return set(yield_all_subclasses(cls))


def all_concrete_subclasses(cls: type[T]) -> set[type[T]]:
    """Compute all subclasses of a class.

    >>> ((A, A1, A11), (A2, A12, A21, A22)) = getfixture("class_hierarchy")
    >>> len(all_concrete_subclasses(A))
    4
    >>> len(all_concrete_subclasses(A1))
    1
    >>> len(all_concrete_subclasses(A2))
    2
    >>> all_concrete_subclasses(A1) == {A12}
    True
    >>> all_concrete_subclasses(A2) == {A21, A22}
    True
    """

    return {sub for sub in all_subclasses(cls) if not isabstract(sub)}
