from pathlib import Path

from clm.core.course_spec import OutputTargetSpec
from clm.core.output_target import OutputTarget
from clm.core.utils.text_utils import Text
from clm.infrastructure.utils.path_utils import (
    Format,
    Kind,
    Lang,
    ext_for,
    is_slides_file,
    output_path_for,
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
    # 3 formats × 3 kinds × 2 languages = 18 outputs
    assert len(unit) == 18

    # Half the outputs should be in each language.
    assert len([os for os in unit if os.language == Lang.DE]) == 9
    assert len([os for os in unit if os.language == Lang.EN]) == 9

    # We generate HTML, notebook, and code files for each language and kind.
    assert len([os for os in unit if os.format == Format.HTML]) == 6
    assert len([os for os in unit if os.format == Format.NOTEBOOK]) == 6
    assert len([os for os in unit if os.format == Format.CODE]) == 6

    # Each kind has 3 formats × 2 languages = 6 outputs
    assert len([os for os in unit if os.kind == Kind.CODE_ALONG]) == 6
    assert len([os for os in unit if os.kind == Kind.COMPLETED]) == 6
    assert len([os for os in unit if os.kind == Kind.SPEAKER]) == 6

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

    # Should have 9 outputs (3 formats × 3 kinds × 1 language)
    assert len(unit) == 9

    # All outputs should be in German
    assert all(os.language == Lang.DE for os in unit)
    assert len([os for os in unit if os.language == Lang.EN]) == 0

    # Should still have all formats and kinds for German
    assert len([os for os in unit if os.format == Format.HTML]) == 3
    assert len([os for os in unit if os.format == Format.NOTEBOOK]) == 3
    assert len([os for os in unit if os.format == Format.CODE]) == 3

    assert len([os for os in unit if os.kind == Kind.CODE_ALONG]) == 3
    assert len([os for os in unit if os.kind == Kind.COMPLETED]) == 3
    assert len([os for os in unit if os.kind == Kind.SPEAKER]) == 3


def test_output_specs_single_language_en(course_1):
    """Test that output_specs filters to only English when languages=['en']."""
    unit = list(output_specs(course_1, Path("slides_1.py"), languages=["en"]))

    # Should have 9 outputs (3 formats × 3 kinds × 1 language)
    assert len(unit) == 9

    # All outputs should be in English
    assert all(os.language == Lang.EN for os in unit)
    assert len([os for os in unit if os.language == Lang.DE]) == 0


def test_output_specs_speaker_only(course_1):
    """Test that output_specs generates only speaker outputs when kinds=['speaker']."""
    unit = list(output_specs(course_1, Path("slides_1.py"), kinds=["speaker"]))

    # Should have 6 speaker outputs (3 formats × 2 languages)
    assert len(unit) == 6

    # All outputs should be speaker kind
    assert all(os.kind == Kind.SPEAKER for os in unit)

    # Should have both languages
    assert len([os for os in unit if os.language == Lang.DE]) == 3
    assert len([os for os in unit if os.language == Lang.EN]) == 3

    # Should have all formats including code
    assert len([os for os in unit if os.format == Format.HTML]) == 2
    assert len([os for os in unit if os.format == Format.NOTEBOOK]) == 2
    assert len([os for os in unit if os.format == Format.CODE]) == 2


def test_output_specs_speaker_only_single_language(course_1):
    """Test combining speaker-only with single language filter."""
    unit = list(output_specs(course_1, Path("slides_1.py"), languages=["en"], kinds=["speaker"]))

    # Should have 3 outputs (3 formats × 1 language)
    assert len(unit) == 3

    # All outputs should be English and speaker
    assert all(os.language == Lang.EN for os in unit)
    assert all(os.kind == Kind.SPEAKER for os in unit)

    # Should have all formats
    assert len([os for os in unit if os.format == Format.HTML]) == 1
    assert len([os for os in unit if os.format == Format.NOTEBOOK]) == 1
    assert len([os for os in unit if os.format == Format.CODE]) == 1


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

    # 3 formats × 2 languages = 6 outputs
    assert len(unit) == 6

    # All outputs should be code-along
    assert all(os.kind == Kind.CODE_ALONG for os in unit)

    # Code format is now generated for all kinds
    assert len([os for os in unit if os.format == Format.CODE]) == 2


def test_output_specs_multiple_kinds(course_1):
    """Test filtering to multiple specific kinds."""
    unit = list(output_specs(course_1, Path("slides_1.py"), kinds=["code-along", "completed"]))

    # 3 formats × 2 kinds × 2 languages = 12 outputs
    assert len(unit) == 12

    # Should have code-along and completed but not speaker
    assert len([os for os in unit if os.kind == Kind.CODE_ALONG]) == 6
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

    # Should have 2 outputs (1 language × 2 formats: notebook and code)
    assert len(unit) == 2

    # All outputs should be German and speaker
    assert all(os.language == Lang.DE for os in unit)
    assert all(os.kind == Kind.SPEAKER for os in unit)

    # Should have notebook and code formats, but no HTML
    assert len([os for os in unit if os.format == Format.NOTEBOOK]) == 1
    assert len([os for os in unit if os.format == Format.CODE]) == 1
    assert len([os for os in unit if os.format == Format.HTML]) == 0


# Tests for output_path_for with skip_toplevel


class TestOutputPathFor:
    """Tests for output_path_for function and skip_toplevel parameter."""

    def test_output_path_for_default_includes_public(self, tmp_path):
        """Test that by default, public output includes 'public' in path."""
        name = Text(de="Mein Kurs", en="My Course")
        path = output_path_for(tmp_path, is_speaker=False, lang="de", name=name)

        assert "public" in path.parts
        assert "De" in path.parts
        assert "Mein Kurs" in path.parts

    def test_output_path_for_default_includes_speaker(self, tmp_path):
        """Test that by default, speaker output includes 'speaker' in path."""
        name = Text(de="Mein Kurs", en="My Course")
        path = output_path_for(tmp_path, is_speaker=True, lang="de", name=name)

        assert "speaker" in path.parts
        assert "public" not in path.parts
        assert "De" in path.parts
        assert "Mein Kurs" in path.parts

    def test_output_path_for_skip_toplevel_excludes_public(self, tmp_path):
        """Test that skip_toplevel=True excludes 'public' from path."""
        name = Text(de="Mein Kurs", en="My Course")
        path = output_path_for(tmp_path, is_speaker=False, lang="de", name=name, skip_toplevel=True)

        assert "public" not in path.parts
        assert "speaker" not in path.parts
        assert "De" in path.parts
        assert "Mein Kurs" in path.parts

    def test_output_path_for_skip_toplevel_excludes_speaker(self, tmp_path):
        """Test that skip_toplevel=True excludes 'speaker' from path."""
        name = Text(de="Mein Kurs", en="My Course")
        path = output_path_for(tmp_path, is_speaker=True, lang="de", name=name, skip_toplevel=True)

        assert "speaker" not in path.parts
        assert "public" not in path.parts
        assert "De" in path.parts
        assert "Mein Kurs" in path.parts

    def test_output_path_for_both_audiences_same_with_skip_toplevel(self, tmp_path):
        """Test that with skip_toplevel, both audiences produce the same path."""
        name = Text(de="Mein Kurs", en="My Course")
        public_path = output_path_for(
            tmp_path, is_speaker=False, lang="de", name=name, skip_toplevel=True
        )
        speaker_path = output_path_for(
            tmp_path, is_speaker=True, lang="de", name=name, skip_toplevel=True
        )

        # Both paths should be identical when skip_toplevel=True
        assert public_path == speaker_path


class TestOutputSpecsWithExplicitTarget:
    """Tests for output_specs with explicit targets (skip_toplevel behavior)."""

    def test_output_specs_with_explicit_target_skips_toplevel(self, course_1, tmp_path):
        """Test that output_specs with explicit target skips public/speaker directories."""
        # Create an explicit target
        spec = OutputTargetSpec(name="test", path=str(tmp_path / "output"))
        target = OutputTarget.from_spec(spec, tmp_path)
        assert target.is_explicit is True

        # Get output specs with the target
        specs = list(
            output_specs(
                course_1,
                tmp_path / "output",
                languages=["en"],
                kinds=["completed"],
                target=target,
            )
        )

        # All specs should have paths without public/speaker
        for os in specs:
            assert "public" not in str(os.output_dir)
            assert "speaker" not in str(os.output_dir)

    def test_output_specs_with_default_target_includes_toplevel(self, course_1, tmp_path):
        """Test that output_specs with default target includes public/speaker directories."""
        # Create a default (non-explicit) target and apply filters to restrict to completed only
        target = OutputTarget.default_target(tmp_path / "output")
        assert target.is_explicit is False

        # Apply filters to restrict to completed kind only (which uses "public" directory)
        target = target.with_cli_filters(languages=["en"], kinds=["completed"])

        # Get output specs with the target
        specs = list(
            output_specs(
                course_1,
                tmp_path / "output",
                target=target,
            )
        )

        # All specs should have paths with public (completed is public, not speaker)
        for os in specs:
            assert "public" in str(os.output_dir)
            assert "speaker" not in str(os.output_dir)

    def test_output_specs_without_target_includes_toplevel(self, course_1, tmp_path):
        """Test that output_specs without target includes public/speaker directories."""
        # Get output specs without a target
        specs = list(
            output_specs(
                course_1,
                tmp_path / "output",
                languages=["en"],
                kinds=["speaker"],
            )
        )

        # All specs should have paths with speaker
        for os in specs:
            assert "speaker" in str(os.output_dir)
