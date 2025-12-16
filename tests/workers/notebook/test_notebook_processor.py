"""Behavior-focused tests for notebook_processor module.

These tests verify the observable behavior of notebook processing:
- Which cells appear in the output based on tags and output spec
- How cell content is transformed (notes styling, answer prefixes, code clearing)
- What output format is produced (HTML, code, notebook)
- Caching behavior for executed notebooks

Tests are designed to enable refactoring by focusing on inputs/outputs
rather than internal implementation details.
"""

import json
import uuid
from base64 import b64encode
from unittest.mock import MagicMock, patch

import pytest
from nbformat import NotebookNode

from clx.infrastructure.messaging.notebook_classes import NotebookPayload
from clx.workers.notebook.notebook_processor import CellIdGenerator, NotebookProcessor
from clx.workers.notebook.output_spec import (
    CodeAlongOutput,
    CompletedOutput,
    SpeakerOutput,
    create_output_spec,
)

# ============================================================================
# Test Fixtures - Notebook Creation Helpers
# ============================================================================


def make_cell(
    cell_type: str, source: str, tags: list[str] | None = None, lang: str | None = None
) -> NotebookNode:
    """Create a notebook cell as NotebookNode.

    Args:
        cell_type: "code" or "markdown"
        source: Cell content
        tags: Optional list of cell tags
        lang: Optional language code (e.g., "en", "de") for language-specific cells

    Returns:
        NotebookNode cell
    """
    metadata: dict = {"tags": tags or []}
    if lang:
        metadata["lang"] = lang
    cell = NotebookNode(
        {
            "id": uuid.uuid4().hex[:16],  # Generate unique cell ID (required by nbformat 5+)
            "cell_type": cell_type,
            "source": source,
            "metadata": metadata,
        }
    )
    if cell_type == "code":
        cell["outputs"] = []
        cell["execution_count"] = None
    return cell


def make_notebook_node(cells: list[NotebookNode]) -> NotebookNode:
    """Create a NotebookNode from cells.

    Args:
        cells: List of NotebookNode cells

    Returns:
        NotebookNode notebook
    """
    return NotebookNode(
        {
            "cells": cells,
            "metadata": {
                "kernelspec": {"name": "python3", "display_name": "Python 3"},
                "language_info": {"name": "python"},
            },
            "nbformat": 4,
            "nbformat_minor": 5,
        }
    )


