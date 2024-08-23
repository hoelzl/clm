from clx.utils.text_utils import Text, as_dir_name


def test_text_getitem():
    unit = Text(de="De", en="En")
    assert unit["de"] == "De"
    assert unit["en"] == "En"


def test_as_dir_name():
    assert as_dir_name("code", "de") == "Python"

