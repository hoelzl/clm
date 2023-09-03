from typing import Callable, Iterable, Optional


def listify(obj):
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, Iterable):
        return list(obj)
    return [obj]


def find(elt, items: Iterable, key: Optional[Callable] = None):
    """Find an element in an iterable.

    If `key` is not `None`, apply it to each member of `items` and to `elt`
    before performing the comparison.

    >>> find(1, [1, 2, 3])
    1
    >>> find(0, [1, 2, 3]) is None
    True
    >>> find((1, "x"), [(1, "a"), (2, "b")], key=lambda t: t[0])
    (1, 'a')
    >>> find((2, 3), [(1, "a"), (2, "b")], key=lambda t: t[0])
    (2, 'b')
    >>> find((3, "b"), [(1, "a"), (2, "b")], key=lambda t: t[0]) is None
    True
    """
    if key is None:
        for item in items:
            if item == elt:
                return item
    else:
        for item in items:
            if key(item) == key(elt):
                return item
    return None
