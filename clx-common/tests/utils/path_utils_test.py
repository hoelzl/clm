from pathlib import Path

from clx_common.utils.path_utils import Format, Lang, Kind, is_slides_file, output_specs, \
    simplify_ordered_name, ext_for


def test_is_slides_file():
    assert is_slides_file(Path("slides_1.py"))
    assert is_slides_file(Path("slides_2.cpp"))
    assert is_slides_file(Path("slides_3.md"))
    assert not is_slides_file(Path("slides4.py"))
    assert not is_slides_file(Path("test.py"))


def test_output_spec(course_1):
    unit = list(output_specs(course_1, Path("slides_1.py")))
    assert len(unit) == 14

    # Half the outputs should be in each language.
    assert len([os for os in unit if os.language == Lang.DE]) == 7
    assert len([os for os in unit if os.language == Lang.EN]) == 7

    # We generate HTML and notebook files for each language and mode, as well as for
    # public and speaker versions. Code files are only generated for completed mode.
    assert len([os for os in unit if os.format == Format.HTML]) == 6
    assert len([os for os in unit if os.format == Format.NOTEBOOK]) == 6
    assert len([os for os in unit if os.format == Format.CODE]) == 2

    # We have HTML and notebooks in 2 languages each for code-along and speaker
    # For completed, we have additionally the code files.
    assert len([os for os in unit if os.kind == Kind.CODE_ALONG]) == 4
    assert len([os for os in unit if os.kind == Kind.COMPLETED]) == 6
    assert len([os for os in unit if os.kind == Kind.SPEAKER]) == 4

    os1 = unit[0]
    assert os1.language == Lang.DE
    assert os1.format == Format.HTML
    assert os1.kind == Kind.CODE_ALONG


def test_simplify_ordered_name():
    assert simplify_ordered_name("topic_100_abc_def") == "abc_def"
    assert simplify_ordered_name("topic_100_abc_def.py") == "abc_def"


def test_ext_for_python():
    assert ext_for("html", "python") == ".html"
    assert ext_for("notebook", "python") == ".ipynb"
    assert ext_for("code", "python") == ".py"

def test_ext_for_cpp():
    assert ext_for("html", "cpp") == ".html"
    assert ext_for("notebook", "cpp") == ".ipynb"
    assert ext_for("code", "cpp") == ".cpp"

def test_ext_for_typescript():
    assert ext_for("html", "typescript") == ".html"
    assert ext_for("notebook", "typescript") == ".ipynb"
    assert ext_for("code", "typescript") == ".ts"