def make_notebook_json(cells: list[dict]) -> str:
    """Create a notebook JSON string from cell dicts.

    Args:
        cells: List of cell dictionaries

    Returns:
        JSON string of the notebook
    """
    notebook = {
        "cells": cells,
        "metadata": {
            "kernelspec": {"name": "python3", "display_name": "Python 3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    return json.dumps(notebook)


def make_payload(
    notebook_data: str,
    format_: str = "notebook",
    kind: str = "completed",
    language: str = "en",
    prog_lang: str = "python",
    other_files: dict | None = None,
) -> NotebookPayload:
    """Create a NotebookPayload for testing.

    Args:
        notebook_data: JSON string of notebook content
        format_: Output format (notebook, html, code)
        kind: Output kind (completed, code-along, speaker)
        language: Human language (en, de)
        prog_lang: Programming language
        other_files: Dict of filename -> base64-encoded content

    Returns:
        NotebookPayload instance
    """
    return NotebookPayload(
        input_file="/test/notebook.ipynb",
        input_file_name="notebook.ipynb",
        output_file="/test/output/notebook",
        data=notebook_data,
        format=format_,
        kind=kind,
        language=language,
        prog_lang=prog_lang,
        correlation_id="test-correlation-id",
        other_files=other_files or {},
    )


# ============================================================================
# Cell Filtering Tests - Which cells appear in output based on tags
# ============================================================================


class TestCellFilteringByTag:
    """Test that cells are correctly filtered based on their tags.

    These tests verify the observable behavior of cell filtering by testing
    the _process_notebook_node method with NotebookNode inputs.
    """

    @pytest.mark.asyncio
    async def test_del_tagged_cells_removed_from_completed(self):
        """Cells tagged with 'del' should be removed from completed output."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Title"),
                make_cell("code", "# This should be deleted", tags=["del"]),
                make_cell("code", "print('hello')"),
            ]
        )

        spec = CompletedOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="completed", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        # Check the resulting cells
        cell_sources = [cell["source"] for cell in result["cells"]]
        assert "# This should be deleted" not in cell_sources
        assert "# Title" in cell_sources
        assert "print('hello')" in cell_sources

    @pytest.mark.asyncio
    async def test_del_tagged_cells_removed_from_speaker(self):
        """Cells tagged with 'del' should be removed from speaker output."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Title"),
                make_cell("code", "# This should be deleted", tags=["del"]),
                make_cell("code", "print('hello')"),
            ]
        )

        spec = SpeakerOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="speaker", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        cell_sources = [cell["source"] for cell in result["cells"]]
        assert "# This should be deleted" not in cell_sources

    @pytest.mark.asyncio
    async def test_del_tagged_cells_removed_from_codealong(self):
        """Cells tagged with 'del' should be removed from code-along output."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Title"),
                make_cell("code", "# This should be deleted", tags=["del"]),
                make_cell("code", "print('hello')"),
            ]
        )

        spec = CodeAlongOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="code-along", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        cell_sources = [cell["source"] for cell in result["cells"]]
        assert "# This should be deleted" not in cell_sources

    @pytest.mark.asyncio
    async def test_notes_cells_included_in_speaker_output(self):
        """Notes cells should appear in speaker output with yellow styling."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Title"),
                make_cell("markdown", "Speaker notes here", tags=["notes"]),
                make_cell("code", "print('hello')"),
            ]
        )

        spec = SpeakerOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="speaker", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        # Find the notes cell
        notes_cells = [
            cell for cell in result["cells"] if "notes" in cell.get("metadata", {}).get("tags", [])
        ]
        assert len(notes_cells) == 1
        # Should have yellow background styling
        assert "background: yellow" in notes_cells[0]["source"]
        assert "Speaker notes here" in notes_cells[0]["source"]

    @pytest.mark.asyncio
    async def test_notes_cells_excluded_from_completed_output(self):
        """Notes cells should be removed from completed output."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Title"),
                make_cell("markdown", "Speaker notes here", tags=["notes"]),
                make_cell("code", "print('hello')"),
            ]
        )

        spec = CompletedOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="completed", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        cell_sources = [cell["source"] for cell in result["cells"]]
        # Notes should be excluded - check none of the sources contain it
        assert not any("Speaker notes here" in src for src in cell_sources)

    @pytest.mark.asyncio
    async def test_notes_cells_excluded_from_codealong_output(self):
        """Notes cells should be removed from code-along output."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Title"),
                make_cell("markdown", "Speaker notes here", tags=["notes"]),
                make_cell("code", "print('hello')"),
            ]
        )

        spec = CodeAlongOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="code-along", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        cell_sources = [cell["source"] for cell in result["cells"]]
        assert not any("Speaker notes here" in src for src in cell_sources)

    @pytest.mark.asyncio
    async def test_start_cells_excluded_from_completed(self):
        """Cells tagged with 'start' should be removed from completed output."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Title"),
                make_cell("code", "# Starting code template", tags=["start"]),
                make_cell("code", "print('hello')"),
            ]
        )

        spec = CompletedOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="completed", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        cell_sources = [cell["source"] for cell in result["cells"]]
        assert "# Starting code template" not in cell_sources

    @pytest.mark.asyncio
    async def test_start_cells_excluded_from_speaker(self):
        """Cells tagged with 'start' should be removed from speaker output."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Title"),
                make_cell("code", "# Starting code template", tags=["start"]),
                make_cell("code", "print('hello')"),
            ]
        )

        spec = SpeakerOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="speaker", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        cell_sources = [cell["source"] for cell in result["cells"]]
        assert "# Starting code template" not in cell_sources

    @pytest.mark.asyncio
    async def test_alt_cells_excluded_from_codealong(self):
        """Cells tagged with 'alt' should be removed from code-along output."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Title"),
                make_cell("code", "# Alternative solution", tags=["alt"]),
                make_cell("code", "print('hello')"),
            ]
        )

        spec = CodeAlongOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="code-along", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        cell_sources = [cell["source"] for cell in result["cells"]]
        assert "# Alternative solution" not in cell_sources

    @pytest.mark.asyncio
    async def test_alt_cells_included_in_completed(self):
        """Cells tagged with 'alt' should be included in completed output."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Title"),
                make_cell("code", "# Alternative solution", tags=["alt"]),
            ]
        )

        spec = CompletedOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="completed", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        cell_sources = [cell["source"] for cell in result["cells"]]
        assert "# Alternative solution" in cell_sources


