"""Tests for output_spec module.

This module tests all output specification classes including:
- OutputSpec base class
- CompletedOutput
- CodeAlongOutput
- TrainerOutput (notes only — strips voiceover)
- RecordingOutput (notes + voiceover; cache producer)
- SpeakerOutput (deprecated alias for RecordingOutput)
- Factory functions create_output_spec and create_output_specs
"""

import pytest
from nbformat import NotebookNode

from clm.workers.notebook.output_spec import (
    POST_WORKSHOP_TAG,
    CodeAlongOutput,
    CompletedOutput,
    OutputSpec,
    PartialOutput,
    RecordingOutput,
    SpeakerOutput,
    TrainerOutput,
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

    def test_recording_output_has_correct_defaults(self):
        """RecordingOutput should have correct default values."""
        spec = RecordingOutput()
        assert spec.language == "en"
        assert spec.prog_lang == "python"
        assert spec.format == "code"
        assert spec.delete_any_cell_contents is False

    def test_trainer_output_has_correct_defaults(self):
        """TrainerOutput should have correct default values."""
        spec = TrainerOutput()
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
        spec = RecordingOutput(format="notebook")
        assert spec.jupytext_format == "ipynb"

    def test_jupytext_format_html(self):
        """HTML format should return 'html' jupytext format."""
        spec = RecordingOutput(format="html")
        assert spec.jupytext_format == "html"

    def test_jupytext_format_code_python(self):
        """Code format with Python should return 'py:percent'."""
        spec = RecordingOutput(format="code", prog_lang="python")
        assert spec.jupytext_format == "py:percent"

    def test_jupytext_format_code_cpp(self):
        """Code format with C++ should return 'cpp:percent'."""
        spec = RecordingOutput(format="code", prog_lang="cpp")
        assert spec.jupytext_format == "cpp:percent"

    def test_jupytext_format_edit_script(self):
        """Edit script format should return 'py:percent'."""
        spec = RecordingOutput(format="edit_script")
        assert spec.jupytext_format == "py:percent"

    def test_jupytext_format_unknown_raises_error(self):
        """Unknown format should raise ValueError."""
        spec = RecordingOutput(format="unknown_format")
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

    def test_path_fragment_recording_output(self):
        """RecordingOutput path_fragment should include 'recording'."""
        spec = RecordingOutput(language="en", format="html")
        assert spec.path_fragment == "en/html/recording"

    def test_path_fragment_trainer_output(self):
        """TrainerOutput path_fragment should include 'trainer'."""
        spec = TrainerOutput(language="en", format="html")
        assert spec.path_fragment == "en/html/trainer"

    def test_path_fragment_speaker_alias_resolves_to_recording(self):
        """The deprecated ``SpeakerOutput`` alias produces ``recording``-shaped paths."""
        spec = SpeakerOutput(language="en", format="html")
        assert spec.path_fragment == "en/html/recording"


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


class TestRecordingOutputCellInclusion:
    """Test cell inclusion logic for RecordingOutput (notes + voiceover both kept)."""

    def test_recording_excludes_deleted_cells(self, make_cell):
        spec = RecordingOutput()
        cell = make_cell("code", ["del"])
        assert spec.is_cell_included(cell) is False

    def test_recording_excludes_start_cells(self, make_cell):
        spec = RecordingOutput()
        cell = make_cell("code", ["start"])
        assert spec.is_cell_included(cell) is False

    def test_recording_includes_notes_cells(self, make_cell):
        spec = RecordingOutput()
        cell = make_cell("markdown", ["notes"])
        assert spec.is_cell_included(cell) is True

    def test_recording_includes_voiceover_cells(self, make_cell):
        """Recording is the variant that keeps voiceover cells for video narration."""
        spec = RecordingOutput()
        cell = make_cell("markdown", ["voiceover"])
        assert spec.is_cell_included(cell) is True

    def test_recording_includes_private_cells(self, make_cell):
        spec = RecordingOutput()
        cell = make_cell("code", ["private"])
        assert spec.is_cell_included(cell) is True

    def test_recording_contents_always_included(self, make_cell):
        spec = RecordingOutput()
        cell = make_cell("code", [], "some code")
        assert spec.is_cell_contents_included(cell) is True

    def test_recording_evaluate_for_html_is_true(self):
        spec = RecordingOutput()
        assert spec.evaluate_for_html is True

    def test_recording_caches_html_execution(self):
        """Recording HTML is the cache producer for trainer/completed/partial reuse."""
        assert RecordingOutput(format="html").should_cache_execution is True
        assert RecordingOutput(format="notebook").should_cache_execution is False
        assert RecordingOutput(format="code").should_cache_execution is False


class TestTrainerOutputCellInclusion:
    """TrainerOutput keeps speaker notes but strips voiceover cells."""

    def test_trainer_excludes_deleted_cells(self, make_cell):
        spec = TrainerOutput()
        cell = make_cell("code", ["del"])
        assert spec.is_cell_included(cell) is False

    def test_trainer_excludes_start_cells(self, make_cell):
        spec = TrainerOutput()
        cell = make_cell("code", ["start"])
        assert spec.is_cell_included(cell) is False

    def test_trainer_excludes_voiceover_cells(self, make_cell):
        """Trainer is the variant that strips voiceover (only Recording keeps it)."""
        spec = TrainerOutput()
        cell = make_cell("markdown", ["voiceover"])
        assert spec.is_cell_included(cell) is False

    def test_trainer_includes_notes_cells(self, make_cell):
        """Trainer keeps speaker notes — that's the whole point of the variant."""
        spec = TrainerOutput()
        cell = make_cell("markdown", ["notes"])
        assert spec.is_cell_included(cell) is True

    def test_trainer_contents_always_included(self, make_cell):
        spec = TrainerOutput()
        cell = make_cell("code", [], "some code")
        assert spec.is_cell_contents_included(cell) is True

    def test_trainer_evaluate_for_html_is_true(self):
        assert TrainerOutput().evaluate_for_html is True

    def test_trainer_reuses_recording_cache_for_html(self):
        assert TrainerOutput(format="html").can_reuse_execution is True
        assert TrainerOutput(format="notebook").can_reuse_execution is False

    def test_trainer_does_not_populate_cache(self):
        assert TrainerOutput(format="html").should_cache_execution is False


class TestSpeakerOutputDeprecatedAlias:
    """``SpeakerOutput`` is preserved as an alias of ``RecordingOutput``."""

    def test_speaker_output_is_recording_output(self):
        """The legacy name now resolves to the same class as Recording."""
        assert SpeakerOutput is RecordingOutput

    def test_speaker_output_keeps_voiceover(self, make_cell):
        """Aliased class behaves like Recording: voiceover cells stay."""
        spec = SpeakerOutput()
        cell = make_cell("markdown", ["voiceover"])
        assert spec.is_cell_included(cell) is True


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

    def test_create_trainer_spec(self):
        """'trainer' should create TrainerOutput."""
        spec = create_output_spec("trainer")
        assert isinstance(spec, TrainerOutput)

    def test_create_recording_spec(self):
        """'recording' should create RecordingOutput."""
        spec = create_output_spec("recording")
        assert isinstance(spec, RecordingOutput)

    def test_create_speaker_spec_emits_deprecation_and_returns_recording(self):
        """The deprecated 'speaker' input still resolves to RecordingOutput."""
        with pytest.warns(DeprecationWarning, match="'speaker' is deprecated"):
            spec = create_output_spec("speaker")
        assert isinstance(spec, RecordingOutput)

    def test_create_spec_case_insensitive(self):
        """Spec creation should be case-insensitive."""
        assert isinstance(create_output_spec("Completed"), CompletedOutput)
        assert isinstance(create_output_spec("Recording"), RecordingOutput)
        assert isinstance(create_output_spec("TRAINER"), TrainerOutput)
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

        # 2 languages x 3 formats x 5 kinds = 30 specs
        # (kinds: completed, code-along, trainer, recording, partial)
        assert len(specs) == 30

        # Check that we have all types
        completed_count = sum(1 for s in specs if isinstance(s, CompletedOutput))
        code_along_count = sum(1 for s in specs if isinstance(s, CodeAlongOutput))
        trainer_count = sum(1 for s in specs if isinstance(s, TrainerOutput))
        recording_count = sum(1 for s in specs if isinstance(s, RecordingOutput))
        partial_count = sum(1 for s in specs if isinstance(s, PartialOutput))

        assert completed_count == 6  # 2 languages x 3 formats
        assert code_along_count == 6
        assert trainer_count == 6
        assert recording_count == 6
        assert partial_count == 6

    def test_create_specs_with_single_language(self):
        """Should create specs for single language."""
        specs = create_output_specs(languages=("en",))
        assert len(specs) == 15  # 1 language x 3 formats x 5 kinds

        for spec in specs:
            assert spec.language == "en"

    def test_create_specs_with_single_format(self):
        """Should create specs for single format."""
        specs = create_output_specs(formats=("notebook",))
        assert len(specs) == 10  # 2 languages x 1 format x 5 kinds

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
            kinds=("completed", "recording"),
        )

        assert len(specs) == 4  # 1 language x 2 formats x 2 kinds

        for spec in specs:
            assert spec.prog_lang == "typescript"
            assert spec.language == "de"
            assert spec.format in ("code", "html")
            assert isinstance(spec, (CompletedOutput, RecordingOutput))


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

    def test_recording_tags_to_delete(self):
        """RecordingOutput should delete del and start cells (keeps notes + voiceover)."""
        spec = RecordingOutput()
        assert "del" in spec.tags_to_delete_cell
        assert "start" in spec.tags_to_delete_cell
        assert "notes" not in spec.tags_to_delete_cell
        assert "voiceover" not in spec.tags_to_delete_cell

    def test_trainer_tags_to_delete(self):
        """TrainerOutput should delete del, start, and voiceover (keeps notes)."""
        spec = TrainerOutput()
        assert "del" in spec.tags_to_delete_cell
        assert "start" in spec.tags_to_delete_cell
        assert "voiceover" in spec.tags_to_delete_cell
        assert "notes" not in spec.tags_to_delete_cell


