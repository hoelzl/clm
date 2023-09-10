from config.cell_fixtures import *  # type: ignore
from clm.core.output_spec import (
    CodeAlongOutput,
    CompletedOutput,
    OutputSpec,
    SpeakerOutput,
)
from clm.utils.introspection import concrete_instance_of


class TestIsCellIncluded:
    @staticmethod
    def test_output_spec_en(
        code_cell,
        english_code_cell,
        english_markdown_cell,
        german_code_cell,
        german_markdown_cell,
    ):
        """Check whether language annotations are correctly processed.

        Cells without language annotation are always included.

        The default language is English, cells with other language annotations should be
        removed.
        """
        os = concrete_instance_of(
            OutputSpec, ["is_cell_included", "tags_to_delete_cell"]
        )

        assert os.is_cell_included(code_cell)
        assert os.is_cell_included(english_code_cell)
        assert os.is_cell_included(english_markdown_cell)
        assert not os.is_cell_included(german_code_cell)
        assert not os.is_cell_included(german_markdown_cell)

    @staticmethod
    def test_output_spec_de(
        code_cell,
        english_code_cell,
        english_markdown_cell,
        german_code_cell,
        german_markdown_cell,
    ):
        os = concrete_instance_of(
            OutputSpec,
            ["is_cell_included", "tags_to_delete_cell"],
            kwargs={"lang": "de"},
        )

        assert os.is_cell_included(code_cell)
        assert not os.is_cell_included(english_code_cell)
        assert not os.is_cell_included(english_markdown_cell)
        assert os.is_cell_included(german_code_cell)
        assert os.is_cell_included(german_markdown_cell)

    @staticmethod
    def test_code_along_code(code_cells):
        os = CodeAlongOutput()
        (
            code_cell,
            code_slide_cell,
            code_subslide_cell,
            deleted_cell,
            kept_cell,
            alternate_cell,
            starting_cell,
        ) = code_cells
        assert os.is_cell_included(code_cell)
        assert os.is_cell_included(code_slide_cell)
        assert os.is_cell_included(code_subslide_cell)
        assert not os.is_cell_included(deleted_cell)
        assert os.is_cell_included(kept_cell)
        assert not os.is_cell_included(alternate_cell)
        assert os.is_cell_included(starting_cell)

    @staticmethod
    def test_code_along_markdown(markdown_cells):
        os = CodeAlongOutput()
        (
            markdown_cell,
            markdown_slide_cell,
            markdown_subslide_cell,
            deleted_markdown_cell,
            markdown_notes_cell,
            answer_cell,
        ) = markdown_cells

        assert os.is_cell_included(markdown_cell)
        assert os.is_cell_included(markdown_slide_cell)
        assert os.is_cell_included(markdown_subslide_cell)
        assert not os.is_cell_included(deleted_markdown_cell)
        assert not os.is_cell_included(markdown_notes_cell)
        assert os.is_cell_included(answer_cell)

    @staticmethod
    def test_completed_code(code_cells):
        os = CompletedOutput()
        (
            code_cell,
            code_slide_cell,
            code_subslide_cell,
            deleted_cell,
            kept_cell,
            alternate_cell,
            starting_cell,
        ) = code_cells
        assert os.is_cell_included(code_cell)
        assert os.is_cell_included(code_slide_cell)
        assert os.is_cell_included(code_subslide_cell)
        assert not os.is_cell_included(deleted_cell)
        assert os.is_cell_included(kept_cell)
        assert os.is_cell_included(alternate_cell)
        assert not os.is_cell_included(starting_cell)

    @staticmethod
    def test_completed_markdown(markdown_cells):
        os = CompletedOutput()
        (
            markdown_cell,
            markdown_slide_cell,
            markdown_subslide_cell,
            deleted_markdown_cell,
            markdown_notes_cell,
            answer_cell,
        ) = markdown_cells

        assert os.is_cell_included(markdown_cell)
        assert os.is_cell_included(markdown_slide_cell)
        assert os.is_cell_included(markdown_subslide_cell)
        assert not os.is_cell_included(deleted_markdown_cell)
        assert not os.is_cell_included(markdown_notes_cell)
        assert os.is_cell_included(answer_cell)

    @staticmethod
    def test_speaker_code(code_cells):
        os = SpeakerOutput()
        (
            code_cell,
            code_slide_cell,
            code_subslide_cell,
            deleted_cell,
            kept_cell,
            alternate_cell,
            starting_cell,
        ) = code_cells
        assert os.is_cell_included(code_cell)
        assert os.is_cell_included(code_slide_cell)
        assert os.is_cell_included(code_subslide_cell)
        assert not os.is_cell_included(deleted_cell)
        assert os.is_cell_included(kept_cell)
        assert os.is_cell_included(alternate_cell)
        assert not os.is_cell_included(starting_cell)

    @staticmethod
    def test_speaker_markdown(markdown_cells):
        os = SpeakerOutput()
        (
            markdown_cell,
            markdown_slide_cell,
            markdown_subslide_cell,
            deleted_markdown_cell,
            markdown_notes_cell,
            answer_cell,
        ) = markdown_cells

        assert os.is_cell_included(markdown_cell)
        assert os.is_cell_included(markdown_slide_cell)
        assert os.is_cell_included(markdown_subslide_cell)
        assert not os.is_cell_included(deleted_markdown_cell)
        assert os.is_cell_included(markdown_notes_cell)
        assert os.is_cell_included(answer_cell)


class TestIsCellContentsIncluded:
    @staticmethod
    def test_completed(code_cell, markdown_cell):
        os = CompletedOutput()
        assert not os.delete_any_cell_contents
        assert os.is_cell_contents_included(code_cell)
        assert os.is_cell_contents_included(markdown_cell)

    @staticmethod
    def test_speaker(code_cell, markdown_cell):
        os = SpeakerOutput()
        assert not os.delete_any_cell_contents
        assert os.is_cell_contents_included(code_cell)
        assert os.is_cell_contents_included(markdown_cell)

    @staticmethod
    def test_code_along_code(code_cells):
        os = CodeAlongOutput()
        (
            code_cell,
            code_slide_cell,
            code_subslide_cell,
            deleted_cell,
            kept_cell,
            alternate_cell,
            starting_cell,
        ) = code_cells
        assert not os.is_cell_contents_included(code_cell)
        assert not os.is_cell_contents_included(code_slide_cell)
        assert not os.is_cell_contents_included(code_subslide_cell)
        assert os.is_cell_contents_included(kept_cell)
        assert os.is_cell_contents_included(starting_cell)

    @staticmethod
    def test_code_along_markdown(markdown_cells):
        os = CodeAlongOutput()
        (
            markdown_cell,
            markdown_slide_cell,
            markdown_subslide_cell,
            _deleted_markdown_cell,
            _markdown_notes_cell,
            answer_cell,
        ) = markdown_cells
        assert os.is_cell_contents_included(markdown_cell)
        assert os.is_cell_contents_included(markdown_slide_cell)
        assert os.is_cell_contents_included(markdown_subslide_cell)
        assert not os.is_cell_contents_included(answer_cell)