class TestCellFilteringByLanguage:
    """Test that cells are filtered based on language metadata."""

    @pytest.mark.asyncio
    async def test_english_only_cells_excluded_from_german_output(self):
        """Cells with lang='en' metadata should be excluded from German output."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Title"),
                make_cell("markdown", "English only content", lang="en"),
                make_cell("markdown", "Universal content"),
            ]
        )

        spec = CompletedOutput(language="de", format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", language="de", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        cell_sources = [cell["source"] for cell in result["cells"]]
        assert "English only content" not in cell_sources
        assert "Universal content" in cell_sources

    @pytest.mark.asyncio
    async def test_german_only_cells_excluded_from_english_output(self):
        """Cells with lang='de' metadata should be excluded from English output."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Title"),
                make_cell("markdown", "German only content", lang="de"),
                make_cell("markdown", "Universal content"),
            ]
        )

        spec = CompletedOutput(language="en", format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", language="en", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        cell_sources = [cell["source"] for cell in result["cells"]]
        assert "German only content" not in cell_sources
        assert "Universal content" in cell_sources

    @pytest.mark.asyncio
    async def test_english_cells_included_in_english_output(self):
        """Cells with lang='en' metadata should be included in English output."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Title"),
                make_cell("markdown", "English only content", lang="en"),
            ]
        )

        spec = CompletedOutput(language="en", format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", language="en", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        cell_sources = [cell["source"] for cell in result["cells"]]
        assert "English only content" in cell_sources


# ============================================================================
# Cell Content Transformation Tests
# ============================================================================


class TestCodeCellContentClearing:
    """Test code cell content clearing in code-along output."""

    @pytest.mark.asyncio
    async def test_codealong_clears_regular_code_cells(self):
        """Code-along should clear contents of regular code cells."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Title"),
                make_cell("code", "print('this should be cleared')"),
            ]
        )

        spec = CodeAlongOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="code-along", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        # Find the code cell and verify it's cleared
        code_cells = [c for c in result["cells"] if c["cell_type"] == "code"]
        assert len(code_cells) == 1
        assert code_cells[0]["source"] == ""

    @pytest.mark.asyncio
    async def test_codealong_preserves_keep_tagged_code(self):
        """Code-along should preserve code in cells tagged 'keep'."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Title"),
                make_cell("code", "# This code should be kept", tags=["keep"]),
                make_cell("code", "# This should be cleared"),
            ]
        )

        spec = CodeAlongOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="code-along", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        code_cells = [c for c in result["cells"] if c["cell_type"] == "code"]
        # Keep cell should have content
        keep_cell = [c for c in code_cells if "keep" in c.get("metadata", {}).get("tags", [])]
        assert len(keep_cell) == 1
        assert "This code should be kept" in keep_cell[0]["source"]

        # Regular cell should be cleared
        regular_cells = [
            c for c in code_cells if "keep" not in c.get("metadata", {}).get("tags", [])
        ]
        assert len(regular_cells) == 1
        assert regular_cells[0]["source"] == ""

    @pytest.mark.asyncio
    async def test_codealong_preserves_start_tagged_code(self):
        """Code-along should preserve code in cells tagged 'start'."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Title"),
                make_cell("code", "# Starting template code", tags=["start"]),
            ]
        )

        spec = CodeAlongOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="code-along", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        code_cells = [c for c in result["cells"] if c["cell_type"] == "code"]
        assert len(code_cells) == 1
        assert "Starting template code" in code_cells[0]["source"]

    @pytest.mark.asyncio
    async def test_completed_preserves_all_code_content(self):
        """Completed output should preserve all code cell contents."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Title"),
                make_cell("code", "print('all code preserved')"),
            ]
        )

        spec = CompletedOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="completed", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        code_cells = [c for c in result["cells"] if c["cell_type"] == "code"]
        assert len(code_cells) == 1
        assert "all code preserved" in code_cells[0]["source"]


class TestAnswerCellFormatting:
    """Test answer cell prefix formatting."""

    @pytest.mark.asyncio
    async def test_answer_cell_gets_english_prefix(self):
        """Answer cells in English output get 'Answer:' prefix."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Title"),
                make_cell("markdown", "The solution is 42", tags=["answer"]),
            ]
        )

        spec = CompletedOutput(language="en", format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", language="en", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        answer_cells = [
            c for c in result["cells"] if "answer" in c.get("metadata", {}).get("tags", [])
        ]
        assert len(answer_cells) == 1
        assert "*Answer:*" in answer_cells[0]["source"]
        assert "The solution is 42" in answer_cells[0]["source"]

    @pytest.mark.asyncio
    async def test_answer_cell_gets_german_prefix(self):
        """Answer cells in German output get 'Antwort:' prefix."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Title"),
                make_cell("markdown", "Die Lösung ist 42", tags=["answer"]),
            ]
        )

        spec = CompletedOutput(language="de", format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", language="de", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        answer_cells = [
            c for c in result["cells"] if "answer" in c.get("metadata", {}).get("tags", [])
        ]
        assert len(answer_cells) == 1
        assert "*Antwort:*" in answer_cells[0]["source"]
        assert "Die Lösung ist 42" in answer_cells[0]["source"]

    @pytest.mark.asyncio
    async def test_codealong_clears_answer_cell_content(self):
        """Code-along should show only prefix for answer cells, not content."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Title"),
                make_cell("markdown", "The detailed answer", tags=["answer"]),
            ]
        )

        spec = CodeAlongOutput(language="en", format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="code-along", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        answer_cells = [
            c for c in result["cells"] if "answer" in c.get("metadata", {}).get("tags", [])
        ]
        assert len(answer_cells) == 1
        # Should have the prefix but not the content
        assert "*Answer:*" in answer_cells[0]["source"]
        assert "The detailed answer" not in answer_cells[0]["source"]


class TestNotesCellStyling:
    """Test notes cell styling in speaker output."""

    @pytest.mark.asyncio
    async def test_notes_cell_wrapped_in_yellow_div(self):
        """Notes cells should be wrapped in yellow background div."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Title"),
                make_cell("markdown", "Important speaker note", tags=["notes"]),
            ]
        )

        spec = SpeakerOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="speaker", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        notes_cells = [
            c for c in result["cells"] if "notes" in c.get("metadata", {}).get("tags", [])
        ]
        assert len(notes_cells) == 1
        # Check for yellow background styling
        assert "background: yellow" in notes_cells[0]["source"]
        assert "Important speaker note" in notes_cells[0]["source"]
        assert "</div>" in notes_cells[0]["source"]


# ============================================================================
# Output Format Tests
# ============================================================================


class TestOutputFormatNotebook:
    """Test notebook (ipynb) format output via create_contents."""

    @pytest.mark.asyncio
    async def test_notebook_format_via_jupytext(self):
        """Notebook format should produce ipynb output via jupytext."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Test"),
                make_cell("code", "x = 1"),
            ]
        )

        spec = CompletedOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", format_="notebook")

        result = await processor.create_contents(notebook, payload)

        # Should be valid JSON containing notebook structure
        parsed = json.loads(result)
        assert "cells" in parsed
        assert "metadata" in parsed