class TestCompletedTagBehavior:
    """Test that the 'completed' tag behaves like 'alt': deleted in code-along,
    kept in completed and speaker output."""

    def test_code_along_excludes_completed_cells(self, make_cell):
        spec = CodeAlongOutput()
        cell = make_cell("code", ["completed"], "x = 42")
        assert spec.is_cell_included(cell) is False

    def test_completed_output_includes_completed_cells(self, make_cell):
        spec = CompletedOutput()
        cell = make_cell("code", ["completed"], "x = 42")
        assert spec.is_cell_included(cell) is True

    def test_recording_output_includes_completed_cells(self, make_cell):
        spec = RecordingOutput()
        cell = make_cell("code", ["completed"], "x = 42")
        assert spec.is_cell_included(cell) is True

    def test_trainer_output_includes_completed_cells(self, make_cell):
        spec = TrainerOutput()
        cell = make_cell("code", ["completed"], "x = 42")
        assert spec.is_cell_included(cell) is True

    def test_completed_tag_in_code_along_delete_set(self):
        spec = CodeAlongOutput()
        assert "completed" in spec.tags_to_delete_cell

    def test_completed_tag_not_in_completed_delete_set(self):
        spec = CompletedOutput()
        assert "completed" not in spec.tags_to_delete_cell

    def test_completed_tag_not_in_recording_delete_set(self):
        spec = RecordingOutput()
        assert "completed" not in spec.tags_to_delete_cell

    def test_completed_tag_not_in_trainer_delete_set(self):
        spec = TrainerOutput()
        assert "completed" not in spec.tags_to_delete_cell


