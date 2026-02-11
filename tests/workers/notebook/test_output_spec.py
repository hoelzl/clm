"""Tests for output_spec module.

This module tests all output specification classes including:
- OutputSpec base class
- CompletedOutput
- CodeAlongOutput
- SpeakerOutput
- Factory functions create_output_spec and create_output_specs
"""

import pytest
from nbformat import NotebookNode

from clm.workers.notebook.output_spec import (
    CodeAlongOutput,
    CompletedOutput,
    OutputSpec,
    SpeakerOutput,
    create_output_spec,
    create_output_specs,
)


# Fixtures for creating mock cells
@pytest.fixture
def make_cell():
    """Factory fixture to create mock notebook cells using NotebookNode."""

    def _make_cell(
        cell_type: str = "code", tags: list[str] | None = None, source: str = "", lang: str = ""
    ):
        metadata = {"tags": tags or []}
        if lang:
            metadata["lang"] = lang
        # NotebookNode supports both dict and attribute access
        return NotebookNode(
            {
                "cell_type": cell_type,
                "source": source,
                "metadata": metadata,
            }
        )

    return _make_cell


@pytest.fixture
def code_cell(make_cell):
    """Create a basic code cell."""
    return make_cell("code", [], "print('hello')")


@pytest.fixture
def markdown_cell(make_cell):
    """Create a basic markdown cell."""
    return make_cell("markdown", [], "# Header")


class TestOutputSpecBase:
    """Test the OutputSpec base class properties and methods."""

    def test_completed_output_has_correct_defaults(self):
        """CompletedOutput should have correct default values."""
        spec = CompletedOutput()
        assert spec.language == "en"
        assert spec.prog_lang == "python"
        assert spec.format == "code"
        assert spec.delete_any_cell_contents is False

    def test_code_along_output_has_correct_defaults(self):
        """CodeAlongOutput should have correct default values."""
        spec = CodeAlongOutput()
        assert spec.language == "en"
        assert spec.prog_lang == "python"
        assert spec.format == "code"
        assert spec.delete_any_cell_contents is True

    def test_speaker_output_has_correct_defaults(self):
        """SpeakerOutput should have correct default values."""
        spec = SpeakerOutput()
        assert spec.language == "en"
        assert spec.prog_lang == "python"
        assert spec.format == "code"
        assert spec.delete_any_cell_contents is False


class TestFileSuffix:
    """Test the file_suffix property for all output specs."""

    def test_file_suffix_notebook_format(self):
        """Notebook format should return .ipynb suffix."""
        spec = CompletedOutput(format="notebook")
        assert spec.file_suffix == ".ipynb"

    def test_file_suffix_html_format(self):
        """HTML format should return .html suffix."""
        spec = CompletedOutput(format="html")
        assert spec.file_suffix == ".html"

    def test_file_suffix_code_format_python(self):
        """Code format with Python should return .py suffix."""
        spec = CompletedOutput(format="code", prog_lang="python")
        assert spec.file_suffix == ".py"

    def test_file_suffix_code_format_cpp(self):
        """Code format with C++ should return .cpp suffix."""
        spec = CompletedOutput(format="code", prog_lang="cpp")
        assert spec.file_suffix == ".cpp"

    def test_file_suffix_code_format_java(self):
        """Code format with Java should return .java suffix."""
        spec = CompletedOutput(format="code", prog_lang="java")
        assert spec.file_suffix == ".java"

    def test_file_suffix_code_format_csharp(self):
        """Code format with C# should return .cs suffix."""
        spec = CompletedOutput(format="code", prog_lang="csharp")
        assert spec.file_suffix == ".cs"

    def test_file_suffix_code_format_typescript(self):
        """Code format with TypeScript should return .ts suffix."""
        spec = CompletedOutput(format="code", prog_lang="typescript")
        assert spec.file_suffix == ".ts"

    def test_file_suffix_code_format_rust(self):
        """Code format with Rust should return .rs suffix."""
        spec = CompletedOutput(format="code", prog_lang="rust")
        assert spec.file_suffix == ".rs"

    def test_file_suffix_edit_script_format(self):
        """Edit script format should return .ahk suffix."""
        spec = CompletedOutput(format="edit_script")
        assert spec.file_suffix == ".ahk"

    def test_file_suffix_unknown_format_raises_error(self):
        """Unknown format should raise ValueError."""
        spec = CompletedOutput(format="unknown_format")
        with pytest.raises(ValueError, match="Could not extract file suffix"):
            _ = spec.file_suffix


