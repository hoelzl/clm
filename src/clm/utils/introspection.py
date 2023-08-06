"""

This module contains some utilities for metaprogramming with classes. Currently,
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

from typing import Any, Generator, TYPE_CHECKING, TypeVar, Iterable, Mapping
from inspect import isabstract

if TYPE_CHECKING:
    # Make PyCharm happy, since it doesn't understand the pytest extensions to doctests.
    def getfixture(_name: str) -> Any:
        ...


T = TypeVar('T')


def yield_all_subclasses(cls: type[T]) -> Generator[type[T], None, None]:
    """Generate all (direct and indirect) subclasses of a class.

    Does not yield `cls` itself."""

    for sub in cls.__subclasses__():
        yield sub
        for sub_of_sub in yield_all_subclasses(sub):
            yield sub_of_sub


def all_subclasses(cls: type[T]) -> set[type[T]]:
    """Return all (direct and indirect) subclasses of a class.

    `cls` itself is not included in the result."""

    return set(yield_all_subclasses(cls))


def all_concrete_subclasses(cls: type[T]) -> set[type[T]]:
    """Return all concrete (direct and indirect) subclasses of a class.

    `cls` itself is not included in the result."""

    return {sub for sub in yield_all_subclasses(cls) if not isabstract(sub)}


def yield_all_matching_subclasses(
    cls: type[T], non_overridden_methods: Iterable[str] = ()
) -> Generator[type[T], None, None]:
    """Generate all (direct and indirect) concrete subclasses of `cls`

    `cls` is included, if it is concrete.

    If `non_overridden_methods` is given, only subclasses that do not override
    any of the given methods are returned."""

    if not isabstract(cls):
        yield cls
    for sub in cls.__subclasses__():
        if any(m in sub.__dict__ for m in non_overridden_methods):
            continue
        yield from yield_all_matching_subclasses(sub, non_overridden_methods)


def concrete_subclass_of(
    cls: type[T], non_overridden_methods: str | Iterable[str] = ()
) -> type[T]:
    """Return any concrete subclass that preserves certain methods."""
    if isinstance(non_overridden_methods, str):
        non_overridden_methods = [non_overridden_methods]
    return next(yield_all_matching_subclasses(cls, non_overridden_methods))


def concrete_instance_of(
    cls: type[T],
    non_overridden_methods: str | Iterable[str] = (),
    *,
    initargs: Iterable = (),
    kwargs: Mapping[str, Any] | None = None,
) -> T:
    """Return an instance of a concrete subclass that preserves certain methods."""
    if kwargs is None:
        kwargs = {}
    return concrete_subclass_of(cls, non_overridden_methods)(
        *initargs, **kwargs
    )
