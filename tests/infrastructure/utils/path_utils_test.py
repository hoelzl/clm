from pathlib import Path

from clx.infrastructure.utils.path_utils import (
    Format,
    Kind,
    Lang,
    ext_for,
    is_slides_file,
    output_specs,
    simplify_ordered_name,
)


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


# Tests for output_specs filtering


def test_output_specs_single_language_de(course_1):
    """Test that output_specs filters to only German when languages=['de']."""
    unit = list(output_specs(course_1, Path("slides_1.py"), languages=["de"]))

    # Should have 7 outputs (half of the 14 total)
    assert len(unit) == 7

    # All outputs should be in German
    assert all(os.language == Lang.DE for os in unit)
    assert len([os for os in unit if os.language == Lang.EN]) == 0

    # Should still have all formats and kinds for German
    assert len([os for os in unit if os.format == Format.HTML]) == 3
    assert len([os for os in unit if os.format == Format.NOTEBOOK]) == 3
    assert len([os for os in unit if os.format == Format.CODE]) == 1

    assert len([os for os in unit if os.kind == Kind.CODE_ALONG]) == 2
    assert len([os for os in unit if os.kind == Kind.COMPLETED]) == 3
    assert len([os for os in unit if os.kind == Kind.SPEAKER]) == 2


def test_output_specs_single_language_en(course_1):
    """Test that output_specs filters to only English when languages=['en']."""
    unit = list(output_specs(course_1, Path("slides_1.py"), languages=["en"]))

    # Should have 7 outputs (half of the 14 total)
    assert len(unit) == 7

    # All outputs should be in English
    assert all(os.language == Lang.EN for os in unit)
    assert len([os for os in unit if os.language == Lang.DE]) == 0


def test_output_specs_speaker_only(course_1):
    """Test that output_specs generates only speaker outputs when kinds=['speaker']."""
    unit = list(output_specs(course_1, Path("slides_1.py"), kinds=["speaker"]))

    # Should have 4 speaker outputs (2 languages x 2 formats)
    assert len(unit) == 4

    # All outputs should be speaker kind
    assert all(os.kind == Kind.SPEAKER for os in unit)

    # Should have both languages
    assert len([os for os in unit if os.language == Lang.DE]) == 2
    assert len([os for os in unit if os.language == Lang.EN]) == 2

    # Should have HTML and notebook formats, but no code
    assert len([os for os in unit if os.format == Format.HTML]) == 2
    assert len([os for os in unit if os.format == Format.NOTEBOOK]) == 2
    assert len([os for os in unit if os.format == Format.CODE]) == 0


def test_output_specs_speaker_only_single_language(course_1):
    """Test combining speaker-only with single language filter."""
    unit = list(output_specs(course_1, Path("slides_1.py"), languages=["en"], kinds=["speaker"]))

    # Should have 2 outputs (1 language x 2 formats)
    assert len(unit) == 2

    # All outputs should be English and speaker
    assert all(os.language == Lang.EN for os in unit)
    assert all(os.kind == Kind.SPEAKER for os in unit)

    # Should have HTML and notebook
    assert len([os for os in unit if os.format == Format.HTML]) == 1
    assert len([os for os in unit if os.format == Format.NOTEBOOK]) == 1


def test_output_specs_completed_only(course_1):
    """Test filtering to only completed outputs."""
    unit = list(output_specs(course_1, Path("slides_1.py"), kinds=["completed"]))

    # 2 languages x (2 HTML/notebook + 1 code) = 6 outputs
    assert len(unit) == 6

    # All outputs should be completed
    assert all(os.kind == Kind.COMPLETED for os in unit)

    # Should have code outputs
    assert len([os for os in unit if os.format == Format.CODE]) == 2


def test_output_specs_code_along_only(course_1):
    """Test filtering to only code-along outputs."""
    unit = list(output_specs(course_1, Path("slides_1.py"), kinds=["code-along"]))

    # 2 languages x 2 formats = 4 outputs (no code format for code-along)
    assert len(unit) == 4

    # All outputs should be code-along
    assert all(os.kind == Kind.CODE_ALONG for os in unit)

    # No code format for code-along
    assert len([os for os in unit if os.format == Format.CODE]) == 0


def test_output_specs_multiple_kinds(course_1):
    """Test filtering to multiple specific kinds."""
    unit = list(output_specs(course_1, Path("slides_1.py"), kinds=["code-along", "completed"]))

    # 2 languages x 2 formats x 2 kinds + 2 code files = 10 outputs
    assert len(unit) == 10

    # Should have code-along and completed but not speaker
    assert len([os for os in unit if os.kind == Kind.CODE_ALONG]) == 4
    assert len([os for os in unit if os.kind == Kind.COMPLETED]) == 6
    assert len([os for os in unit if os.kind == Kind.SPEAKER]) == 0


def test_output_specs_with_skip_html_and_filters(course_1):
    """Test that skip_html works together with language and kinds filters."""
    unit = list(
        output_specs(
            course_1,
            Path("slides_1.py"),
            skip_html=True,
            languages=["de"],
            kinds=["speaker"],
        )
    )

    # Should have 1 output (1 language x 1 notebook format)
    assert len(unit) == 1

    os = unit[0]
    assert os.language == Lang.DE
    assert os.format == Format.NOTEBOOK
    assert os.kind == Kind.SPEAKER