class TestJupytextFormat:
    """Test the jupytext_format property for all output specs."""

    def test_jupytext_format_notebook(self):
        """Notebook format should return 'ipynb' jupytext format."""
        spec = SpeakerOutput(format="notebook")
        assert spec.jupytext_format == "ipynb"

    def test_jupytext_format_html(self):
        """HTML format should return 'html' jupytext format."""
        spec = SpeakerOutput(format="html")
        assert spec.jupytext_format == "html"

    def test_jupytext_format_code_python(self):
        """Code format with Python should return 'py:percent'."""
        spec = SpeakerOutput(format="code", prog_lang="python")
        assert spec.jupytext_format == "py:percent"

    def test_jupytext_format_code_cpp(self):
        """Code format with C++ should return 'cpp:percent'."""
        spec = SpeakerOutput(format="code", prog_lang="cpp")
        assert spec.jupytext_format == "cpp:percent"

    def test_jupytext_format_edit_script(self):
        """Edit script format should return 'py:percent'."""
        spec = SpeakerOutput(format="edit_script")
        assert spec.jupytext_format == "py:percent"

    def test_jupytext_format_unknown_raises_error(self):
        """Unknown format should raise ValueError."""
        spec = SpeakerOutput(format="unknown_format")
        with pytest.raises(ValueError, match="Could not extract jupytext format"):
            _ = spec.jupytext_format


class TestPathFragment:
    """Test the path_fragment property for all output specs."""

    def test_path_fragment_completed_output(self):
        """CompletedOutput path_fragment should include 'completed'."""
        spec = CompletedOutput(language="en", format="notebook")
        assert spec.path_fragment == "en/notebook/completed"

    def test_path_fragment_code_along_output(self):
        """CodeAlongOutput path_fragment should include 'code_along'."""
        spec = CodeAlongOutput(language="de", format="code")
        assert spec.path_fragment == "de/code/code_along"

    def test_path_fragment_speaker_output(self):
        """SpeakerOutput path_fragment should include 'speaker'."""
        spec = SpeakerOutput(language="en", format="html")
        assert spec.path_fragment == "en/html/speaker"


class TestCompletedOutputCellInclusion:
    """Test cell inclusion logic for CompletedOutput."""

    def test_completed_excludes_deleted_cells(self, make_cell):
        """Cells with 'del' tag should be excluded."""
        spec = CompletedOutput()
        cell = make_cell("code", ["del"])
        assert spec.is_cell_included(cell) is False

    def test_completed_excludes_notes_cells(self, make_cell):
        """Cells with 'notes' tag should be excluded."""
        spec = CompletedOutput()
        cell = make_cell("markdown", ["notes"])
        assert spec.is_cell_included(cell) is False

    def test_completed_excludes_start_cells(self, make_cell):
        """Cells with 'start' tag should be excluded."""
        spec = CompletedOutput()
        cell = make_cell("code", ["start"])
        assert spec.is_cell_included(cell) is False

    def test_completed_includes_regular_code_cells(self, make_cell):
        """Regular code cells should be included."""
        spec = CompletedOutput()
        cell = make_cell("code", [])
        assert spec.is_cell_included(cell) is True

    def test_completed_includes_regular_markdown_cells(self, make_cell):
        """Regular markdown cells should be included."""
        spec = CompletedOutput()
        cell = make_cell("markdown", [])
        assert spec.is_cell_included(cell) is True

    def test_completed_includes_alt_cells(self, make_cell):
        """Cells with 'alt' tag should be included in completed."""
        spec = CompletedOutput()
        cell = make_cell("code", ["alt"])
        assert spec.is_cell_included(cell) is True

    def test_completed_includes_keep_cells(self, make_cell):
        """Cells with 'keep' tag should be included."""
        spec = CompletedOutput()
        cell = make_cell("code", ["keep"])
        assert spec.is_cell_included(cell) is True

    def test_completed_includes_answer_cells(self, make_cell):
        """Cells with 'answer' tag should be included."""
        spec = CompletedOutput()
        cell = make_cell("markdown", ["answer"])
        assert spec.is_cell_included(cell) is True

    def test_completed_contents_always_included(self, make_cell):
        """CompletedOutput should always include cell contents."""
        spec = CompletedOutput()
        cell = make_cell("code", [])
        assert spec.is_cell_contents_included(cell) is True

    def test_completed_evaluate_for_html_is_true(self):
        """CompletedOutput should have evaluate_for_html=True."""
        spec = CompletedOutput()
        assert spec.evaluate_for_html is True


