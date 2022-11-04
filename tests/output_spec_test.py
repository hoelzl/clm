from clm.core.output_spec import (
    OutputSpec,
    CodeAlongOutput,
    CompletedOutput,
    SpeakerOutput,
)
from conftest import concrete_instance_of, all_cells, code_cells, markdown_cells  # noqa


class TestIsCellIncluded:
    @staticmethod
    def test_output_spec_en(all_cells):
        """Check whether language annotations are correctly processed.

        Cells without language annotation are always included.

        The default language is English, cells with other language annotations should be
        removed.
        """
        os = concrete_instance_of(
            OutputSpec, ["is_cell_included", "tags_to_delete_cell"]
        )

        assert os.is_cell_included(all_cells["code"])
        assert os.is_cell_included(all_cells["en"])
        assert os.is_cell_included(all_cells["en-md"])
        assert not os.is_cell_included(all_cells["de"])
        assert not os.is_cell_included(all_cells["de-md"])

    @staticmethod
    def test_output_spec_de(all_cells):
        os = concrete_instance_of(
            OutputSpec,
            ["is_cell_included", "tags_to_delete_cell"],
            kwargs={"lang": "de"},
        )

        assert os.is_cell_included(all_cells["code"])
        assert not os.is_cell_included(all_cells["en"])
        assert not os.is_cell_included(all_cells["en-md"])
        assert os.is_cell_included(all_cells["de"])
        assert os.is_cell_included(all_cells["de-md"])

    @staticmethod
    def test_code_along_code(code_cells):
        """Check whether non-language tags are correctly processed."""
        os = CodeAlongOutput()

        assert os.is_cell_included(code_cells["code"])
        assert os.is_cell_included(code_cells["slide"])
        assert os.is_cell_included(code_cells["subslide"])
        assert not os.is_cell_included(code_cells["del"])
        assert os.is_cell_included(code_cells["keep"])
        assert not os.is_cell_included(code_cells["alt"])
        assert os.is_cell_included(code_cells["start"])

    @staticmethod
    def test_code_along_markdown(markdown_cells):
        """Check whether non-language tags are correctly processed."""
        os = CodeAlongOutput()

        assert os.is_cell_included(markdown_cells["md"])
        assert os.is_cell_included(markdown_cells["slide"])
        assert os.is_cell_included(markdown_cells["subslide"])
        assert not os.is_cell_included(markdown_cells["del"])
        assert not os.is_cell_included(markdown_cells["notes"])
        assert os.is_cell_included(markdown_cells["answer"])

    @staticmethod
    def test_completed_code(code_cells):
        """Check whether non-language tags are correctly processed."""
        os = CompletedOutput()

        assert os.is_cell_included(code_cells["code"])
        assert os.is_cell_included(code_cells["slide"])
        assert os.is_cell_included(code_cells["subslide"])
        assert not os.is_cell_included(code_cells["del"])
        assert os.is_cell_included(code_cells["keep"])
        assert os.is_cell_included(code_cells["alt"])
        assert not os.is_cell_included(code_cells["start"])

    @staticmethod
    def test_completed_markdown(markdown_cells):
        """Check whether non-language tags are correctly processed."""
        os = CompletedOutput()

        assert os.is_cell_included(markdown_cells["md"])
        assert os.is_cell_included(markdown_cells["slide"])
        assert os.is_cell_included(markdown_cells["subslide"])
        assert not os.is_cell_included(markdown_cells["del"])
        assert not os.is_cell_included(markdown_cells["notes"])
        assert os.is_cell_included(markdown_cells["answer"])

    @staticmethod
    def test_speaker_code(code_cells):
        """Check whether non-language tags are correctly processed."""
        os = SpeakerOutput()

        assert os.is_cell_included(code_cells["code"])
        assert os.is_cell_included(code_cells["slide"])
        assert os.is_cell_included(code_cells["subslide"])
        assert not os.is_cell_included(code_cells["del"])
        assert os.is_cell_included(code_cells["keep"])
        assert os.is_cell_included(code_cells["alt"])
        assert not os.is_cell_included(code_cells["start"])

    @staticmethod
    def test_speaker_markdown(markdown_cells):
        """Check whether non-language tags are correctly processed."""
        os = SpeakerOutput()

        assert os.is_cell_included(markdown_cells["md"])
        assert os.is_cell_included(markdown_cells["slide"])
        assert os.is_cell_included(markdown_cells["subslide"])
        assert not os.is_cell_included(markdown_cells["del"])
        assert os.is_cell_included(markdown_cells["notes"])
        assert os.is_cell_included(markdown_cells["answer"])


class TestIsCellContentsIncluded:
    @staticmethod
    def test_completed(all_cells):
        os = CompletedOutput()
        assert not os.delete_any_cell_contents
        assert os.is_cell_included(all_cells["code"])
        assert os.is_cell_included(all_cells["md"])

    @staticmethod
    def test_speaker(all_cells):
        os = SpeakerOutput()
        assert not os.delete_any_cell_contents
        assert os.is_cell_included(all_cells["code"])
        assert os.is_cell_included(all_cells["md"])

    @staticmethod
    def test_code_along_code(code_cells):
        os = CodeAlongOutput()

        assert not os.is_cell_contents_included(code_cells["code"])
        assert not os.is_cell_contents_included(code_cells["slide"])
        assert not os.is_cell_contents_included(code_cells["subslide"])
        assert os.is_cell_contents_included(code_cells["keep"])
        assert os.is_cell_contents_included(code_cells["start"])

    @staticmethod
    def test_code_along_markdown(markdown_cells):
        os = CodeAlongOutput()

        assert os.is_cell_contents_included(markdown_cells["md"])
        assert os.is_cell_contents_included(markdown_cells["slide"])
        assert os.is_cell_contents_included(markdown_cells["subslide"])
        assert not os.is_cell_contents_included(markdown_cells["answer"])
