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


def split_list_by_predicate(input_list, predicate):
    """Split a list into two lists according to a predicate.

    Is stable for the order of the elements, i.e., the elements in both lists
    will appear in the same order as in the input list.

    >>> split_list_by_predicate([1, 2, 3, 4, 5, 6], lambda x: x % 2 == 0)
    ([2, 4, 6], [1, 3, 5])
    """
    true_values, false_values = [], []
    for item in input_list:
        if predicate(item):
            true_values.append(item)
        else:
            false_values.append(item)
    return true_values, false_values