class TestCodeAlongOutputCellInclusion:
    """Test cell inclusion logic for CodeAlongOutput."""

    def test_code_along_excludes_deleted_cells(self, make_cell):
        """Cells with 'del' tag should be excluded."""
        spec = CodeAlongOutput()
        cell = make_cell("code", ["del"])
        assert spec.is_cell_included(cell) is False

    def test_code_along_excludes_notes_cells(self, make_cell):
        """Cells with 'notes' tag should be excluded."""
        spec = CodeAlongOutput()
        cell = make_cell("markdown", ["notes"])
        assert spec.is_cell_included(cell) is False

    def test_code_along_excludes_alt_cells(self, make_cell):
        """Cells with 'alt' tag should be excluded from code along."""
        spec = CodeAlongOutput()
        cell = make_cell("code", ["alt"])
        assert spec.is_cell_included(cell) is False

    def test_code_along_includes_start_cells(self, make_cell):
        """Cells with 'start' tag should be included (but not 'del')."""
        spec = CodeAlongOutput()
        cell = make_cell("code", ["start"])
        assert spec.is_cell_included(cell) is True

    def test_code_along_clears_code_cell_contents(self, make_cell):
        """Code cells should have contents cleared."""
        spec = CodeAlongOutput()
        cell = make_cell("code", [], "some code")
        assert spec.is_cell_contents_included(cell) is False

    def test_code_along_keeps_keep_tagged_contents(self, make_cell):
        """Code cells with 'keep' tag should retain contents."""
        spec = CodeAlongOutput()
        cell = make_cell("code", ["keep"], "important code")
        assert spec.is_cell_contents_included(cell) is True

    def test_code_along_keeps_start_tagged_contents(self, make_cell):
        """Code cells with 'start' tag should retain contents."""
        spec = CodeAlongOutput()
        cell = make_cell("code", ["start"], "starting code")
        assert spec.is_cell_contents_included(cell) is True

    def test_code_along_keeps_regular_markdown_contents(self, make_cell):
        """Regular markdown cells should retain contents."""
        spec = CodeAlongOutput()
        cell = make_cell("markdown", [], "# Header")
        assert spec.is_cell_contents_included(cell) is True

    def test_code_along_clears_answer_markdown_contents(self, make_cell):
        """Markdown cells with 'answer' tag should have contents cleared."""
        spec = CodeAlongOutput()
        cell = make_cell("markdown", ["answer"], "The answer is...")
        assert spec.is_cell_contents_included(cell) is False


class TestSpeakerOutputCellInclusion:
    """Test cell inclusion logic for SpeakerOutput."""

    def test_speaker_excludes_deleted_cells(self, make_cell):
        """Cells with 'del' tag should be excluded."""
        spec = SpeakerOutput()
        cell = make_cell("code", ["del"])
        assert spec.is_cell_included(cell) is False

    def test_speaker_excludes_start_cells(self, make_cell):
        """Cells with 'start' tag should be excluded."""
        spec = SpeakerOutput()
        cell = make_cell("code", ["start"])
        assert spec.is_cell_included(cell) is False

    def test_speaker_includes_notes_cells(self, make_cell):
        """Cells with 'notes' tag should be included in speaker output."""
        spec = SpeakerOutput()
        cell = make_cell("markdown", ["notes"])
        assert spec.is_cell_included(cell) is True

    def test_speaker_includes_private_cells(self, make_cell):
        """Cells with 'private' tag should be included in speaker output."""
        spec = SpeakerOutput()
        cell = make_cell("code", ["private"])
        assert spec.is_cell_included(cell) is True

    def test_speaker_contents_always_included(self, make_cell):
        """SpeakerOutput should always include cell contents."""
        spec = SpeakerOutput()
        cell = make_cell("code", [], "some code")
        assert spec.is_cell_contents_included(cell) is True

    def test_speaker_evaluate_for_html_is_true(self):
        """SpeakerOutput should have evaluate_for_html=True."""
        spec = SpeakerOutput()
        assert spec.evaluate_for_html is True