class TestOutputFormatCode:
    """Test code format output (Python script)."""

    @pytest.mark.asyncio
    async def test_code_format_produces_python_script(self):
        """Code format should produce a Python script via jupytext."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Test Notebook"),
                make_cell("code", "x = 1\nprint(x)"),
            ]
        )

        spec = CompletedOutput(format="code", prog_lang="python")
        processor = NotebookProcessor(spec)
        payload = make_payload("", format_="code", prog_lang="python")

        result = await processor.create_contents(notebook, payload)

        # Should contain the code
        assert "x = 1" in result
        assert "print(x)" in result
        # Should end with newline
        assert result.endswith("\n")


class TestOutputFormatHtml:
    """Test HTML format output."""

    @pytest.mark.asyncio
    async def test_html_format_uses_html_exporter(self):
        """HTML format should use HTMLExporter."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Test"),
            ]
        )

        # For testing HTML without execution, we mock both the exporter and ExecutePreprocessor
        spec = SpeakerOutput(format="html")
        processor = NotebookProcessor(spec)
        payload = make_payload("", format_="html", kind="speaker")

        with (
            patch("clx.workers.notebook.notebook_processor.HTMLExporter") as MockExporter,
            patch("clx.workers.notebook.notebook_processor.TrackingExecutePreprocessor") as MockEP,
        ):
            mock_exporter = MagicMock()
            mock_exporter.from_notebook_node.return_value = (
                "<html><body>Test</body></html>",
                {},
            )
            MockExporter.return_value = mock_exporter
            # Mock ExecutePreprocessor to avoid actual notebook execution
            MockEP.return_value = MagicMock()

            result = await processor.create_contents(notebook, payload)

        assert "<html>" in result
        MockExporter.assert_called_once()


# ============================================================================
# Jinja Template Expansion Tests
# ============================================================================


class TestJinjaExpansion:
    """Test Jinja2 template expansion in notebooks."""

    @pytest.mark.asyncio
    async def test_jinja_globals_available_in_template(self):
        """Jinja globals (is_notebook, is_html, lang) should be available."""
        # Create a notebook that uses Jinja variables
        notebook_content = """# j2 if is_notebook
Notebook mode content
# j2 endif
# j2 if lang == "en"
English content
# j2 endif"""

        # This tests that the Jinja environment is correctly set up
        spec = CompletedOutput(format="notebook", language="en")
        processor = NotebookProcessor(spec)

        # Test the globals creation
        globals_ = processor._create_jinja_globals(spec)
        assert globals_["is_notebook"] is True
        assert globals_["is_html"] is False
        assert globals_["lang"] == "en"

    @pytest.mark.asyncio
    async def test_html_format_sets_is_html_true(self):
        """HTML format should set is_html=True in Jinja globals."""
        spec = CompletedOutput(format="html", language="de")

        globals_ = NotebookProcessor._create_jinja_globals(spec)
        assert globals_["is_html"] is True
        assert globals_["is_notebook"] is False
        assert globals_["lang"] == "de"


# ============================================================================
# Cell ID Generation Tests
# ============================================================================


