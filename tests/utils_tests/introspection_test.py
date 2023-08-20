from abc import ABC, abstractmethod

from clm.utils.introspection import (
    all_concrete_subclasses,
    all_subclasses,
    concrete_instance_of,
    concrete_subclass_of,
    yield_all_matching_subclasses,
    yield_all_subclasses,
)


# We define the following hierarchy of classes
#
# - `A...` ist an abstract class
# - `C...` is a concrete class
# - `*` marks abstract classes for emphasis
#
# - A*           [abstract_method* | concrete_method]
#     - A1*      [                 |                ]
#         - A11* [                 |                ]
#         - C12  [abstract_method  |                ]
#         - C13  [abstract_method  | concrete_method]
#     - C2       [abstract_method  |                ]
#         - C21  [                 | concrete_method]
#         - C22  [abstract_method  |                ]


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


def test_yield_all_subclasses_a():
    assert set(yield_all_subclasses(A)) == {A1, A11, C12, C13, C2, C21, C22}


def test_yield_all_subclasses_a1():
    assert set(yield_all_subclasses(A1)) == {A11, C12, C13}


def test_yield_all_subclasses_c2():
    assert set(yield_all_subclasses(C2)) == {C21, C22}


def test_yield_all_subclasses_a1_next():
    assert next(yield_all_subclasses(A1)) in {A11, C12, C13}


def test_all_subclasses_a():
    assert all_subclasses(A) == {A1, A11, C12, C13, C2, C21, C22}


def test_all_subclasses_a1():
    assert all_subclasses(A1) == {A11, C12, C13}


def test_all_subclasses_c2():
    assert all_subclasses(C2) == {C21, C22}


def test_all_concrete_subclasses_a():
    assert all_concrete_subclasses(A) == {C12, C13, C2, C21, C22}


def test_all_concrete_subclasses_a1():
    assert all_concrete_subclasses(A1) == {C12, C13}


def test_all_concrete_subclasses_c2():
    assert all_concrete_subclasses(C2) == {C21, C22}


def test_yield_all_matching_subclasses_a():
    assert set(yield_all_matching_subclasses(A)) == {C12, C13, C2, C21, C22}


def test_yield_all_matching_subclasses_a1():
    assert set(yield_all_matching_subclasses(A1)) == {C12, C13}


def test_yield_all_matching_subclasses_c2():
    assert set(yield_all_matching_subclasses(C2)) == {C2, C21, C22}


def test_yield_all_matching_subclasses_a1_concrete_method():
    assert set(yield_all_matching_subclasses(A1, ["concrete_method"])) == {C12}


def test_yield_all_matching_subclasses_c2_concrete_method():
    assert set(yield_all_matching_subclasses(C2, ["concrete_method"])) == {
        C2,
        C22,
    }


def test_yield_all_matching_subclasses_another_method():
    assert set(
        yield_all_matching_subclasses(C2, ["concrete_method", "another_method"])
    ) == {C2, C22}


def test_concrete_subclass_of_a():
    assert concrete_subclass_of(A) in {C12, C13, C2, C21, C22}


def test_concrete_subclass_of_a1():
    assert concrete_subclass_of(A1) in {C12, C13}


def test_concrete_subclass_of_c2():
    assert concrete_subclass_of(C2) in {C2, C21, C22}


def test_concrete_subclass_of_a1_concrete_method():
    assert concrete_subclass_of(A1, ["concrete_method"]) == C12


def test_concrete_subclass_of_c2_concrete_method():
    assert concrete_subclass_of(C2, ["concrete_method"]) in {C2, C22}


def test_concrete_instance_of_a():
    assert isinstance(concrete_instance_of(A), A)


def test_concrete_instance_of_a1():
    assert isinstance(concrete_instance_of(A1), A1)


def test_concrete_instance_of_c2():
    assert isinstance(concrete_instance_of(C2), C2)


def test_concrete_instance_of_a1_concrete_method():
    assert isinstance(concrete_instance_of(A1, ["concrete_method"]), C12)


def test_concrete_instance_of_c2_concrete_method():
    unit = concrete_instance_of(C2, ["concrete_method"])

    assert isinstance(unit, C2)
    assert type(unit) in {C2, C22}
