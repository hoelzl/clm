from clm.utils.general import listify


def test_listify():
    assert listify("foo") == ["foo"]
    assert listify(["foo", "bar"]) == ["foo", "bar"]
    assert listify(1) == [1]
    assert listify((1, 2)) == [1, 2]