class TestCellIdGeneration:
    """Test unique cell ID generation."""

    def test_cell_id_generator_creates_unique_ids(self):
        """CellIdGenerator should create unique IDs for different content."""
        generator = CellIdGenerator()

        cell1 = make_cell("code", "x = 1")
        cell2 = make_cell("code", "y = 2")
        cell3 = make_cell("markdown", "# Title")

        generator.set_cell_id(cell1, 0)
        generator.set_cell_id(cell2, 1)
        generator.set_cell_id(cell3, 2)

        ids = [cell1.id, cell2.id, cell3.id]

        # All IDs should be unique
        assert len(ids) == len(set(ids))
        # All IDs should be 16 characters (hex from sha3_224)
        assert all(len(id_) == 16 for id_ in ids)

    def test_duplicate_content_cells_get_different_ids(self):
        """Cells with identical content should still get different IDs."""
        generator = CellIdGenerator()

        cells = [
            make_cell("code", "x = 1"),
            make_cell("code", "x = 1"),  # Same content
            make_cell("code", "x = 1"),  # Same content again
        ]

        for i, cell in enumerate(cells):
            generator.set_cell_id(cell, i)

        ids = [cell.id for cell in cells]

        # All IDs should still be unique despite identical content
        assert len(ids) == len(set(ids))

    @pytest.mark.asyncio
    async def test_process_notebook_node_assigns_cell_ids(self):
        """Processing a notebook should assign IDs to all cells."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Title"),
                make_cell("code", "x = 1"),
                make_cell("code", "y = 2"),
            ]
        )

        spec = CompletedOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        # All cells should have IDs
        for cell in result["cells"]:
            assert cell.id is not None
            assert len(cell.id) == 16


# ============================================================================
# Slide Tag Processing Tests
# ============================================================================


class TestSlideTagProcessing:
    """Test slide tag processing for presentations."""

    @pytest.mark.asyncio
    async def test_slide_tag_creates_slideshow_metadata(self):
        """Cells with slide tags should get slideshow metadata."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# New Slide", tags=["slide"]),
                make_cell("markdown", "Content"),
            ]
        )

        spec = CompletedOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        first_cell = result["cells"][0]

        # Should have slideshow metadata
        assert "slideshow" in first_cell.get("metadata", {})
        assert first_cell["metadata"]["slideshow"]["slide_type"] == "slide"

    @pytest.mark.asyncio
    async def test_subslide_tag_creates_subslide_metadata(self):
        """Cells with subslide tags should get subslide slideshow metadata."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Sub Slide", tags=["subslide"]),
            ]
        )

        spec = CompletedOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        first_cell = result["cells"][0]
        assert first_cell["metadata"]["slideshow"]["slide_type"] == "subslide"


# ============================================================================
# Caching Behavior Tests
# ============================================================================


class TestExecutionCaching:
    """Test notebook execution caching behavior.

    These tests verify the caching contract between Speaker and Completed outputs:
    - Speaker HTML caches executed notebooks for reuse
    - Completed HTML reuses cached notebooks when available
    """

    def test_speaker_html_should_cache_property(self):
        """Speaker HTML spec should indicate caching is enabled."""
        spec = SpeakerOutput(format="html")
        assert spec.should_cache_execution is True

    def test_speaker_notebook_should_not_cache_property(self):
        """Speaker notebook spec should not cache."""
        spec = SpeakerOutput(format="notebook")
        assert spec.should_cache_execution is False

    def test_completed_html_can_reuse_property(self):
        """Completed HTML spec should indicate it can reuse cache."""
        spec = CompletedOutput(format="html")
        assert spec.can_reuse_execution is True

    def test_completed_notebook_cannot_reuse_property(self):
        """Completed notebook spec should not reuse cache."""
        spec = CompletedOutput(format="notebook")
        assert spec.can_reuse_execution is False

    def test_codealong_never_caches_or_reuses(self):
        """CodeAlong outputs should never cache or reuse."""
        for format_ in ["notebook", "code", "html"]:
            spec = CodeAlongOutput(format=format_)
            assert spec.should_cache_execution is False
            assert spec.can_reuse_execution is False


# ============================================================================
# Other Files Handling Tests
# ============================================================================


class TestOtherFilesHandling:
    """Test handling of additional files (other_files payload attribute)."""

    def test_payload_can_contain_other_files(self):
        """NotebookPayload should support other_files for auxiliary data."""
        # Encode some test data
        test_data = b"test content"
        encoded_data = b64encode(test_data).decode("utf-8")

        payload = make_payload(
            "",
            kind="speaker",
            format_="html",
            other_files={"data.txt": encoded_data},
        )

        assert "data.txt" in payload.other_files
        # Note: other_files values are stored as bytes (base64 encoded)
        assert payload.other_files["data.txt"] == encoded_data.encode("utf-8")


# ============================================================================
# Error Handling Tests
# ============================================================================


class TestErrorHandling:
    """Test error handling in notebook processing."""

    @pytest.mark.asyncio
    async def test_invalid_cell_type_handled_gracefully(self):
        """Unknown cell types should not crash processing."""
        # Create a notebook with an unusual cell type
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Test"),
            ]
        )
        # Add a weird cell type
        notebook["cells"].append(
            NotebookNode(
                {
                    "cell_type": "raw",
                    "source": "raw content",
                    "metadata": {"tags": []},
                }
            )
        )

        spec = CompletedOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", format_="notebook")

        # Should not raise - raw cells are passed through
        result = await processor._process_notebook_node(notebook, payload)
        assert len(result["cells"]) == 2


# ============================================================================
# Integration-style Tests (Still Unit Tests but More Realistic)
# ============================================================================


class TestRealisticScenarios:
    """Test realistic notebook processing scenarios."""

    @pytest.mark.asyncio
    async def test_workshop_notebook_produces_correct_completed_output(self):
        """A typical workshop notebook should produce correct completed output."""
        # A realistic workshop notebook with various cell types
        workshop_notebook = make_notebook_node(
            [
                make_cell("markdown", "# Workshop: Introduction to Python"),
                make_cell("markdown", "Remember to pace yourself", tags=["notes"]),
                make_cell("code", "# Import libraries\nimport math", tags=["keep"]),
                make_cell("markdown", "## Exercise 1"),
                make_cell("code", "# Calculate area\nradius = 5\narea = math.pi * radius**2"),
                make_cell("markdown", "The area is calculated using πr²", tags=["answer"]),
                make_cell("code", "# Alternative using lambda", tags=["alt"]),
                make_cell("code", "# Template for students", tags=["start"]),
                make_cell("code", "# This cell should be removed", tags=["del"]),
            ]
        )

        # Test completed output
        spec = CompletedOutput(format="notebook", language="en")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="completed", format_="notebook", language="en")

        result = await processor._process_notebook_node(workshop_notebook, payload)

        cell_sources = [cell["source"] for cell in result["cells"]]
        all_sources = " ".join(cell_sources)

        # Verify completed output characteristics
        assert "Workshop: Introduction to Python" in all_sources
        assert "Remember to pace yourself" not in all_sources  # notes excluded
        assert "Import libraries" in all_sources  # keep cell included
        assert "Calculate area" in all_sources  # regular code included
        assert "*Answer:*" in all_sources  # answer prefix added
        assert "Alternative using lambda" in all_sources  # alt cell included in completed
        assert "Template for students" not in all_sources  # start excluded
        assert "This cell should be removed" not in all_sources  # del excluded

    @pytest.mark.asyncio
    async def test_workshop_notebook_produces_correct_codealong_output(self):
        """Code-along should clear code but preserve structure."""
        workshop_notebook = make_notebook_node(
            [
                make_cell("markdown", "# Workshop"),
                make_cell("code", "# Keep this setup", tags=["keep"]),
                make_cell("markdown", "## Try it yourself"),
                make_cell("code", "# Student should write this\nresult = 42"),
                make_cell("markdown", "The answer is 42", tags=["answer"]),
            ]
        )

        spec = CodeAlongOutput(format="notebook", language="en")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="code-along", format_="notebook", language="en")

        result = await processor._process_notebook_node(workshop_notebook, payload)

        cell_sources = [cell["source"] for cell in result["cells"]]
        all_sources = " ".join(cell_sources)

        # Structure preserved
        assert "Workshop" in all_sources
        assert "Try it yourself" in all_sources

        # Keep cell preserved
        assert "Keep this setup" in all_sources

        # Student code cleared - find the code cell without keep tag
        code_cells = [c for c in result["cells"] if c["cell_type"] == "code"]
        regular_code_cells = [
            c for c in code_cells if "keep" not in c.get("metadata", {}).get("tags", [])
        ]
        assert len(regular_code_cells) == 1
        assert regular_code_cells[0]["source"] == ""

        # Answer shows only prefix
        answer_cells = [
            c for c in result["cells"] if "answer" in c.get("metadata", {}).get("tags", [])
        ]
        assert len(answer_cells) == 1
        assert "*Answer:*" in answer_cells[0]["source"]
        assert "The answer is 42" not in answer_cells[0]["source"]

    @pytest.mark.asyncio
    async def test_workshop_notebook_produces_correct_speaker_output(self):
        """Speaker output should include notes with yellow styling."""
        workshop_notebook = make_notebook_node(
            [
                make_cell("markdown", "# Workshop"),
                make_cell("markdown", "Important notes for speaker", tags=["notes"]),
                make_cell("code", "print('example')"),
                make_cell("code", "# Deleted cell", tags=["del"]),
            ]
        )

        spec = SpeakerOutput(format="notebook", language="en")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="speaker", format_="notebook", language="en")

        result = await processor._process_notebook_node(workshop_notebook, payload)

        cell_sources = [cell["source"] for cell in result["cells"]]
        all_sources = " ".join(cell_sources)

        # Notes should be included with styling
        assert "Important notes for speaker" in all_sources
        assert "background: yellow" in all_sources

        # Code preserved
        assert "print('example')" in all_sources

        # Del cell excluded
        assert "Deleted cell" not in all_sources


# ============================================================================
# Kernel Cleanup Tests
# ============================================================================


class TestKernelCleanup:
    """Test kernel resource cleanup behavior.

    These tests verify that kernel resources (ZMQ sockets, kernel processes)
    are properly cleaned up to prevent "Connection reset by peer [10054]"
    errors on Windows.
    """

    @pytest.mark.asyncio
    async def test_cleanup_handles_missing_km_kc(self):
        """Verify cleanup handles case where km/kc are None.

        When an ExecutePreprocessor is created but preprocess() is never
        called (or fails very early), km and kc will be None.
        """
        from nbconvert.preprocessors import ExecutePreprocessor

        spec = SpeakerOutput(format="html")
        processor = NotebookProcessor(spec)

        # Create EP but don't call preprocess - km/kc will be None
        ep = ExecutePreprocessor(timeout=None, startup_timeout=300)

        # Should not raise even though km/kc are None
        await processor._cleanup_kernel_resources(ep, "test-cid")

    @pytest.mark.asyncio
    async def test_cleanup_handles_ep_without_km_attribute(self):
        """Verify cleanup handles EP that doesn't have km attribute at all."""
        spec = SpeakerOutput(format="html")
        processor = NotebookProcessor(spec)

        # Create a mock object that doesn't have km/kc attributes
        class FakeEP:
            pass

        fake_ep = FakeEP()

        # Should not raise
        await processor._cleanup_kernel_resources(fake_ep, "test-cid")  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_fresh_preprocessor_each_retry(self):
        """Verify new ExecutePreprocessor is created for each retry attempt.

        This test mocks ExecutePreprocessor to track how many instances
        are created when kernel dies repeatedly.
        """
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Test"),
                make_cell("code", "x = 1"),
            ]
        )

        spec = SpeakerOutput(format="html")
        processor = NotebookProcessor(spec)
        payload = make_payload("", format_="html", kind="speaker")

        ep_instances: list = []
        original_ep_class = None

        # We need to track EP creation and make preprocess fail
        with patch("clx.workers.notebook.notebook_processor.TrackingExecutePreprocessor") as MockEP:
            # Make preprocess always raise RuntimeError (kernel died)
            def create_mock_ep(*args, **kwargs):
                mock_ep = MagicMock()
                mock_ep.preprocess.side_effect = RuntimeError("Kernel died")
                mock_ep.km = None
                mock_ep.kc = None
                ep_instances.append(mock_ep)
                return mock_ep

            MockEP.side_effect = create_mock_ep

            # Also mock HTMLExporter since we won't get there
            with patch("clx.workers.notebook.notebook_processor.HTMLExporter") as MockHTML:
                mock_exporter = MagicMock()
                mock_exporter.from_notebook_node.return_value = ("<html></html>", {})
                MockHTML.return_value = mock_exporter

                # This should try multiple times then raise
                with pytest.raises(RuntimeError, match="Kernel died"):
                    await processor.create_contents(notebook, payload)

        # Should have created NUM_RETRIES_FOR_HTML (6) separate EP instances
        from clx.workers.notebook.notebook_processor import NUM_RETRIES_FOR_HTML

        assert len(ep_instances) == NUM_RETRIES_FOR_HTML

    @pytest.mark.asyncio
    async def test_cleanup_called_on_success(self):
        """Verify cleanup is called after successful notebook execution."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Test"),
                make_cell("code", "x = 1"),
            ]
        )

        spec = SpeakerOutput(format="html")
        processor = NotebookProcessor(spec)
        payload = make_payload("", format_="html", kind="speaker")

        cleanup_calls: list = []

        # Patch the cleanup method to track calls
        original_cleanup = processor._cleanup_kernel_resources

        async def tracked_cleanup(ep, cid):
            cleanup_calls.append((ep, cid))
            await original_cleanup(ep, cid)

        processor._cleanup_kernel_resources = tracked_cleanup  # type: ignore[method-assign]

        with (
            patch("clx.workers.notebook.notebook_processor.TrackingExecutePreprocessor") as MockEP,
            patch("clx.workers.notebook.notebook_processor.HTMLExporter") as MockHTML,
        ):
            mock_ep = MagicMock()
            mock_ep.preprocess.return_value = (notebook, {})
            mock_ep.km = None
            mock_ep.kc = None
            MockEP.return_value = mock_ep

            mock_exporter = MagicMock()
            mock_exporter.from_notebook_node.return_value = ("<html></html>", {})
            MockHTML.return_value = mock_exporter

            await processor.create_contents(notebook, payload)

        # Cleanup should have been called exactly once (success on first try)
        assert len(cleanup_calls) == 1

    @pytest.mark.asyncio
    async def test_cleanup_called_on_kernel_death(self):
        """Verify cleanup is called even when kernel dies (RuntimeError)."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Test"),
                make_cell("code", "x = 1"),
            ]
        )

        spec = SpeakerOutput(format="html")
        processor = NotebookProcessor(spec)
        payload = make_payload("", format_="html", kind="speaker")

        cleanup_calls: list = []

        original_cleanup = processor._cleanup_kernel_resources

        async def tracked_cleanup(ep, cid):
            cleanup_calls.append((ep, cid))
            await original_cleanup(ep, cid)

        processor._cleanup_kernel_resources = tracked_cleanup  # type: ignore[method-assign]

        call_count = 0

        with (
            patch("clx.workers.notebook.notebook_processor.TrackingExecutePreprocessor") as MockEP,
            patch("clx.workers.notebook.notebook_processor.HTMLExporter") as MockHTML,
        ):

            def create_mock_ep(*args, **kwargs):
                nonlocal call_count
                mock_ep = MagicMock()
                call_count += 1
                # Fail first 2 times, succeed on 3rd
                if call_count <= 2:
                    mock_ep.preprocess.side_effect = RuntimeError("Kernel died")
                else:
                    mock_ep.preprocess.return_value = (notebook, {})
                mock_ep.km = None
                mock_ep.kc = None
                return mock_ep

            MockEP.side_effect = create_mock_ep

            mock_exporter = MagicMock()
            mock_exporter.from_notebook_node.return_value = ("<html></html>", {})
            MockHTML.return_value = mock_exporter

            await processor.create_contents(notebook, payload)

        # Cleanup should have been called 3 times (2 failures + 1 success)
        assert len(cleanup_calls) == 3


