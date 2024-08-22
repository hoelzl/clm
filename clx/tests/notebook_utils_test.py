from clx.utils.notebook_utils import find_images, find_imports, find_notebook_titles
from clx.utils.text_utils import Text


def test_find_images():
    unit = """
    <img src="image1.png" style="color=blue">
    <img src="image2.png">
    """
    assert find_images(unit) == {"image1.png", "image2.png"}


def test_find_notebook_titles_when_header_exists():
    unit = """
    {{ header("De", "En") }}
    """
    assert find_notebook_titles(unit, "Default") == Text(de="De", en="En")


def test_find_notebook_titles_when_header_does_not_exist():
    unit = """
    Notebook without header
    """
    assert find_notebook_titles(unit, "Default") == Text(de="Default", en="Default")


def test_find_imports_for_import():
    unit = """
    import clx
    """
    assert find_imports(unit) == {"clx"}


def test_find_imports_for_from_import():
    unit = """
    from clx import text_utils
    """
    assert find_imports(unit) == {"clx"}


def test_find_imports_for_multiple_imports():
    unit = """
    import clx
    from clx import text_utils
    def test():
        pass
    import abc
    def test2():
        pass
    from abc import foo
    """
    assert find_imports(unit) == {"clx", "abc"}