class TestLanguageFiltering:
    """Test language-based cell filtering."""

    def test_cell_without_language_included_in_all(self, make_cell):
        """Cells without language metadata should be included for all languages."""
        spec = CompletedOutput(language="en")
        cell = make_cell("code", [], "code")
        assert spec.is_cell_included(cell) is True

        spec_de = CompletedOutput(language="de")
        assert spec_de.is_cell_included(cell) is True

    def test_cell_with_matching_language_included(self, make_cell):
        """Cells with matching language should be included."""
        spec = CompletedOutput(language="en")
        cell = make_cell("code", [], "english code", lang="en")
        assert spec.is_cell_included(cell) is True

    def test_cell_with_different_language_excluded(self, make_cell):
        """Cells with different language should be excluded."""
        spec = CompletedOutput(language="en")
        cell = make_cell("code", [], "german code", lang="de")
        assert spec.is_cell_included(cell) is False

    def test_german_spec_excludes_english_cells(self, make_cell):
        """German spec should exclude English-only cells."""
        spec = CompletedOutput(language="de")
        cell = make_cell("markdown", [], "English text", lang="en")
        assert spec.is_cell_included(cell) is False


class TestCreateOutputSpec:
    """Test the create_output_spec factory function."""

    def test_create_completed_spec(self):
        """'completed' should create CompletedOutput."""
        spec = create_output_spec("completed")
        assert isinstance(spec, CompletedOutput)

    def test_create_code_along_spec(self):
        """'code-along' should create CodeAlongOutput."""
        spec = create_output_spec("code-along")
        assert isinstance(spec, CodeAlongOutput)

    def test_create_speaker_spec(self):
        """'speaker' should create SpeakerOutput."""
        spec = create_output_spec("speaker")
        assert isinstance(spec, SpeakerOutput)

    def test_create_spec_case_insensitive(self):
        """Spec creation should be case-insensitive."""
        assert isinstance(create_output_spec("Completed"), CompletedOutput)
        assert isinstance(create_output_spec("SPEAKER"), SpeakerOutput)
        assert isinstance(create_output_spec("Code-Along"), CodeAlongOutput)

    def test_create_spec_with_language(self):
        """Should be able to create spec with specific language."""
        spec = create_output_spec("completed", language="de")
        assert spec.language == "de"

    def test_create_spec_with_format(self):
        """Should be able to create spec with specific format."""
        spec = create_output_spec("completed", format="html")
        assert spec.format == "html"

    def test_create_spec_with_prog_lang(self):
        """Should be able to create spec with specific programming language."""
        spec = create_output_spec("completed", prog_lang="cpp")
        assert spec.prog_lang == "cpp"

    def test_create_unknown_spec_raises_error(self):
        """Unknown spec type should raise ValueError."""
        with pytest.raises(ValueError, match="Unknown spec type"):
            create_output_spec("unknown")