# ============================================================================
# Source Directory Handling Tests (Docker Mode with Source Mount)
# ============================================================================


class TestSourceDirectoryHandling:
    """Test handling of source_dir parameter for Docker mode with source mount.

    In Docker mode with source mount, supporting files (data.csv, model.pkl, etc.)
    are available directly from the mounted /source directory, eliminating the need
    to decode base64-encoded other_files from the payload.
    """

    @pytest.mark.asyncio
    async def test_write_other_files_skips_when_source_dir_provided(self, tmp_path):
        """When source_dir is provided, write_other_files should skip writing."""
        spec = SpeakerOutput(format="html")
        processor = NotebookProcessor(spec)

        # Create a payload with some other_files
        test_data = b"test content"
        encoded_data = b64encode(test_data).decode("utf-8")
        payload = make_payload(
            "",
            kind="speaker",
            format_="html",
            other_files={"data.txt": encoded_data},
        )

        # Create a temp directory to be our "source" mount
        source_dir = tmp_path / "source"
        source_dir.mkdir()

        # Create another temp directory that would be written to
        write_dir = tmp_path / "write"
        write_dir.mkdir()

        # Call write_other_files with source_dir provided
        await processor.write_other_files("test-cid", write_dir, payload, source_dir=source_dir)

        # No files should have been written to write_dir
        assert list(write_dir.iterdir()) == []

    @pytest.mark.asyncio
    async def test_write_other_files_writes_when_no_source_dir(self, tmp_path):
        """When source_dir is None, write_other_files should write files."""
        spec = SpeakerOutput(format="html")
        processor = NotebookProcessor(spec)

        # Create a payload with some other_files
        test_data = b"test content"
        encoded_data = b64encode(test_data).decode("utf-8")
        payload = make_payload(
            "",
            kind="speaker",
            format_="html",
            other_files={"data.txt": encoded_data},
        )

        # Create temp directory to write to
        write_dir = tmp_path / "write"
        write_dir.mkdir()

        # Call write_other_files without source_dir
        await processor.write_other_files("test-cid", write_dir, payload, source_dir=None)

        # File should have been written
        written_file = write_dir / "data.txt"
        assert written_file.exists()
        assert written_file.read_bytes() == test_data

    @pytest.mark.asyncio
    async def test_process_notebook_accepts_source_dir_parameter(self):
        """process_notebook should accept source_dir parameter."""
        from pathlib import Path

        notebook = make_notebook_node([make_cell("markdown", "# Test")])

        spec = CompletedOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload(
            make_notebook_json([{"cell_type": "markdown", "source": "# Test"}]),
            format_="notebook",
        )

        # Should be able to call with source_dir parameter
        # (notebook format doesn't use write_other_files, so this should work)
        result = await processor.process_notebook(payload, source_dir=Path("/tmp/source"))
        assert result  # Should return something

    def test_payload_source_topic_dir_field(self):
        """NotebookPayload should have source_topic_dir field."""
        payload = make_payload(
            "",
            kind="speaker",
            format_="html",
        )

        # source_topic_dir should exist and default to empty string
        assert hasattr(payload, "source_topic_dir")
        assert payload.source_topic_dir == ""

        # Should be able to set it
        payload_with_source = NotebookPayload(
            data="",
            input_file="/test/notebook.ipynb",
            input_file_name="notebook.ipynb",
            output_file="/output/notebook.html",
            kind="speaker",
            prog_lang="python",
            language="en",
            format="html",
            correlation_id="test-123",
            source_topic_dir="/home/user/courses/slides/topic1",
        )

        assert payload_with_source.source_topic_dir == "/home/user/courses/slides/topic1"

    @pytest.mark.asyncio
    async def test_write_other_files_handles_nested_files(self, tmp_path):
        """write_other_files should create nested directories when needed."""
        spec = SpeakerOutput(format="html")
        processor = NotebookProcessor(spec)

        # Create a payload with nested other_files
        test_data = b"nested content"
        encoded_data = b64encode(test_data).decode("utf-8")
        payload = make_payload(
            "",
            kind="speaker",
            format_="html",
            other_files={"subdir/nested/data.txt": encoded_data},
        )

        write_dir = tmp_path / "write"
        write_dir.mkdir()

        # Call write_other_files without source_dir
        await processor.write_other_files("test-cid", write_dir, payload, source_dir=None)

        # Nested file should have been written with directories created
        written_file = write_dir / "subdir" / "nested" / "data.txt"
        assert written_file.exists()
        assert written_file.read_bytes() == test_data