class TestWorkshopTagBehavior:
    """Test that the 'workshop' tag is recognized but has no effect on output."""

    def test_workshop_tag_included_in_code_along(self, make_cell):
        spec = CodeAlongOutput()
        cell = make_cell("markdown", ["workshop"], "## Workshop: Lists")
        assert spec.is_cell_included(cell) is True

    def test_workshop_tag_included_in_completed(self, make_cell):
        spec = CompletedOutput()
        cell = make_cell("markdown", ["workshop"], "## Workshop: Lists")
        assert spec.is_cell_included(cell) is True

    def test_workshop_tag_included_in_recording(self, make_cell):
        spec = RecordingOutput()
        cell = make_cell("markdown", ["workshop"], "## Workshop: Lists")
        assert spec.is_cell_included(cell) is True

    def test_workshop_tag_included_in_trainer(self, make_cell):
        spec = TrainerOutput()
        cell = make_cell("markdown", ["workshop"], "## Workshop: Lists")
        assert spec.is_cell_included(cell) is True

    def test_workshop_tag_contents_kept_in_code_along(self, make_cell):
        spec = CodeAlongOutput()
        cell = make_cell("markdown", ["workshop"], "## Workshop: Lists")
        assert spec.is_cell_contents_included(cell) is True


class TestPartialOutput:
    """PartialOutput behaves as CompletedOutput up to the first ``workshop``
    markdown heading, then switches to CodeAlongOutput behaviour for the
    remainder of the notebook."""

    def test_subdir_fragment(self):
        assert PartialOutput().get_target_subdir_fragment() == "partial"

    def test_evaluate_for_html(self):
        """Partial never executes on its own — pre-workshop outputs come from
        Speaker's cached notebook, post-workshop cells are blanked with
        outputs cleared in post-processing."""
        assert PartialOutput().evaluate_for_html is False

    def test_reuses_speaker_execution_for_html(self):
        """Partial HTML reuses Speaker's cached executed notebook and
        post-processes it, so no workshop code is ever executed."""
        assert PartialOutput(format="html").can_reuse_execution is True

    def test_does_not_reuse_execution_for_non_html(self):
        """Notebook/code formats don't execute, so cache reuse does not apply."""
        assert PartialOutput(format="notebook").can_reuse_execution is False
        assert PartialOutput(format="code").can_reuse_execution is False

    def test_does_not_populate_cache(self):
        assert PartialOutput(format="html").should_cache_execution is False

    def test_annotate_cells_tags_post_workshop_suffix(self, make_cell):
        cells = [
            make_cell("markdown", ["slide"], "# Intro"),
            make_cell("code", [], "x = 1"),
            make_cell("markdown", ["subslide", "workshop"], "## Workshop: Lists"),
            make_cell("markdown", ["subslide"], "## Task 1"),
            make_cell("code", [], "answer = ..."),
        ]
        PartialOutput().annotate_cells(cells)
        assert POST_WORKSHOP_TAG not in cells[0]["metadata"]["tags"]
        assert POST_WORKSHOP_TAG not in cells[1]["metadata"]["tags"]
        assert POST_WORKSHOP_TAG in cells[2]["metadata"]["tags"]
        assert POST_WORKSHOP_TAG in cells[3]["metadata"]["tags"]
        assert POST_WORKSHOP_TAG in cells[4]["metadata"]["tags"]

    def test_annotate_cells_noop_without_workshop(self, make_cell):
        cells = [
            make_cell("markdown", ["slide"], "# Intro"),
            make_cell("code", [], "x = 1"),
        ]
        PartialOutput().annotate_cells(cells)
        assert POST_WORKSHOP_TAG not in cells[0]["metadata"]["tags"]
        assert POST_WORKSHOP_TAG not in cells[1]["metadata"]["tags"]

    def test_annotate_cells_is_idempotent(self, make_cell):
        cells = [make_cell("markdown", ["workshop"], "## Workshop")]
        spec = PartialOutput()
        spec.annotate_cells(cells)
        spec.annotate_cells(cells)
        assert cells[0]["metadata"]["tags"].count(POST_WORKSHOP_TAG) == 1

    def test_annotate_cells_respects_end_workshop(self, make_cell):
        """Cells after an ``end-workshop`` markdown cell return to
        non-workshop scope and must not carry the synthetic tag."""
        cells = [
            make_cell("markdown", ["slide"], "# Intro"),
            make_cell("markdown", ["subslide", "workshop"], "## Workshop"),
            make_cell("code", [], "answer = ..."),
            make_cell("markdown", ["subslide", "end-workshop"], "## Next topic"),
            make_cell("code", [], "more_demo = 1"),
        ]
        PartialOutput().annotate_cells(cells)
        assert POST_WORKSHOP_TAG not in cells[0]["metadata"]["tags"]
        assert POST_WORKSHOP_TAG in cells[1]["metadata"]["tags"]
        assert POST_WORKSHOP_TAG in cells[2]["metadata"]["tags"]
        # end-workshop heading itself is OUTSIDE the workshop.
        assert POST_WORKSHOP_TAG not in cells[3]["metadata"]["tags"]
        assert POST_WORKSHOP_TAG not in cells[4]["metadata"]["tags"]

    def test_annotate_cells_handles_multiple_workshops(self, make_cell):
        """Two separate workshops with explicit ends — only their interiors
        get the synthetic tag; the cells in between are untouched."""
        cells = [
            make_cell("markdown", ["subslide", "workshop"], "## Workshop 1"),
            make_cell("code", [], "ws1 = ..."),
            make_cell("markdown", ["subslide", "end-workshop"], "## Interlude"),
            make_cell("code", [], "demo = 1"),
            make_cell("markdown", ["subslide", "workshop"], "## Workshop 2"),
            make_cell("code", [], "ws2 = ..."),
        ]
        PartialOutput().annotate_cells(cells)
        # Workshop 1
        assert POST_WORKSHOP_TAG in cells[0]["metadata"]["tags"]
        assert POST_WORKSHOP_TAG in cells[1]["metadata"]["tags"]
        # Interlude (between workshops)
        assert POST_WORKSHOP_TAG not in cells[2]["metadata"]["tags"]
        assert POST_WORKSHOP_TAG not in cells[3]["metadata"]["tags"]
        # Workshop 2 (extends to EOF)
        assert POST_WORKSHOP_TAG in cells[4]["metadata"]["tags"]
        assert POST_WORKSHOP_TAG in cells[5]["metadata"]["tags"]

    def test_pre_workshop_code_retained(self, make_cell):
        """Pre-workshop code cells keep their contents (Completed behaviour)."""
        spec = PartialOutput()
        cell = make_cell("code", [], "x = 1")
        assert spec.is_cell_included(cell) is True
        assert spec.is_cell_contents_included(cell) is True

    def test_pre_workshop_keeps_completed_cells(self, make_cell):
        """The ``completed`` tag is kept pre-workshop (Completed behaviour)."""
        spec = PartialOutput()
        cell = make_cell("code", ["completed"], "x = 1")
        assert spec.is_cell_included(cell) is True

    def test_pre_workshop_drops_start_cells(self, make_cell):
        """Pre-workshop drops ``start`` cells, matching Completed."""
        spec = PartialOutput()
        cell = make_cell("code", ["start"], "# starter")
        assert spec.is_cell_included(cell) is False

    def test_pre_workshop_keeps_answer_markdown_contents(self, make_cell):
        """Pre-workshop keeps ``answer`` markdown content (Completed behaviour)."""
        spec = PartialOutput()
        cell = make_cell("markdown", ["answer"], "The answer is 42")
        assert spec.is_cell_included(cell) is True
        assert spec.is_cell_contents_included(cell) is True

    def test_post_workshop_code_cleared(self, make_cell):
        """Post-workshop code cells have their contents cleared (CodeAlong)."""
        spec = PartialOutput()
        cell = make_cell("code", [POST_WORKSHOP_TAG], "answer = ...")
        assert spec.is_cell_included(cell) is True
        assert spec.is_cell_contents_included(cell) is False

    def test_post_workshop_keeps_cells_with_keep_tag(self, make_cell):
        spec = PartialOutput()
        cell = make_cell("code", ["keep", POST_WORKSHOP_TAG], "setup = True")
        assert spec.is_cell_contents_included(cell) is True

    def test_post_workshop_keeps_cells_with_start_tag(self, make_cell):
        """Post-workshop keeps ``start`` cells (CodeAlong behaviour: scaffolding shown)."""
        spec = PartialOutput()
        cell = make_cell("code", ["start", POST_WORKSHOP_TAG], "# TODO")
        assert spec.is_cell_included(cell) is True
        assert spec.is_cell_contents_included(cell) is True

    def test_post_workshop_drops_completed_cells(self, make_cell):
        """Post-workshop drops ``completed`` cells (CodeAlong behaviour)."""
        spec = PartialOutput()
        cell = make_cell("code", ["completed", POST_WORKSHOP_TAG], "result = 42")
        assert spec.is_cell_included(cell) is False

    def test_post_workshop_drops_alt_cells(self, make_cell):
        spec = PartialOutput()
        cell = make_cell("markdown", ["alt", POST_WORKSHOP_TAG], "Alternative solution")
        assert spec.is_cell_included(cell) is False

    def test_post_workshop_clears_answer_markdown(self, make_cell):
        """Post-workshop clears ``answer`` markdown (CodeAlong behaviour)."""
        spec = PartialOutput()
        cell = make_cell("markdown", ["answer", POST_WORKSHOP_TAG], "The answer is 42")
        assert spec.is_cell_included(cell) is True
        assert spec.is_cell_contents_included(cell) is False

    def test_post_workshop_drops_notes(self, make_cell):
        spec = PartialOutput()
        cell = make_cell("markdown", ["notes", POST_WORKSHOP_TAG], "Speaker note")
        assert spec.is_cell_included(cell) is False

    def test_pre_workshop_drops_notes(self, make_cell):
        spec = PartialOutput()
        cell = make_cell("markdown", ["notes"], "Speaker note")
        assert spec.is_cell_included(cell) is False

    def test_workshop_heading_cell_itself_included(self, make_cell):
        """The workshop heading carries the synthetic tag after annotation
        and must still render as markdown with its content intact."""
        spec = PartialOutput()
        cells = [make_cell("markdown", ["subslide", "workshop"], "## Workshop: Lists")]
        spec.annotate_cells(cells)
        assert spec.is_cell_included(cells[0]) is True
        assert spec.is_cell_contents_included(cells[0]) is True


class TestCreateOutputSpecPartial:
    def test_creates_partial_output(self):
        spec = create_output_spec("partial")
        assert isinstance(spec, PartialOutput)

    def test_unknown_kind_mentions_partial(self):
        with pytest.raises(ValueError, match="partial"):
            create_output_spec("MySpecialSpec")


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
        """Each canonical output type should have a unique subdir fragment.

        ``SpeakerOutput`` is excluded because it is now an alias of
        ``RecordingOutput`` and intentionally shares its fragment.
        """
        fragments = {
            CompletedOutput().get_target_subdir_fragment(),
            CodeAlongOutput().get_target_subdir_fragment(),
            TrainerOutput().get_target_subdir_fragment(),
            RecordingOutput().get_target_subdir_fragment(),
            PartialOutput().get_target_subdir_fragment(),
        }
        assert len(fragments) == 5, "All canonical subdir fragments should be unique"
