"""

This module contains some utilities for metaprogramming with classes. Currently
these are mostly intercessory methods for enumerating the subclasses of a class.

### Main Functions

- `all_subclasses()`: Return a set of all (direct and indirect) subclasses of a
  class.
- `all_concrete_subclasses()`: Return a set of all (direct and indirect)
  concrete subclasses of a class.

### Utility Functions

- `yield_all_subclasses()` Return a generator over all (direct and indirect)
  subclasses of a class.
"""

from typing import Generator, TypeVar
from inspect import isabstract


T = TypeVar("T")


def yield_all_subclasses(cls: type[T]) -> Generator[type[T], None, None]:
    """Generate all (direct and indirect) subclasses of a class.

    Does not yield `cls` itself.

    >>> ((A, A1, A11), (C2, C12, C13, C21, C22)) = getfixture("class_hierarchy")
    >>> set(yield_all_subclasses(A1)) == {A11, C12, C13}
    True
    >>> set(yield_all_subclasses(C2)) == {C21, C22}
    True
    >>> set(yield_all_subclasses(A)) == {A1, A11, C12, C13, C2, C21, C22}
    True
    >>> next(yield_all_subclasses(A1)) in {A11, C12, C13}
    True
    """

    for sub in cls.__subclasses__():
        yield sub
        for sub_of_sub in yield_all_subclasses(sub):
            yield sub_of_sub


def all_subclasses(cls: type[T]) -> set[type[T]]:
    """Return all (direct and indirect) subclasses of a class.

    `cls` itself is not included in the result.

    >>> ((A, A1, A11), (C2, C12, C13, C21, C22)) = getfixture("class_hierarchy")
    >>> all_subclasses(A1) == {A11, C12, C13}
    True
    >>> all_subclasses(C2) == {C21, C22}
    True
    >>> all_subclasses(A) == {A1, A11, C12, C13, C2, C21, C22}
    True
    """

    return set(yield_all_subclasses(cls))


def all_concrete_subclasses(cls: type[T]) -> set[type[T]]:
    """Return all concrete (direct and indirect) subclasses of a class.

    `cls` itself is not included in the result.

    >>> ((A, A1, A11), (C2, C12, C13, C21, C22)) = getfixture("class_hierarchy")
    >>> all_concrete_subclasses(A1) == {C12, C13}
    True
    >>> all_concrete_subclasses(C2) == {C21, C22}
    True
    >>> all_concrete_subclasses(A) == {C12, C13, C2, C21, C22}
    True
    """

    return {sub for sub in all_subclasses(cls) if not isabstract(sub)}
