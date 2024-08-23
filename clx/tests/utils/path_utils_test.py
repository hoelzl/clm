from pathlib import Path

from clx.utils.path_utils import Format, Lang, Mode, is_slides_file, output_specs, \
    simplify_ordered_name


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
    assert len([os for os in unit if os.lang == Lang.DE]) == 7
    assert len([os for os in unit if os.lang == Lang.EN]) == 7

    # We generate HTML and notebook files for each language and mode, as well as for
    # public and speaker versions. Code files are only generated for completed mode.
    assert len([os for os in unit if os.format == Format.HTML]) == 6
    assert len([os for os in unit if os.format == Format.NOTEBOOK]) == 6
    assert len([os for os in unit if os.format == Format.CODE]) == 2

    # We have HTML and notebooks in 2 languages each for code-along and speaker
    # For completed, we have additionally the code files.
    assert len([os for os in unit if os.mode == Mode.CODE_ALONG]) == 4
    assert len([os for os in unit if os.mode == Mode.COMPLETED]) == 6
    assert len([os for os in unit if os.mode == Mode.SPEAKER]) == 4

    os1 = unit[0]
    assert os1.lang == Lang.DE
    assert os1.format == Format.HTML
    assert os1.mode == Mode.CODE_ALONG


def test_simplify_ordered_name():
    assert simplify_ordered_name("topic_100_abc_def") == "abc_def"
    assert simplify_ordered_name("topic_100_abc_def.py") == "abc_def"
