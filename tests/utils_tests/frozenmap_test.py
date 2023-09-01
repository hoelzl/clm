from clm.utils.frozenmap import FrozenMap, frozenmap


def test_create_empty_frozenmap():
    fm = frozenmap()
    assert isinstance(fm, FrozenMap)
    assert len(fm) == 0


def test_create_frozenmap_with_two_items():
    fm = frozenmap({"a": 1, "b": 2})
    assert isinstance(fm, FrozenMap)
    assert len(fm) == 2
    assert fm["a"] == 1
    assert fm["b"] == 2


def test_create_frozenmap_with_keyword_args():
    fm = frozenmap(a=1, b=2)
    assert isinstance(fm, FrozenMap)
    assert len(fm) == 2
    assert fm["a"] == 1
    assert fm["b"] == 2


def test_frozenmap_contains():
    fm = frozenmap(a=1, b=2)
    assert "a" in fm
    assert "b" in fm
    assert "c" not in fm


def test_frozenmap_iter():
    fm = frozenmap(a=1, b=2)
    assert list(fm) == ["a", "b"]


def test_frozenmap_len():
    fm = frozenmap(a=1, b=2)
    assert len(fm) == 2


def test_frozenmap_hash():
    fm1 = frozenmap(a=1, b=2)
    fm2 = frozenmap(a=1, b=2)
    fm3 = frozenmap(a=1, b=3)
    assert hash(fm1) is not None
    assert hash(fm1) == hash(fm2)
    assert hash(fm1) != hash(fm3)


def test_frozenmap_replace():
    fm1 = frozenmap(a=1, b=2)
    fm2 = fm1.replace(b=3, c=4)
    assert fm1 is not fm2
    assert fm2.keys() == {"a", "b", "c"}
    assert fm2["a"] == 1
    assert fm2["b"] == 3
    assert fm2["c"] == 4


def test_frozenmap_replace_no_changes():
    fm1 = frozenmap(a=1, b=2)
    fm2 = fm1.replace()
    assert fm1 is fm2
    assert fm2.keys() == {"a", "b"}
    assert fm2["a"] == 1
    assert fm2["b"] == 2


def test_frozenmap_constructor_fun():
    fm = frozenmap({"a": 1, "b": 2})
    assert isinstance(fm, FrozenMap)
    assert len(fm) == 2


def test_frozenmap_constructor_fun_with_keyword_args():
    fm = frozenmap(a=1, b=2)
    assert isinstance(fm, FrozenMap)
    assert len(fm) == 2
    assert fm["a"] == 1
    assert fm["b"] == 2