class TestCreateOutputSpecs:
    """Test the create_output_specs factory function."""

    def test_create_default_specs(self):
        """Default call should create specs for all combinations."""
        specs = create_output_specs()

        # 2 languages x 3 formats x 3 kinds = 18 specs
        assert len(specs) == 18

        # Check that we have all types
        completed_count = sum(1 for s in specs if isinstance(s, CompletedOutput))
        code_along_count = sum(1 for s in specs if isinstance(s, CodeAlongOutput))
        speaker_count = sum(1 for s in specs if isinstance(s, SpeakerOutput))

        assert completed_count == 6  # 2 languages x 3 formats
        assert code_along_count == 6
        assert speaker_count == 6

    def test_create_specs_with_single_language(self):
        """Should create specs for single language."""
        specs = create_output_specs(languages=("en",))
        assert len(specs) == 9  # 1 language x 3 formats x 3 kinds

        for spec in specs:
            assert spec.language == "en"

    def test_create_specs_with_single_format(self):
        """Should create specs for single format."""
        specs = create_output_specs(formats=("notebook",))
        assert len(specs) == 6  # 2 languages x 1 format x 3 kinds

        for spec in specs:
            assert spec.format == "notebook"

    def test_create_specs_with_single_kind(self):
        """Should create specs for single kind."""
        specs = create_output_specs(kinds=("completed",))
        assert len(specs) == 6  # 2 languages x 3 formats x 1 kind

        for spec in specs:
            assert isinstance(spec, CompletedOutput)

    def test_create_specs_with_prog_lang(self):
        """Should create all specs with specified programming language."""
        specs = create_output_specs(prog_lang="java")

        for spec in specs:
            assert spec.prog_lang == "java"

    def test_create_specs_empty_input(self):
        """Empty inputs should create no specs."""
        specs = create_output_specs(languages=())
        assert len(specs) == 0

        specs = create_output_specs(formats=())
        assert len(specs) == 0

        specs = create_output_specs(kinds=())
        assert len(specs) == 0

    def test_create_specs_custom_combination(self):
        """Custom combination should work correctly."""
        specs = create_output_specs(
            prog_lang="typescript",
            languages=("de",),
            formats=("code", "html"),
            kinds=("completed", "speaker"),
        )

        assert len(specs) == 4  # 1 language x 2 formats x 2 kinds

        for spec in specs:
            assert spec.prog_lang == "typescript"
            assert spec.language == "de"
            assert spec.format in ("code", "html")
            assert isinstance(spec, (CompletedOutput, SpeakerOutput))


class TestTagsToDeleteCell:
    """Test the tags_to_delete_cell class attributes."""

    def test_completed_tags_to_delete(self):
        """CompletedOutput should delete del, notes, and start cells."""
        spec = CompletedOutput()
        assert "del" in spec.tags_to_delete_cell
        assert "notes" in spec.tags_to_delete_cell
        assert "start" in spec.tags_to_delete_cell

    def test_code_along_tags_to_delete(self):
        """CodeAlongOutput should delete del, notes, and alt cells."""
        spec = CodeAlongOutput()
        assert "del" in spec.tags_to_delete_cell
        assert "notes" in spec.tags_to_delete_cell
        assert "alt" in spec.tags_to_delete_cell
        assert "start" not in spec.tags_to_delete_cell

    def test_speaker_tags_to_delete(self):
        """SpeakerOutput should delete del and start cells."""
        spec = SpeakerOutput()
        assert "del" in spec.tags_to_delete_cell
        assert "start" in spec.tags_to_delete_cell
        assert "notes" not in spec.tags_to_delete_cell


class TestEdgeCases:
    """Test edge cases and special scenarios."""

    def test_cell_with_multiple_tags(self, make_cell):
        """Cell with multiple tags should be evaluated correctly."""
        spec = CompletedOutput()
        # Cell has both 'del' and 'keep' - should be excluded because of 'del'
        cell = make_cell("code", ["del", "keep"])
        assert spec.is_cell_included(cell) is False

    def test_cell_with_empty_tags_list(self, make_cell):
        """Cell with empty tags list should be included."""
        spec = CompletedOutput()
        cell = make_cell("code", [])
        assert spec.is_cell_included(cell) is True
        assert spec.is_cell_contents_included(cell) is True

    def test_code_along_code_cell_with_multiple_retain_tags(self, make_cell):
        """Code cell with multiple retain tags should keep contents."""
        spec = CodeAlongOutput()
        cell = make_cell("code", ["keep", "start"])
        assert spec.is_cell_contents_included(cell) is True

    def test_subdir_fragments_are_unique(self):
        """Each output type should have a unique subdir fragment."""
        completed = CompletedOutput().get_target_subdir_fragment()
        code_along = CodeAlongOutput().get_target_subdir_fragment()
        speaker = SpeakerOutput().get_target_subdir_fragment()

        fragments = {completed, code_along, speaker}
        assert len(fragments) == 3, "All subdir fragments should be unique"
