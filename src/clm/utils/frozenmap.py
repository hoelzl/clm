"""
A simple immutable map implementation.

Inspired by [silas/frozenmap.py](https://gist.github.com/silas/217e441e22297d7901e97d8c6ddd2162).
"""

import collections.abc
from typing import TypeVar, Generic, Iterator

from attr import frozen, field

K = TypeVar("K")
V = TypeVar("V")


@frozen(hash=False, init=False, order=False, repr=False)
class FrozenMap(Generic[K, V], collections.abc.Mapping[K, V]):
    _dict: dict[K, V] = field(hash=False)
    _hash: int | None = field(repr=False)

    def __init__(self, *args, **kwargs):
        # noinspection PyUnresolvedReferences
        self.__attrs_init__(dict(*args, **kwargs), None)

    def __repr__(self):
        return f"{self.__class__.__name__}({self._dict!r})"

    def __getitem__(self, key: K) -> V:
        return self._dict[key]

    def __contains__(self, key: K) -> bool:
        return key in self._dict

    def __iter__(self) -> Iterator[K]:
        return iter(self._dict)

    def __len__(self) -> int:
        return len(self._dict)

    def __hash__(self) -> int:
        if self._hash is None:
            object.__setattr__(self, "_hash", hash(frozenset(self._dict.items())))
        return self._hash

    def replace(self, /, **changes):
        if not changes:
            return self
        return self.__class__(self, **changes)


def frozenmap(*args, **kwargs) -> FrozenMap[K, V]:
    return FrozenMap(*args, **kwargs)
