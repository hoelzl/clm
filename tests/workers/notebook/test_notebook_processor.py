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
import time
import uuid
from base64 import b64encode
from collections.abc import Mapping, Sequence
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import psutil
import pytest
from nbformat import NotebookNode

from clm.infrastructure.messaging.notebook_classes import NotebookPayload
from clm.workers.notebook import notebook_processor as notebook_processor_module
from clm.workers.notebook.notebook_processor import (
    CellIdGenerator,
    NotebookProcessor,
    TrackingExecutePreprocessor,
    _effective_cell_timeout,
    _normalize_jupytext_metadata_filters,
    _strip_lines_to_next_cell,
)
from clm.workers.notebook.output_spec import (
    CodeAlongOutput,
    CompletedOutput,
    PartialOutput,
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


def make_notebook_json(cells: Sequence[Mapping[str, Any]]) -> str:
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
    async def test_voiceover_cells_included_in_speaker_output(self):
        """Voiceover cells should appear in speaker output with amber styling."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Title"),
                make_cell("markdown", "Voiceover transcript here", tags=["voiceover"]),
                make_cell("code", "print('hello')"),
            ]
        )

        spec = SpeakerOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="speaker", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        voiceover_cells = [
            cell
            for cell in result["cells"]
            if "voiceover" in cell.get("metadata", {}).get("tags", [])
        ]
        assert len(voiceover_cells) == 1
        assert "background: #FFEEBA" in voiceover_cells[0]["source"]
        assert "Voiceover transcript here" in voiceover_cells[0]["source"]

    @pytest.mark.asyncio
    async def test_voiceover_cells_excluded_from_completed_output(self):
        """Voiceover cells should be removed from completed output."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Title"),
                make_cell("markdown", "Voiceover transcript here", tags=["voiceover"]),
                make_cell("code", "print('hello')"),
            ]
        )

        spec = CompletedOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="completed", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        cell_sources = [cell["source"] for cell in result["cells"]]
        assert not any("Voiceover transcript here" in src for src in cell_sources)

    @pytest.mark.asyncio
    async def test_voiceover_cells_excluded_from_codealong_output(self):
        """Voiceover cells should be removed from code-along output."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Title"),
                make_cell("markdown", "Voiceover transcript here", tags=["voiceover"]),
                make_cell("code", "print('hello')"),
            ]
        )

        spec = CodeAlongOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="code-along", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        cell_sources = [cell["source"] for cell in result["cells"]]
        assert not any("Voiceover transcript here" in src for src in cell_sources)

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


class TestVoiceoverCellStyling:
    """Test voiceover cell styling in speaker output."""

    @pytest.mark.asyncio
    async def test_voiceover_cell_wrapped_in_amber_div(self):
        """Voiceover cells should be wrapped in amber background div."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Title"),
                make_cell("markdown", "Voiceover transcript", tags=["voiceover"]),
            ]
        )

        spec = SpeakerOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="speaker", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        voiceover_cells = [
            c for c in result["cells"] if "voiceover" in c.get("metadata", {}).get("tags", [])
        ]
        assert len(voiceover_cells) == 1
        assert "background: #FFEEBA" in voiceover_cells[0]["source"]
        assert "Voiceover transcript" in voiceover_cells[0]["source"]
        assert "</div>" in voiceover_cells[0]["source"]


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
            patch("clm.workers.notebook.notebook_processor.HTMLExporter") as MockExporter,
            patch("clm.workers.notebook.notebook_processor.TrackingExecutePreprocessor") as MockEP,
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

    @pytest.mark.asyncio
    async def test_jinja_globals_include_author_and_organization(self):
        """Jinja globals should include author and organization."""
        spec = CompletedOutput(format="notebook", language="en")

        globals_ = NotebookProcessor._create_jinja_globals(
            spec, author="Dr. Jane Smith", organization="My Academy"
        )
        assert globals_["author"] == "Dr. Jane Smith"
        assert globals_["organization"] == "My Academy"

    @pytest.mark.asyncio
    async def test_jinja_globals_default_author(self):
        """Jinja globals should use default author when not specified."""
        spec = CompletedOutput(format="notebook", language="en")

        globals_ = NotebookProcessor._create_jinja_globals(spec)
        assert globals_["author"] == "Dr. Matthias Hölzl"
        assert globals_["organization"] == ""


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

    @pytest.mark.asyncio
    async def test_partial_output_splits_at_workshop_boundary(self):
        """Partial output: Completed behaviour before the first ``workshop``
        heading, CodeAlong behaviour for every cell from that heading to
        end-of-notebook. Exercises a realistic shape with a multi-slide
        workshop at the end."""
        notebook = make_notebook_node(
            [
                # ----- Pre-workshop demonstration section -----
                make_cell("markdown", "# Intro", tags=["slide"]),
                make_cell("markdown", "Speaker note about intro", tags=["notes"]),
                make_cell("code", "# Demo setup\nimport math", tags=["keep"]),
                make_cell("code", "# Worked demo\narea = math.pi * 5**2"),
                make_cell("markdown", "The demo answer is 78.5", tags=["answer"]),
                make_cell("code", "# Alternative demo approach", tags=["alt"]),
                make_cell("code", "# Deleted cell", tags=["del"]),
                make_cell("code", "# Starter scaffold", tags=["start"]),
                make_cell("code", "# Full solution for demo", tags=["completed"]),
                # ----- Workshop section (runs to EOF) -----
                make_cell("markdown", "## Workshop: Exercises", tags=["subslide", "workshop"]),
                make_cell("markdown", "## Task 1", tags=["subslide"]),
                make_cell("code", "# Student writes this\nresult = ..."),
                make_cell("markdown", "Workshop answer text", tags=["answer"]),
                make_cell("code", "# Required setup line", tags=["keep"]),
                make_cell("code", "# Hint scaffold", tags=["start"]),
                make_cell("code", "# Hidden workshop solution", tags=["completed"]),
                make_cell("markdown", "Alt workshop prose", tags=["alt"]),
                make_cell("markdown", "## Task 2", tags=["subslide"]),
                make_cell("code", "# Second student exercise\nvalue = None"),
            ]
        )

        spec = PartialOutput(format="notebook", language="en")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="partial", format_="notebook", language="en")

        result = await processor._process_notebook_node(notebook, payload)
        cells = result["cells"]
        sources = [c["source"] for c in cells]
        joined = " ".join(sources)

        # --- Pre-workshop: Completed behaviour ---
        assert "Intro" in joined, "markdown slide heading retained"
        assert "Speaker note about intro" not in joined, "notes excluded"
        assert "Demo setup" in joined, "keep cell retained with contents"
        assert "Worked demo" in joined, "regular demo code retained"
        assert "area = math.pi * 5**2" in joined, "demo code body retained"
        assert "*Answer:*" in joined, "answer prefix added pre-workshop"
        assert "The demo answer is 78.5" in joined, "answer body retained pre-workshop"
        assert "Alternative demo approach" in joined, "alt cell kept pre-workshop"
        assert "Deleted cell" not in joined, "del cell removed"
        assert "Starter scaffold" not in joined, "start cell removed pre-workshop"
        assert "Full solution for demo" in joined, "completed cell kept pre-workshop"

        # --- Workshop heading: kept, contents intact ---
        assert "Workshop: Exercises" in joined, "workshop heading retained"
        assert "Task 1" in joined, "later workshop slide heading retained"
        assert "Task 2" in joined, "multi-slide workshop fully included"

        # --- Post-workshop: CodeAlong behaviour ---
        # Student code cleared
        student_exercise_cells = [
            c
            for c in cells
            if c["cell_type"] == "code"
            and not {"keep", "start"}.intersection(c.get("metadata", {}).get("tags", []))
            and "_post_workshop" not in c.get("metadata", {}).get("tags", [])  # noqa: SIM118
        ]
        # The only un-cleared post-workshop code cells must be keep/start ones.
        # After tag stripping, _post_workshop is removed; use the fact that
        # cleared cells have empty source.
        post_workshop_code_cells = [
            c
            for c in cells
            if c["cell_type"] == "code"
            and any(
                src_fragment in c["source"]
                for src_fragment in ("Student writes this", "Second student exercise")
            )
        ]
        # Should be empty because those sources were cleared.
        assert post_workshop_code_cells == [], "post-workshop student code cleared"

        # keep/start in workshop are preserved as scaffolding
        assert "Required setup line" in joined, "post-workshop keep cell retained"
        assert "Hint scaffold" in joined, "post-workshop start cell retained (CodeAlong rule)"

        # completed/alt in workshop are dropped
        assert "Hidden workshop solution" not in joined, "completed cell dropped post-workshop"
        assert "Alt workshop prose" not in joined, "alt cell dropped post-workshop"

        # answer markdown: both pre- and post-workshop answer cells survive
        # (CompletedOutput and CodeAlongOutput both keep the cell); the
        # post-workshop one has its body cleared, leaving only the "*Answer:*"
        # prefix.
        answer_cells = [
            c
            for c in cells
            if c["cell_type"] == "markdown" and "answer" in c.get("metadata", {}).get("tags", [])
        ]
        assert len(answer_cells) == 2, "one pre-workshop + one post-workshop answer cell"
        # Post-workshop answer body is cleared; pre-workshop keeps its body.
        pre_workshop_answer = next(c for c in answer_cells if "The demo answer" in c["source"])
        post_workshop_answer = next(c for c in answer_cells if c is not pre_workshop_answer)
        assert post_workshop_answer["source"].strip() == "*Answer:*"
        assert "Workshop answer text" not in post_workshop_answer["source"]

        # --- Synthetic _post_workshop tag is stripped from output ---
        for cell in cells:
            tags = cell.get("metadata", {}).get("tags", [])
            assert "_post_workshop" not in tags, (
                f"Synthetic tag must not appear in output; saw tags={tags} on cell "
                f"{cell.get('cell_type')!r} with source={cell.get('source')[:40]!r}"
            )

    @pytest.mark.asyncio
    async def test_partial_without_workshop_behaves_like_completed(self):
        """When no ``workshop`` heading is present, Partial output matches
        Completed output — every cell stays in the pre-workshop branch."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Demo", tags=["slide"]),
                make_cell("code", "x = 1"),
                make_cell("code", "# Starter", tags=["start"]),
                make_cell("code", "# Solution", tags=["completed"]),
                make_cell("markdown", "Answer text", tags=["answer"]),
            ]
        )

        spec = PartialOutput(format="notebook", language="en")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="partial", format_="notebook", language="en")

        result = await processor._process_notebook_node(notebook, payload)
        joined = " ".join(c["source"] for c in result["cells"])

        # Completed-style: code retained, start dropped, completed kept,
        # answer prose shown.
        assert "x = 1" in joined
        assert "Starter" not in joined
        assert "Solution" in joined
        assert "Answer text" in joined


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
        with patch("clm.workers.notebook.notebook_processor.TrackingExecutePreprocessor") as MockEP:
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
            with patch("clm.workers.notebook.notebook_processor.HTMLExporter") as MockHTML:
                mock_exporter = MagicMock()
                mock_exporter.from_notebook_node.return_value = ("<html></html>", {})
                MockHTML.return_value = mock_exporter

                # Skip the exponential backoff sleeps between retries
                with patch(
                    "clm.workers.notebook.notebook_processor.asyncio.sleep", new_callable=AsyncMock
                ):
                    # This should try multiple times then raise
                    with pytest.raises(RuntimeError, match="Kernel died"):
                        await processor.create_contents(notebook, payload)

        # Should have created NUM_RETRIES_FOR_HTML (6) separate EP instances
        from clm.workers.notebook.notebook_processor import NUM_RETRIES_FOR_HTML

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
            patch("clm.workers.notebook.notebook_processor.TrackingExecutePreprocessor") as MockEP,
            patch("clm.workers.notebook.notebook_processor.HTMLExporter") as MockHTML,
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

    # Spawns a REAL ipykernel subprocess (~5s). Marked ``integration`` so it
    # runs in CI's integration step but is excluded from the per-commit fast
    # suite — keeps a real-subprocess long-pole off every local commit without
    # losing the coverage. See docs/developer-guide/testing.md.
    @pytest.mark.integration
    def test_reaping_kernel_manager_kills_grandchild_on_success(self, tmp_path):
        """Grandchildren spawned by a cell must die on kernel shutdown (happy path).

        Regression test for the Windows orphan-python.exe incident
        documented in docs/proposals/WORKER_CLEANUP_RELIABILITY.md.
        ``jupyter_client.LocalProvisioner.kill`` is ``TerminateProcess`` on
        Windows, which only kills the kernel pid. Any subprocesses the
        kernel spawned (cells using ``subprocess.Popen``, ``multiprocessing``,
        etc.) survive as orphans unless
        :class:`clm.workers.notebook.notebook_processor._ReapingKernelManager`
        walks the tree and reaps survivors.

        Without the ``_ReapingKernelManager`` hook the sleeping ``python.exe``
        grandchild outlives the kernel and this test fails; with the hook,
        the grandchild is gone by the time ``preprocess`` returns.
        """
        pid_file = tmp_path / "grandchild.pid"
        # The cell spawns a 120-second-sleep child process and records its
        # pid to a file so the test can check it after the kernel shuts down.
        # Using a file (rather than parsing cell output) keeps the test robust
        # against nbconvert output formatting changes.
        cell_source = (
            "import subprocess, sys\n"
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            f"open({str(pid_file)!r}, 'w').write(str(p.pid))\n"
        )
        notebook = make_notebook_node([make_cell("code", cell_source)])

        processor = NotebookProcessor(SpeakerOutput(format="html"))
        ep = TrackingExecutePreprocessor(processor, timeout=60, startup_timeout=60)

        ep.preprocess(notebook, resources={"metadata": {"path": str(tmp_path)}})

        assert pid_file.exists(), "Cell did not spawn the grandchild subprocess"
        grandchild_pid = int(pid_file.read_text())

        try:
            # _ReapingKernelManager runs inside nbclient's setup_kernel finally,
            # so by the time preprocess returns the grandchild should already
            # be gone. Allow a generous grace window — under xdist parallel
            # load on Windows, many concurrent subprocess spawns can delay
            # the OS's report of process exit.
            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline:
                if not psutil.pid_exists(grandchild_pid):
                    break
                time.sleep(0.05)

            assert not psutil.pid_exists(grandchild_pid), (
                f"Grandchild pid {grandchild_pid} survived kernel shutdown — "
                f"_ReapingKernelManager did not reap it"
            )
        finally:
            # Belt-and-braces cleanup if the test failed, so we do not leak
            # a 120-second sleeper across the rest of the test run.
            if psutil.pid_exists(grandchild_pid):
                try:
                    psutil.Process(grandchild_pid).kill()
                except psutil.NoSuchProcess:
                    pass

    # Real ipykernel subprocess (~5s); ``integration`` for the same reason as
    # ``test_reaping_kernel_manager_kills_grandchild_on_success`` above.
    @pytest.mark.integration
    def test_reaping_kernel_manager_kills_grandchild_on_cell_error(self, tmp_path):
        """Grandchildren must be reaped even when a later cell raises.

        Same regression as ``test_reaping_kernel_manager_kills_grandchild_on_success``
        but exercises the error path: cell 0 spawns a grandchild, cell 1
        raises, ``preprocess`` re-raises as ``CellExecutionError``. The
        reap must still run — it lives in the ``finally`` of nbclient's
        ``setup_kernel`` context manager, so it happens regardless of
        whether execution succeeded.
        """
        pid_file = tmp_path / "grandchild.pid"
        spawn_source = (
            "import subprocess, sys\n"
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            f"open({str(pid_file)!r}, 'w').write(str(p.pid))\n"
        )
        notebook = make_notebook_node(
            [
                make_cell("code", spawn_source),
                make_cell("code", "raise RuntimeError('boom')"),
            ]
        )

        processor = NotebookProcessor(SpeakerOutput(format="html"))
        ep = TrackingExecutePreprocessor(processor, timeout=60, startup_timeout=60)

        with pytest.raises(Exception):  # nbconvert wraps this as CellExecutionError
            ep.preprocess(notebook, resources={"metadata": {"path": str(tmp_path)}})

        assert pid_file.exists(), "Cell did not spawn the grandchild subprocess"
        grandchild_pid = int(pid_file.read_text())

        try:
            # Generous deadline — error-path cleanup unwinds more nbclient
            # state before reaping, and xdist parallel load on Windows can
            # delay the OS's report of process exit.
            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline:
                if not psutil.pid_exists(grandchild_pid):
                    break
                time.sleep(0.05)

            assert not psutil.pid_exists(grandchild_pid), (
                f"Grandchild pid {grandchild_pid} survived error-path shutdown — "
                f"_ReapingKernelManager did not reap it"
            )
        finally:
            if psutil.pid_exists(grandchild_pid):
                try:
                    psutil.Process(grandchild_pid).kill()
                except psutil.NoSuchProcess:
                    pass


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


# ============================================================================
# Metadata Stripping Tests — slide_id and for_slide
# ============================================================================


class TestMetadataStripping:
    """Test that slide_id and for_slide are stripped from output cells."""

    @pytest.mark.asyncio
    async def test_slide_id_stripped_from_completed_output(self):
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Title", tags=["slide"]),
                make_cell("code", "x = 1"),
            ]
        )
        # Inject slide_id into cell metadata
        notebook.cells[0]["metadata"]["slide_id"] = "title-slide"
        notebook.cells[1]["metadata"]["slide_id"] = "code-cell"

        spec = CompletedOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="completed", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        for cell in result["cells"]:
            assert "slide_id" not in cell["metadata"]

    @pytest.mark.asyncio
    async def test_for_slide_stripped_from_speaker_output(self):
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Title", tags=["slide"]),
                make_cell("markdown", "Speaker notes here", tags=["voiceover"]),
            ]
        )
        notebook.cells[0]["metadata"]["slide_id"] = "title-slide"
        notebook.cells[1]["metadata"]["for_slide"] = "title-slide"

        spec = SpeakerOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="speaker", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        for cell in result["cells"]:
            assert "slide_id" not in cell["metadata"]
            assert "for_slide" not in cell["metadata"]

    @pytest.mark.asyncio
    async def test_slide_id_stripped_from_codealong_output(self):
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Title", tags=["slide"]),
                make_cell("code", "x = 1", tags=["keep"]),
            ]
        )
        notebook.cells[0]["metadata"]["slide_id"] = "intro"
        notebook.cells[1]["metadata"]["slide_id"] = "code-1"

        spec = CodeAlongOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="code-along", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        for cell in result["cells"]:
            assert "slide_id" not in cell["metadata"]

    @pytest.mark.asyncio
    async def test_other_metadata_preserved(self):
        """Stripping slide_id/for_slide should not affect other metadata."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Title", tags=["slide"]),
            ]
        )
        notebook.cells[0]["metadata"]["slide_id"] = "title"
        notebook.cells[0]["metadata"]["lang"] = "en"

        spec = SpeakerOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="speaker", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        cell = result["cells"][0]
        assert "slide_id" not in cell["metadata"]
        assert cell["metadata"].get("lang") == "en"

    @pytest.mark.asyncio
    async def test_cells_without_metadata_keys_unaffected(self):
        """Cells that don't have slide_id/for_slide should process normally."""
        notebook = make_notebook_node(
            [
                make_cell("markdown", "# Title", tags=["slide"]),
                make_cell("code", "x = 1"),
            ]
        )

        spec = CompletedOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="completed", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        assert len(result["cells"]) == 2
        for cell in result["cells"]:
            assert "slide_id" not in cell["metadata"]
            assert "for_slide" not in cell["metadata"]


class TestJupytextMetadataFilterNormalization:
    """``_normalize_jupytext_metadata_filters`` deterministically sorts
    the CSV that jupytext writes into ``metadata.jupytext.*_filter``.

    Jupytext builds these fields by joining a Python ``set``; under
    randomized ``PYTHONHASHSEED`` the same .py input therefore yields
    .ipynb files that differ only in CSV-entry order. The post-read
    normalization removes that noise from the build's output.
    """

    def test_sorts_cell_metadata_filter_csv(self):
        nb = NotebookNode({"metadata": {"jupytext": {"cell_metadata_filter": "tags,lang,-all"}}})
        _normalize_jupytext_metadata_filters(nb)
        assert nb["metadata"]["jupytext"]["cell_metadata_filter"] == "-all,lang,tags"

    def test_different_permutations_normalize_identically(self):
        nb_a = NotebookNode({"metadata": {"jupytext": {"cell_metadata_filter": "tags,lang,-all"}}})
        nb_b = NotebookNode({"metadata": {"jupytext": {"cell_metadata_filter": "lang,-all,tags"}}})
        _normalize_jupytext_metadata_filters(nb_a)
        _normalize_jupytext_metadata_filters(nb_b)
        assert (
            nb_a["metadata"]["jupytext"]["cell_metadata_filter"]
            == nb_b["metadata"]["jupytext"]["cell_metadata_filter"]
        )

    def test_normalizes_notebook_metadata_filter_too(self):
        nb = NotebookNode(
            {
                "metadata": {
                    "jupytext": {
                        "notebook_metadata_filter": "kernelspec,jupytext,-all",
                    }
                }
            }
        )
        _normalize_jupytext_metadata_filters(nb)
        assert nb["metadata"]["jupytext"]["notebook_metadata_filter"] == "-all,jupytext,kernelspec"

    def test_single_entry_unchanged(self):
        nb = NotebookNode({"metadata": {"jupytext": {"cell_metadata_filter": "-all"}}})
        _normalize_jupytext_metadata_filters(nb)
        assert nb["metadata"]["jupytext"]["cell_metadata_filter"] == "-all"

    def test_missing_jupytext_metadata_is_noop(self):
        nb = NotebookNode({"metadata": {}})
        _normalize_jupytext_metadata_filters(nb)
        assert nb["metadata"] == {}


class TestLinesToNextCellStripping:
    """``_strip_lines_to_next_cell`` removes jupytext's layout artifact so that
    split and bilingual builds produce byte-equivalent output (issue #133).

    ``lines_to_next_cell`` is recorded by jupytext when the actual blank-line
    count between two cells differs from its PEP 8 lookahead heuristic. Because
    that heuristic depends on the *identity* of the physical next cell, the
    same logical cell receives the value in a split deck (next cell is a DE
    markdown) but not in a bilingual deck (next cell is an EN code clone that
    is later filtered out). Stripping the field unconditionally converges both.
    """

    def test_strips_field_from_all_cells(self):
        cells = [
            make_cell("code", "for x in xs:\n    pass"),
            make_cell("markdown", "# heading"),
            make_cell("code", "def f():\n    pass"),
        ]
        cells[0]["metadata"]["lines_to_next_cell"] = 1
        cells[2]["metadata"]["lines_to_next_cell"] = 2
        _strip_lines_to_next_cell(cells)
        for cell in cells:
            assert "lines_to_next_cell" not in cell["metadata"]

    def test_no_op_when_field_absent(self):
        cells = [make_cell("code", "print('hi')"), make_cell("markdown", "# x")]
        _strip_lines_to_next_cell(cells)
        for cell in cells:
            assert "lines_to_next_cell" not in cell["metadata"]

    def test_preserves_other_metadata(self):
        cell = make_cell("code", "x = 1", tags=["keep"], lang="de")
        cell["metadata"]["lines_to_next_cell"] = 3
        _strip_lines_to_next_cell([cell])
        assert "lines_to_next_cell" not in cell["metadata"]
        assert cell["metadata"]["tags"] == ["keep"]
        assert cell["metadata"]["lang"] == "de"

    @pytest.mark.asyncio
    async def test_process_notebook_node_strips_field(self):
        """The processing pipeline removes ``lines_to_next_cell`` from output."""
        notebook = make_notebook_node(
            [
                make_cell("code", "for x in xs:\n    pass"),
                make_cell("markdown", "# heading"),
                make_cell("code", "def f():\n    pass"),
            ]
        )
        notebook["cells"][0]["metadata"]["lines_to_next_cell"] = 1
        notebook["cells"][2]["metadata"]["lines_to_next_cell"] = 2

        spec = CompletedOutput(format="notebook")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="completed", format_="notebook")

        result = await processor._process_notebook_node(notebook, payload)

        for cell in result["cells"]:
            assert "lines_to_next_cell" not in cell["metadata"]

    def test_split_and_bilingual_converge(self):
        """Parsing a deck both as a bilingual file and as a jupytext-split
        single-language file yields the same surviving cell metadata once
        ``lines_to_next_cell`` is stripped — the exact scenario from #133.

        This unit-isolates the jupytext-read + language-filter + strip steps
        without a full build cycle. The fixture is a minimal reproduction of
        the divergent layout: a DE code cell whose source-successor is a DE
        markdown cell in the split form but an EN code clone in the bilingual
        form, followed by a ``def`` cell that triggers jupytext's PEP 8
        lookahead asymmetry.
        """
        import jupytext

        from clm.workers.notebook.utils.jupyter_utils import (
            is_cell_included_for_language,
        )

        bilingual = (
            '# %% lang="de"\n'
            "for vague in questions:\n"
            "    print(vague)\n"
            "\n"
            '# %% lang="en"\n'
            "for vague in questions:\n"
            "    print(vague)\n"
            "\n"
            '# %% [markdown] lang="de"\n'
            "# ## Aufgabe\n"
            "\n"
            '# %% lang="de"\n'
            "def solve(x):\n"
            "    return x\n"
            "\n"
            "\n"
            '# %% lang="en"\n'
            "def solve(x):\n"
            "    return x\n"
        )
        split_de = (
            '# %% lang="de"\n'
            "for vague in questions:\n"
            "    print(vague)\n"
            "\n"
            '# %% [markdown] lang="de"\n'
            "# ## Aufgabe\n"
            "\n"
            '# %% lang="de"\n'
            "def solve(x):\n"
            "    return x\n"
        )

        def filtered_de_metadata(text: str) -> list[int | None]:
            nb = jupytext.reads(text, fmt="py:percent")
            cells = [c for c in nb.cells if is_cell_included_for_language(c, "de")]
            _strip_lines_to_next_cell(cells)
            return [c["metadata"].get("lines_to_next_cell") for c in cells]

        bil_meta = filtered_de_metadata(bilingual)
        split_meta = filtered_de_metadata(split_de)

        # Same number of surviving DE cells and no residual layout artifact.
        assert bil_meta == split_meta
        assert all(v is None for v in bil_meta)


# ============================================================================
# skip_errors post-execution cleanup
# ============================================================================


def _error_output() -> dict:
    """An nbformat-shaped error output."""
    return {
        "output_type": "error",
        "ename": "RuntimeError",
        "evalue": "service down",
        "traceback": ["Traceback (most recent call last):", "RuntimeError: service down"],
    }


def _stream_output(text: str = "ok\n") -> dict:
    return {"output_type": "stream", "name": "stdout", "text": text}


class TestSkipErrorsPostExecutionCleanup:
    """``_clear_error_outputs`` strips error tracebacks and records warnings."""

    def test_clears_error_outputs_and_records_warning(self):
        cells = [
            make_cell("markdown", "# Title"),
            make_cell("code", "ok = 1"),
            make_cell("code", "raise RuntimeError('service down')"),
            make_cell("code", "use(ok)"),
        ]
        cells[1]["outputs"] = [_stream_output()]
        cells[1]["execution_count"] = 1
        cells[2]["outputs"] = [_error_output()]
        cells[2]["execution_count"] = 2
        cells[3]["outputs"] = [_error_output()]
        cells[3]["execution_count"] = 3
        notebook = make_notebook_node(cells)

        spec = SpeakerOutput(format="html")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="speaker", format_="html").model_copy(
            update={"skip_errors": True}
        )

        processor._clear_error_outputs(notebook, payload)

        # Unrelated cells are untouched.
        assert notebook["cells"][1]["outputs"] == [_stream_output()]
        assert notebook["cells"][1]["execution_count"] == 1

        # Error-bearing cells are cleared.
        for idx in (2, 3):
            assert notebook["cells"][idx]["outputs"] == []
            assert notebook["cells"][idx]["execution_count"] is None

        warnings = processor.get_warnings()
        assert len(warnings) == 1
        warning = warnings[0]
        assert warning.category == "skip_errors_cell_failed"
        assert warning.details["cell_indices"] == [2, 3]
        assert warning.severity == "low"

    def test_no_warning_when_no_errors_present(self):
        cells = [make_cell("code", "x = 1")]
        cells[0]["outputs"] = [_stream_output()]
        cells[0]["execution_count"] = 1
        notebook = make_notebook_node(cells)

        spec = SpeakerOutput(format="html")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="speaker", format_="html").model_copy(
            update={"skip_errors": True}
        )

        processor._clear_error_outputs(notebook, payload)

        assert notebook["cells"][0]["outputs"] == [_stream_output()]
        assert processor.get_warnings() == []

    def test_markdown_cells_are_ignored(self):
        notebook = make_notebook_node([make_cell("markdown", "# Title")])

        spec = SpeakerOutput(format="html")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="speaker", format_="html").model_copy(
            update={"skip_errors": True}
        )

        processor._clear_error_outputs(notebook, payload)

        assert processor.get_warnings() == []


# ============================================================================
# HTTP replay bootstrap injection/strip
# ============================================================================


class TestHttpReplayBootstrap:
    """Injecting and stripping the vcrpy bootstrap cell."""

    def test_inject_prepends_cell_with_marker_metadata(self):
        from clm.workers.notebook.notebook_processor import (
            _inject_http_replay_bootstrap,
        )

        nb = make_notebook_node([make_cell("code", "import requests; requests.get('http://x')")])

        _inject_http_replay_bootstrap(nb, "/abs/slides_test.http-cassette.yaml", "replay")

        assert len(nb["cells"]) == 2
        injected = nb["cells"][0]
        assert injected["cell_type"] == "code"
        assert injected["metadata"]["clm_injected"] == "http_replay"
        assert "del" in injected["metadata"]["tags"]
        assert "import vcr" in injected["source"]
        # record_mode maps replay -> vcrpy's "none"
        assert "'none'" in injected["source"]
        assert "/abs/slides_test.http-cassette.yaml" in injected["source"]
        # The bootstrap must register an atexit hook so vcrpy flushes the
        # cassette to disk on kernel shutdown (refresh/once recording paths).
        assert "atexit" in injected["source"]
        assert "_clm_ctx.__exit__" in injected["source"]
        # And it must patch Cassette.append for eager save so partial
        # recordings survive a forceful kernel kill.
        assert "_clm_eager_append" in injected["source"]
        assert "_save" in injected["source"]

    def test_inject_pins_allow_playback_repeats(self):
        # The bootstrap must opt into vcrpy's ``allow_playback_repeats``
        # so a deck that issues the same request N times still replays
        # successfully against a canonical cassette with exactly one
        # entry per fingerprint.  The host-side merger in
        # :mod:`clm.workers.notebook.http_replay_cassette` dedupes by
        # ``(method, uri, body)``; without this flag, vcrpy serves the
        # entry on call #1 and raises ``CannotOverwriteExistingCassetteException``
        # on calls #2..N — even though every matcher succeeded —
        # because ``record_mode='none'`` consumes each entry once.
        # Regression for issue #95 (A).
        from clm.workers.notebook.notebook_processor import (
            _inject_http_replay_bootstrap,
        )

        nb = make_notebook_node([make_cell("code", "pass")])
        _inject_http_replay_bootstrap(nb, "/abs/c.yaml", "replay")

        src = nb["cells"][0]["source"]
        assert "allow_playback_repeats=True" in src, (
            "Bootstrap is missing ``allow_playback_repeats=True``; deduped "
            "cassettes will fail strict replay on the second identical request."
        )

    def test_inject_defaults_ignore_langsmith_host(self):
        # LangSmith trace uploads (POST api.smith.langchain.com/runs/multipart)
        # carry per-build timestamps + UUIDs in the request body. The
        # ``clm_json_body`` matcher then never matches a previously-recorded
        # entry, so every build records a fresh one — making cassettes grow
        # on every rebuild even when the slide source is unchanged. The
        # bootstrap defaults vcrpy's ``ignore_hosts`` to skip LangSmith so
        # those requests pass through to the real network and never enter
        # the cassette.
        from clm.workers.notebook.notebook_processor import (
            _inject_http_replay_bootstrap,
        )

        nb = make_notebook_node([make_cell("code", "pass")])
        _inject_http_replay_bootstrap(nb, "/abs/c.yaml", "replay")

        src = nb["cells"][0]["source"]
        assert "ignore_hosts=" in src, "Bootstrap is missing the ``ignore_hosts`` arg."
        assert "api.smith.langchain.com" in src, (
            "Bootstrap default ``ignore_hosts`` must include LangSmith's "
            "telemetry endpoint; otherwise cassettes grow on every rebuild."
        )

    def test_inject_ignore_hosts_overridable(self):
        from clm.workers.notebook.notebook_processor import (
            _inject_http_replay_bootstrap,
        )

        nb = make_notebook_node([make_cell("code", "pass")])
        _inject_http_replay_bootstrap(
            nb,
            "/abs/c.yaml",
            "replay",
            ignore_hosts=("foo.example.com", "bar.example.com"),
        )

        src = nb["cells"][0]["source"]
        assert "foo.example.com" in src
        assert "bar.example.com" in src
        # And the default shouldn't sneak in when caller passed an explicit list
        assert "api.smith.langchain.com" not in src

    def test_inject_ignore_hosts_can_be_empty(self):
        from clm.workers.notebook.notebook_processor import (
            _inject_http_replay_bootstrap,
        )

        nb = make_notebook_node([make_cell("code", "pass")])
        _inject_http_replay_bootstrap(
            nb,
            "/abs/c.yaml",
            "replay",
            ignore_hosts=(),
        )

        src = nb["cells"][0]["source"]
        # Empty list literal in the rendered source
        assert "ignore_hosts=[]" in src

    def test_inject_pins_body_in_match_on(self):
        # The bootstrap must include a body matcher in vcrpy's ``match_on`` tuple.
        # vcrpy's default matcher only looks at method+scheme+host+port+path+
        # query, so two POSTs to the same chat-completion endpoint with
        # different request bodies (e.g. ``stream=true`` vs ``stream=false``)
        # are indistinguishable. Without body matching, vcrpy serves the
        # interactions in on-disk order, which silently breaks replay when
        # the call sequence diverges from the recording order -- producing
        # confusing downstream errors such as
        # ``'tuple' object has no attribute 'model_dump'`` in
        # langchain-openrouter when it receives a JSON ChatResult instead
        # of an EventStream.  Pin the matcher so we never regress.
        #
        # We use a custom ``clm_json_body`` matcher rather than vcrpy's
        # built-in ``body`` because of two latent vcrpy bugs that break
        # byte-level matching for JSON POSTs:
        #
        # 1. ``filter_post_data_parameters`` re-serializes every JSON body
        #    via ``json.dumps()`` (pretty-printed with spaces) during the
        #    record-time filter pass, even when no replacement key
        #    actually matches. The live ``httpx`` request body is compact
        #    JSON (no spaces), so byte comparison fails.
        # 2. The built-in ``body`` matcher's JSON transform is gated on
        #    ``headers.get("Content-Type")`` (case-sensitive lookup),
        #    while real clients (and vcrpy's own header storage) use
        #    lowercase ``content-type``. The transform never runs.
        #
        # Our ``clm_json_body`` matcher does case-insensitive content-type
        # detection and parses JSON bodies before comparison, so the
        # formatting difference no longer matters.
        from clm.workers.notebook.notebook_processor import (
            _inject_http_replay_bootstrap,
        )

        nb = make_notebook_node([make_cell("code", "pass")])
        _inject_http_replay_bootstrap(nb, "/abs/c.yaml", "replay")

        src = nb["cells"][0]["source"]
        assert "match_on=" in src
        assert '"clm_json_body"' in src
        assert "register_matcher" in src
        assert "_clm_json_body_matcher" in src

    def test_clm_json_body_matcher_handles_whitespace_differences(self):
        # Live ``httpx`` requests send compact JSON (no spaces) while
        # vcrpy's ``filter_post_data_parameters`` rewrites JSON bodies via
        # ``json.dumps()`` (pretty, with spaces) into the cassette. The
        # custom ``clm_json_body`` matcher must treat the two as equal so
        # strict replay doesn't fail on every chat-completion call.
        from types import SimpleNamespace

        # ``vcrpy`` is an optional ``[replay]`` extra. The bootstrap
        # template ``import vcr`` line below blows up in CI test envs
        # that don't install replay deps; the matcher we exercise lives
        # *inside* that bootstrap and only matters when replay is in
        # use, so skipping cleanly here is correct.
        pytest.importorskip("vcr")

        from clm.workers.notebook.notebook_processor import (
            _inject_http_replay_bootstrap,
        )

        nb = make_notebook_node([make_cell("code", "pass")])
        _inject_http_replay_bootstrap(nb, "/abs/c.yaml", "replay")
        src = nb["cells"][0]["source"]

        # Execute the bootstrap template in an isolated namespace and grab
        # the matcher function. The template imports ``vcr`` and registers
        # cassette context as side effects; suppress those by skipping
        # ``use_cassette``/atexit by exec'ing only up to the matcher def.
        # Easiest robust approach: just grab the function via the registered
        # matcher dict on the global vcr instance.
        ns: dict = {}
        # Replace the use_cassette call so it doesn't actually open one
        stub_src = src.split("_clm_vcr_instance.register_persister")[0]
        stub_src += "_clm_vcr_instance.register_persister(_ClmDeepCopyPersister)\n"
        exec(stub_src, ns)
        matcher = ns["_clm_json_body_matcher"]

        # Pretty JSON (with spaces, as the filter would produce) vs compact JSON
        pretty = b'{"messages": [{"role": "user", "content": "hi"}], "stream": false}'
        compact = b'{"messages":[{"role":"user","content":"hi"}],"stream":false}'

        def mk(body: bytes, content_type: str | None = "application/json"):
            headers = {}
            if content_type is not None:
                # Use lowercase header name to match what vcrpy stores after
                # filtering and what httpx sends at runtime.
                headers["content-type"] = [content_type]
            return SimpleNamespace(body=body, headers=headers)

        # JSON bodies that differ only in whitespace must match.
        matcher(mk(pretty), mk(compact))  # no AssertionError

        # JSON bodies that differ semantically must NOT match.
        with pytest.raises(AssertionError):
            matcher(
                mk(pretty), mk(b'{"messages":[{"role":"user","content":"bye"}],"stream":false}')
            )

        # Non-JSON bodies fall back to byte comparison.
        matcher(
            mk(b"raw bytes", content_type="text/plain"), mk(b"raw bytes", content_type="text/plain")
        )
        with pytest.raises(AssertionError):
            matcher(
                mk(b"raw bytes", content_type="text/plain"),
                mk(b"other bytes", content_type="text/plain"),
            )

        # Case-insensitive content-type detection: uppercase header name still
        # triggers the JSON path.
        upper = SimpleNamespace(body=pretty, headers={"Content-Type": ["application/json"]})
        matcher(upper, mk(compact))

    def test_inject_uses_vcr_mode_mapping(self):
        from clm.workers.notebook.notebook_processor import (
            _inject_http_replay_bootstrap,
        )

        nb = make_notebook_node([make_cell("code", "pass")])
        _inject_http_replay_bootstrap(nb, "/abs/c.yaml", "refresh")

        # refresh maps to vcrpy's "all"
        assert "'all'" in nb["cells"][0]["source"]

    def test_inject_maps_new_episodes_to_vcrpy_value(self):
        from clm.workers.notebook.notebook_processor import (
            _inject_http_replay_bootstrap,
        )

        nb = make_notebook_node([make_cell("code", "pass")])
        _inject_http_replay_bootstrap(nb, "/abs/c.yaml", "new-episodes")

        # new-episodes maps to vcrpy's "new_episodes" (underscore form).
        assert "'new_episodes'" in nb["cells"][0]["source"]

    def test_strip_removes_only_marker_cells(self):
        from clm.workers.notebook.notebook_processor import (
            _inject_http_replay_bootstrap,
            _strip_injected_cells,
        )

        nb = make_notebook_node(
            [
                make_cell("markdown", "# Title"),
                make_cell("code", "x = 1"),
            ]
        )
        _inject_http_replay_bootstrap(nb, "/abs/c.yaml", "replay")
        assert len(nb["cells"]) == 3

        _strip_injected_cells(nb)

        assert len(nb["cells"]) == 2
        assert nb["cells"][0]["cell_type"] == "markdown"
        assert nb["cells"][1]["source"] == "x = 1"

    def test_strip_is_noop_when_no_injection(self):
        from clm.workers.notebook.notebook_processor import _strip_injected_cells

        nb = make_notebook_node([make_cell("code", "x = 1"), make_cell("markdown", "done")])
        original = [dict(c) for c in nb["cells"]]

        _strip_injected_cells(nb)

        assert len(nb["cells"]) == 2
        assert nb["cells"][0]["source"] == original[0]["source"]
        assert nb["cells"][1]["source"] == original[1]["source"]

    def test_resolve_paths_returns_none_without_mode(self):
        spec = SpeakerOutput(format="html")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="speaker", format_="html")

        assert processor._resolve_cassette_paths(payload, source_dir=None) is None

    def test_resolve_paths_returns_none_for_disabled_mode(self):
        spec = SpeakerOutput(format="html")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="speaker", format_="html").model_copy(
            update={
                "http_replay_mode": "disabled",
                "http_replay_cassette_name": "c.yaml",
            }
        )

        assert processor._resolve_cassette_paths(payload, source_dir=None) is None

    def test_resolve_paths_returns_none_without_cassette_name(self):
        spec = SpeakerOutput(format="html")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="speaker", format_="html").model_copy(
            update={"http_replay_mode": "replay", "source_topic_dir": "/tmp/topic"}
        )

        assert processor._resolve_cassette_paths(payload, source_dir=None) is None

    def test_resolve_paths_returns_none_without_target_dir(self):
        spec = SpeakerOutput(format="html")
        processor = NotebookProcessor(spec)
        payload = make_payload("", kind="speaker", format_="html").model_copy(
            update={
                "http_replay_mode": "once",
                "http_replay_cassette_name": "slides.http-cassette.yaml",
            }
        )

        assert processor._resolve_cassette_paths(payload, source_dir=None) is None

    def test_resolve_paths_uses_source_topic_dir_in_direct_mode(self, tmp_path):
        spec = SpeakerOutput(format="html")
        processor = NotebookProcessor(spec)
        topic = tmp_path / "topic"
        topic.mkdir()
        payload = make_payload("", kind="speaker", format_="html").model_copy(
            update={
                "http_replay_mode": "once",
                "http_replay_cassette_name": "slides.http-cassette.yaml",
                "source_topic_dir": str(topic),
            }
        )

        paths = processor._resolve_cassette_paths(payload, source_dir=None)
        assert paths is not None
        assert paths.canonical == topic / "slides.http-cassette.yaml"
        # Staging file is in the same directory but with a unique suffix.
        assert paths.staging.parent == paths.canonical.parent
        assert paths.staging.name.startswith(paths.canonical.name + ".staging-")
        assert paths.staging != paths.canonical

    def test_resolve_paths_prefers_source_dir_in_docker_mode(self, tmp_path):
        spec = SpeakerOutput(format="html")
        processor = NotebookProcessor(spec)
        container_source = tmp_path / "container_source"
        container_source.mkdir()
        host_topic = tmp_path / "host_topic"  # different path; must NOT be used
        host_topic.mkdir()
        payload = make_payload("", kind="speaker", format_="html").model_copy(
            update={
                "http_replay_mode": "once",
                "http_replay_cassette_name": "slides.http-cassette.yaml",
                "source_topic_dir": str(host_topic),
            }
        )

        paths = processor._resolve_cassette_paths(payload, source_dir=container_source)
        assert paths is not None
        assert paths.canonical == container_source / "slides.http-cassette.yaml"

    def test_resolve_paths_two_workers_get_distinct_staging_paths(self, tmp_path):
        """Concurrent workers building the same notebook must not collide on staging."""
        spec = SpeakerOutput(format="html")
        processor = NotebookProcessor(spec)
        topic = tmp_path / "topic"
        topic.mkdir()
        payload = make_payload("", kind="speaker", format_="html").model_copy(
            update={
                "http_replay_mode": "new-episodes",
                "http_replay_cassette_name": "slides.http-cassette.yaml",
                "source_topic_dir": str(topic),
            }
        )

        paths_a = processor._resolve_cassette_paths(payload, source_dir=None)
        paths_b = processor._resolve_cassette_paths(payload, source_dir=None)
        assert paths_a is not None and paths_b is not None
        assert paths_a.canonical == paths_b.canonical
        assert paths_a.staging != paths_b.staging

    def test_maybe_inject_skips_when_paths_none(self):
        spec = SpeakerOutput(format="html")
        processor = NotebookProcessor(spec)
        nb = make_notebook_node([make_cell("code", "x = 1")])
        payload = make_payload("", kind="speaker", format_="html")

        injected = processor._maybe_inject_http_replay(nb, payload, paths=None)

        assert injected is False
        assert len(nb["cells"]) == 1

    def test_maybe_inject_injects_when_paths_provided(self, tmp_path):
        from clm.workers.notebook.http_replay_cassette import CassettePaths

        spec = SpeakerOutput(format="html")
        processor = NotebookProcessor(spec)
        nb = make_notebook_node([make_cell("code", "x = 1")])
        payload = make_payload("", kind="speaker", format_="html").model_copy(
            update={
                "http_replay_mode": "once",
                "http_replay_cassette_name": "_cassettes/slides.http-cassette.yaml",
            }
        )
        paths = CassettePaths(
            canonical=tmp_path / "_cassettes" / "slides.http-cassette.yaml",
            staging=tmp_path / "_cassettes" / "slides.http-cassette.yaml.staging-abc",
        )

        injected = processor._maybe_inject_http_replay(nb, payload, paths)

        assert injected is True
        assert len(nb["cells"]) == 2
        assert nb["cells"][0]["metadata"]["clm_injected"] == "http_replay"
        # Bootstrap must reference the absolute staging path, not the
        # relative cassette name from the payload. The path is emitted
        # via ``repr()`` so on Windows the embedded backslashes are
        # escaped — compare against ``repr(str(...))`` accordingly.
        assert repr(str(paths.staging))[1:-1] in nb["cells"][0]["source"]
        # And the canonical path's own name (which is part of the
        # staging name) must appear too — a quick check that the
        # canonical-to-staging mapping was honored.
        assert paths.staging.name in nb["cells"][0]["source"]


# ============================================================================
# Cassette persistence: merge, locking, durability under forceful kill.
# ============================================================================


_SAMPLE_CASSETTE_TEMPLATE = """interactions:
- request:
    method: GET
    uri: {uri}
    headers: {{}}
    body: null
  response:
    status: {{code: 200, message: OK}}
    headers:
      content-type: [text/plain]
    body: {{string: '{body}'}}
version: 1
"""


def _write_cassette(path, *, uri: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_SAMPLE_CASSETTE_TEMPLATE.format(uri=uri, body=body), encoding="utf-8")


def _touch_completion_marker(staging) -> None:
    """Drop a sentinel ``.completed`` marker beside ``staging``.

    The marker's *existence* is the signal — the merge does not inspect
    its contents. Tests that exercise the "fold into canonical" path
    need this beside every staging file they expect to be folded; tests
    that exercise the "discard / leave alone" branches deliberately
    omit it.
    """
    marker = staging.parent / f"{staging.name}.completed"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("{}\n", encoding="utf-8")


class TestCassetteMerge:
    """Locked merge of per-worker staging files into the canonical cassette."""

    def test_merge_creates_canonical_when_only_staging_exists(self, tmp_path):
        pytest.importorskip("vcr")
        pytest.importorskip("filelock")
        from clm.workers.notebook.http_replay_cassette import (
            CassettePaths,
            merge_staging_into_canonical,
        )

        canonical = tmp_path / "slides.http-cassette.yaml"
        staging = tmp_path / "slides.http-cassette.yaml.staging-worker-a"
        _write_cassette(staging, uri="http://example/a", body="A")
        _touch_completion_marker(staging)

        paths = CassettePaths(canonical=canonical, staging=staging)
        merged = merge_staging_into_canonical(paths)

        assert merged == 1
        assert canonical.exists()
        assert not staging.exists()
        content = canonical.read_text(encoding="utf-8")
        assert "http://example/a" in content

    def test_merge_is_noop_with_no_staging_files(self, tmp_path):
        pytest.importorskip("vcr")
        pytest.importorskip("filelock")
        from clm.workers.notebook.http_replay_cassette import (
            CassettePaths,
            merge_staging_into_canonical,
        )

        canonical = tmp_path / "slides.http-cassette.yaml"
        staging = tmp_path / "slides.http-cassette.yaml.staging-worker-a"
        # Neither file exists.
        paths = CassettePaths(canonical=canonical, staging=staging)

        merged = merge_staging_into_canonical(paths)

        assert merged == 0
        assert not canonical.exists()

    def test_merge_sweeps_orphan_staging_files(self, tmp_path):
        """Staging files from previously-killed workers must be absorbed too."""
        pytest.importorskip("vcr")
        pytest.importorskip("filelock")
        from clm.workers.notebook.http_replay_cassette import (
            CassettePaths,
            merge_staging_into_canonical,
        )

        canonical = tmp_path / "slides.http-cassette.yaml"
        orphan_one = tmp_path / "slides.http-cassette.yaml.staging-orphan-1"
        orphan_two = tmp_path / "slides.http-cassette.yaml.staging-orphan-2"
        own = tmp_path / "slides.http-cassette.yaml.staging-own"
        _write_cassette(orphan_one, uri="http://example/orphan1", body="O1")
        _touch_completion_marker(orphan_one)
        _write_cassette(orphan_two, uri="http://example/orphan2", body="O2")
        _touch_completion_marker(orphan_two)
        _write_cassette(own, uri="http://example/own", body="OWN")
        _touch_completion_marker(own)

        paths = CassettePaths(canonical=canonical, staging=own)
        merged = merge_staging_into_canonical(paths)

        assert merged == 3
        assert canonical.exists()
        assert not orphan_one.exists()
        assert not orphan_two.exists()
        assert not own.exists()
        content = canonical.read_text(encoding="utf-8")
        assert "http://example/orphan1" in content
        assert "http://example/orphan2" in content
        assert "http://example/own" in content

    def test_merge_deduplicates_against_canonical(self, tmp_path):
        """An interaction already present in canonical must not be appended twice."""
        pytest.importorskip("vcr")
        pytest.importorskip("filelock")
        from clm.workers.notebook.http_replay_cassette import (
            CassettePaths,
            merge_staging_into_canonical,
        )

        canonical = tmp_path / "slides.http-cassette.yaml"
        staging = tmp_path / "slides.http-cassette.yaml.staging-worker"
        _write_cassette(canonical, uri="http://example/same", body="SAME")
        _write_cassette(staging, uri="http://example/same", body="SAME")
        _touch_completion_marker(staging)

        paths = CassettePaths(canonical=canonical, staging=staging)
        merged = merge_staging_into_canonical(paths)

        assert merged == 1
        content = canonical.read_text(encoding="utf-8")
        # Should appear exactly once after dedup.
        assert content.count("http://example/same") == 1

    def test_merge_appends_new_interactions_to_existing_canonical(self, tmp_path):
        pytest.importorskip("vcr")
        pytest.importorskip("filelock")
        from clm.workers.notebook.http_replay_cassette import (
            CassettePaths,
            merge_staging_into_canonical,
        )

        canonical = tmp_path / "slides.http-cassette.yaml"
        staging = tmp_path / "slides.http-cassette.yaml.staging-worker"
        _write_cassette(canonical, uri="http://example/old", body="OLD")
        _write_cassette(staging, uri="http://example/new", body="NEW")
        _touch_completion_marker(staging)

        paths = CassettePaths(canonical=canonical, staging=staging)
        merge_staging_into_canonical(paths)

        content = canonical.read_text(encoding="utf-8")
        assert "http://example/old" in content
        assert "http://example/new" in content

    def test_merge_writes_lf_endings_on_every_platform(self, tmp_path):
        """Cassette files must have LF endings even on Windows.

        ``Path.write_text`` without ``newline=`` defaults to
        ``os.linesep`` (``\\r\\n`` on Windows). With ``.gitattributes``
        forcing ``eol=lf``, that produces a permanent LF↔CRLF
        flip-flop: builds write CRLF, ``git checkout`` writes LF, next
        build writes CRLF again, and so on.
        """
        pytest.importorskip("vcr")
        pytest.importorskip("filelock")
        from clm.workers.notebook.http_replay_cassette import (
            CassettePaths,
            merge_staging_into_canonical,
        )

        canonical = tmp_path / "slides.http-cassette.yaml"
        staging = tmp_path / "slides.http-cassette.yaml.staging-worker"
        _write_cassette(staging, uri="http://example/a", body="A")
        _touch_completion_marker(staging)

        paths = CassettePaths(canonical=canonical, staging=staging)
        merge_staging_into_canonical(paths)

        raw = canonical.read_bytes()
        assert b"\r\n" not in raw, (
            "Canonical cassette contains CRLF line endings on this platform. "
            "_atomic_write_text must pass newline='\\n' to Path.write_text "
            "so cassettes are LF-only regardless of os.linesep."
        )

    def test_concurrent_merges_do_not_lose_interactions(self, tmp_path):
        """Two workers merging different recordings must produce a union, not a last-writer-wins."""
        pytest.importorskip("vcr")
        pytest.importorskip("filelock")
        import threading

        from clm.workers.notebook.http_replay_cassette import (
            CassettePaths,
            merge_staging_into_canonical,
        )

        canonical = tmp_path / "slides.http-cassette.yaml"
        staging_a = tmp_path / "slides.http-cassette.yaml.staging-worker-a"
        staging_b = tmp_path / "slides.http-cassette.yaml.staging-worker-b"
        _write_cassette(staging_a, uri="http://example/a", body="A-PAYLOAD")
        _touch_completion_marker(staging_a)
        _write_cassette(staging_b, uri="http://example/b", body="B-PAYLOAD")
        _touch_completion_marker(staging_b)

        paths_a = CassettePaths(canonical=canonical, staging=staging_a)
        paths_b = CassettePaths(canonical=canonical, staging=staging_b)

        errors: list[Exception] = []

        def _run(paths):
            try:
                merge_staging_into_canonical(paths)
            except Exception as exc:  # pragma: no cover — debug aid
                errors.append(exc)

        t_a = threading.Thread(target=_run, args=(paths_a,))
        t_b = threading.Thread(target=_run, args=(paths_b,))
        t_a.start()
        t_b.start()
        t_a.join(timeout=30)
        t_b.join(timeout=30)

        assert not errors, f"Merge threads raised: {errors!r}"
        assert canonical.exists()
        content = canonical.read_text(encoding="utf-8")
        # The union of both workers' recordings must be present — neither
        # may have overwritten the other.
        assert "http://example/a" in content
        assert "http://example/b" in content
        assert not staging_a.exists()
        assert not staging_b.exists()

    def test_seed_does_not_delete_concurrent_workers_staging(self, tmp_path):
        """Worker B's seed must not delete Worker A's still-active staging file.

        Regression for issue #86: PR #83 added an orphan sweep inside
        ``seed_staging_from_canonical`` that globs every ``*.staging-*``
        sibling of the canonical cassette and unlinks them after folding
        them into canonical. The sweep can't distinguish dead orphans
        from active staging files of currently-running concurrent
        workers, so a second worker's seed deletes the first worker's
        live staging file. When the first worker's kernel finally boots
        and tries to load that staging file, vcrpy silently treats the
        missing file as an empty cassette and raises
        ``CannotOverwriteExistingCassetteException`` on the first
        request in ``record_mode="none"`` (replay) mode.
        """
        pytest.importorskip("vcr")
        pytest.importorskip("filelock")
        from clm.workers.notebook.http_replay_cassette import (
            resolve_paths,
            seed_staging_from_canonical,
        )

        topic = tmp_path / "topic"
        topic.mkdir()
        cassette_name = "slides.http-cassette.yaml"
        canonical = topic / cassette_name
        _write_cassette(canonical, uri="http://example/seed", body="SEED")

        paths_a = resolve_paths(topic, cassette_name)
        paths_b = resolve_paths(topic, cassette_name)
        assert paths_a.staging != paths_b.staging

        # Worker A seeds first. Its staging file is now on disk and the
        # kernel — when it eventually boots — will point vcrpy at this
        # exact path.
        seed_staging_from_canonical(paths_a)
        assert paths_a.staging.exists(), "Worker A's seed failed to create its staging file."

        # Worker B starts. Its seed must NOT delete A's active staging.
        seed_staging_from_canonical(paths_b)

        assert paths_a.staging.exists(), (
            "Worker B's seed deleted Worker A's active staging file — "
            "regression for issue #86 race condition."
        )
        assert paths_b.staging.exists(), "Worker B's seed failed to create its own staging file."

    def test_two_concurrent_workers_full_seed_record_merge_cycle(self, tmp_path):
        """End-to-end: two workers seed, record distinct interactions, merge — canonical converges to the union.

        Models the real-world lifecycle of two concurrent notebook
        workers building the same topic with ``http-replay="yes"``:

        1. Canonical cassette already contains a previously-recorded
           interaction (``/seed``).
        2. Worker A and Worker B each ``seed_staging_from_canonical``.
        3. Each worker's "kernel" loads its staging file — this is the
           regression point for issue #86. Under the buggy seed-time
           sweep, Worker B's seed unlinked Worker A's staging, so
           Worker A's kernel would see an empty cassette and raise
           ``CannotOverwriteExistingCassetteException`` on the first
           replay request. The fix in Phase 2 keeps both stagings
           intact and seeded with the canonical contents.
        4. Each worker records a new interaction (``/a``, ``/b``) by
           rewriting its staging file with the seed + the new
           interaction. (We synthesize this rather than spin up a real
           vcrpy ``record_mode`` to keep the test fast and Docker-free.)
        5. Both workers concurrently merge their staging into canonical
           via threads — exercising the cross-process file lock at the
           same time as the seed-survival invariant.
        6. Final canonical must contain all three interactions
           (deduplicated — ``/seed`` appears only once).
        """
        pytest.importorskip("vcr")
        pytest.importorskip("filelock")
        import threading

        from clm.workers.notebook.http_replay_cassette import (
            merge_staging_into_canonical,
            resolve_paths,
            seed_staging_from_canonical,
        )

        topic = tmp_path / "topic"
        topic.mkdir()
        cassette_name = "slides.http-cassette.yaml"
        canonical = topic / cassette_name
        _write_cassette(canonical, uri="http://example/seed", body="SEED")

        paths_a = resolve_paths(topic, cassette_name)
        paths_b = resolve_paths(topic, cassette_name)

        # Step 2: both workers seed in sequence.
        seed_staging_from_canonical(paths_a)
        seed_staging_from_canonical(paths_b)

        # Step 3: each "kernel" loads its staging — the seed must survive.
        # This is the issue #86 regression guard: under the old buggy
        # seed-time sweep, paths_a.staging was unlinked by paths_b's seed
        # and the read below would either raise FileNotFoundError or
        # return an empty cassette.
        assert paths_a.staging.exists(), (
            "Worker A's staging file was deleted before its kernel could load it — "
            "regression for issue #86."
        )
        assert "http://example/seed" in paths_a.staging.read_text(encoding="utf-8"), (
            "Worker A's staging exists but lost the seed interaction — regression for issue #86."
        )
        assert paths_b.staging.exists()
        assert "http://example/seed" in paths_b.staging.read_text(encoding="utf-8")

        # Step 4: each worker's kernel "records" a new interaction by
        # rewriting its staging with the seed plus the new entry. The
        # real vcrpy bootstrap eager-saves after every interaction; we
        # simulate the final post-execution state here.
        _write_two_interactions(
            paths_a.staging,
            ("http://example/seed", "SEED"),
            ("http://example/a", "A"),
        )
        _write_two_interactions(
            paths_b.staging,
            ("http://example/seed", "SEED"),
            ("http://example/b", "B"),
        )

        # Each "host" writes its completion marker after the kernel
        # returned cleanly — the merge below treats this as the
        # signal that both staging files hold complete recordings.
        _touch_completion_marker(paths_a.staging)
        _touch_completion_marker(paths_b.staging)

        # Step 5: concurrent merges — both workers finish their
        # post-execution merge at the same time.
        errors: list[Exception] = []

        def _merge(paths):
            try:
                merge_staging_into_canonical(paths)
            except Exception as exc:  # pragma: no cover — debug aid
                errors.append(exc)

        t_a = threading.Thread(target=_merge, args=(paths_a,))
        t_b = threading.Thread(target=_merge, args=(paths_b,))
        t_a.start()
        t_b.start()
        t_a.join(timeout=30)
        t_b.join(timeout=30)

        assert not errors, f"Concurrent merge threads raised: {errors!r}"

        # Step 6: canonical contains the union of all interactions,
        # deduplicated. Both stagings have been cleaned up.
        assert canonical.exists()
        content = canonical.read_text(encoding="utf-8")
        assert "http://example/seed" in content
        assert "http://example/a" in content
        assert "http://example/b" in content
        # The seed must appear exactly once — both workers carried it in
        # via the seed copy, and dedup must collapse the duplicates.
        assert content.count("http://example/seed") == 1
        assert not paths_a.staging.exists()
        assert not paths_b.staging.exists()


class TestCompletionMarker:
    """Per-staging completion marker plumbing (issue #115 Phase 1).

    The marker file ``<staging>.completed`` is the host's signal that
    a worker's notebook execution returned cleanly and the staging
    file's recordings are safe to fold into the canonical cassette.
    These tests cover the low-level helpers only; the merge logic
    that *consumes* the marker is wired up in Phase 2.
    """

    def test_marker_path_sits_beside_staging(self, tmp_path):
        from clm.workers.notebook.http_replay_cassette import marker_path

        staging = tmp_path / "_cassettes" / "slides.http-cassette.yaml.staging-abc"
        expected = tmp_path / "_cassettes" / "slides.http-cassette.yaml.staging-abc.completed"

        assert marker_path(staging) == expected

    def test_has_completion_marker_false_when_absent(self, tmp_path):
        from clm.workers.notebook.http_replay_cassette import has_completion_marker

        staging = tmp_path / "slides.http-cassette.yaml.staging-xyz"
        # Staging file doesn't even exist — marker still reports False.
        assert has_completion_marker(staging) is False

        # Staging file exists, marker does not.
        staging.write_text("interactions: []\nversion: 1\n", encoding="utf-8")
        assert has_completion_marker(staging) is False

    def test_has_completion_marker_true_when_present(self, tmp_path):
        from clm.workers.notebook.http_replay_cassette import (
            has_completion_marker,
            marker_path,
        )

        staging = tmp_path / "slides.http-cassette.yaml.staging-xyz"
        staging.write_text("interactions: []\nversion: 1\n", encoding="utf-8")
        marker_path(staging).write_text("{}\n", encoding="utf-8")

        assert has_completion_marker(staging) is True

    def test_write_completion_marker_creates_file(self, tmp_path):
        from clm.workers.notebook.http_replay_cassette import (
            CassettePaths,
            has_completion_marker,
            marker_path,
            write_completion_marker,
        )

        canonical = tmp_path / "_cassettes" / "slides.http-cassette.yaml"
        staging = tmp_path / "_cassettes" / "slides.http-cassette.yaml.staging-abc"
        paths = CassettePaths(canonical=canonical, staging=staging)

        # Parent dir might not exist yet — writer must create it.
        write_completion_marker(paths)

        assert marker_path(staging).is_file()
        assert has_completion_marker(staging) is True

    def test_write_completion_marker_payload_is_valid_json_with_iso_timestamp(self, tmp_path):
        from clm.workers.notebook.http_replay_cassette import (
            CassettePaths,
            marker_path,
            write_completion_marker,
        )

        canonical = tmp_path / "_cassettes" / "slides.http-cassette.yaml"
        staging = tmp_path / "_cassettes" / "slides.http-cassette.yaml.staging-abc"
        paths = CassettePaths(canonical=canonical, staging=staging)

        write_completion_marker(paths)

        data = json.loads(marker_path(staging).read_text(encoding="utf-8"))
        assert data["schema"] == 1
        assert isinstance(data["host_pid"], int)
        assert data["host_pid"] == psutil.Process().pid
        # ISO-8601 UTC timestamp — parseable and not ridiculously old.
        import datetime as _dt

        parsed = _dt.datetime.fromisoformat(data["completed_at"])
        assert parsed.tzinfo is not None
        delta = _dt.datetime.now(_dt.timezone.utc) - parsed
        assert abs(delta.total_seconds()) < 60

    def test_write_completion_marker_is_idempotent(self, tmp_path):
        from clm.workers.notebook.http_replay_cassette import (
            CassettePaths,
            marker_path,
            write_completion_marker,
        )

        canonical = tmp_path / "slides.http-cassette.yaml"
        staging = tmp_path / "slides.http-cassette.yaml.staging-abc"
        paths = CassettePaths(canonical=canonical, staging=staging)

        write_completion_marker(paths)
        first = marker_path(staging).read_text(encoding="utf-8")
        # Sleep a hair so the timestamp can differ on a fast clock.
        time.sleep(0.01)
        write_completion_marker(paths)
        second = marker_path(staging).read_text(encoding="utf-8")

        # Both writes succeeded; file still exists and parses as JSON.
        # We don't assert the contents are byte-identical — the
        # timestamp may legitimately advance — only that the marker
        # is stable through re-writes (no exception, file remains).
        assert marker_path(staging).is_file()
        assert json.loads(first)["schema"] == 1
        assert json.loads(second)["schema"] == 1

    def test_marker_filename_is_ignored_for_output(self, tmp_path):
        """Markers must never travel into worker payloads or public output.

        Same class as ``.staging-*`` files — build-internal artifacts.
        Tests the ``is_ignored_file_for_output`` predicate that the
        payload builder and output copier both consult.
        """
        from clm.infrastructure.utils.path_utils import is_ignored_file_for_output

        marker = tmp_path / "_cassettes" / "slides.http-cassette.yaml.staging-abc.completed"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("{}\n", encoding="utf-8")

        assert is_ignored_file_for_output(marker) is True


class TestDiscriminatingMerge:
    """Marker-aware merge: only completed sessions fold into canonical (issue #115).

    These tests cover the per-file branches inside
    :func:`merge_staging_into_canonical`. The marker file's *existence*
    discriminates "this staging file holds a complete recording session,
    safe to fold" from "this staging file is a partial chain whose
    completion is unknown — either a concurrent worker still recording
    or a previously-aborted session whose recordings would poison the
    canonical cassette." The ``sweep_orphans`` flag tells the merge
    which environment it is running in (single-threaded pre-build
    sweep vs. concurrent post-execution worker).
    """

    def test_merge_folds_entries_when_marker_present(self, tmp_path):
        """Default per-worker merge folds markered staging into canonical."""
        pytest.importorskip("vcr")
        pytest.importorskip("filelock")
        from clm.workers.notebook.http_replay_cassette import (
            CassettePaths,
            marker_path,
            merge_staging_into_canonical,
        )

        canonical = tmp_path / "slides.http-cassette.yaml"
        staging = tmp_path / "slides.http-cassette.yaml.staging-worker"
        _write_cassette(staging, uri="http://example/marked", body="MARKED")
        _touch_completion_marker(staging)

        paths = CassettePaths(canonical=canonical, staging=staging)
        merged = merge_staging_into_canonical(paths)  # default sweep_orphans=False

        assert merged == 1
        assert canonical.exists()
        assert not staging.exists()
        assert not marker_path(staging).exists()  # marker cleaned up after fold
        assert "http://example/marked" in canonical.read_text(encoding="utf-8")

    def test_merge_skips_markerless_in_per_worker_mode(self, tmp_path):
        """A markerless staging file must be left strictly alone by per-worker merge.

        The marker may be missing because (a) the producing worker is
        still recording — taking the cross-process lock for its own
        merge later — or (b) the producing worker's kernel died. In
        the per-worker context we can't distinguish (a) from (b)
        safely, so the contract is "don't touch, don't fold, don't
        delete." The next build's pre-build sweep (single-threaded)
        decides their fate.
        """
        pytest.importorskip("vcr")
        pytest.importorskip("filelock")
        from clm.workers.notebook.http_replay_cassette import (
            CassettePaths,
            merge_staging_into_canonical,
        )

        canonical = tmp_path / "slides.http-cassette.yaml"
        staging = tmp_path / "slides.http-cassette.yaml.staging-worker"
        _write_cassette(staging, uri="http://example/partial", body="PARTIAL")
        # No marker.

        paths = CassettePaths(canonical=canonical, staging=staging)
        merged = merge_staging_into_canonical(paths)

        # Markerless staging is not folded; canonical is unchanged.
        assert merged == 0
        assert not canonical.exists()
        # Critically: staging file must still be on disk for a later
        # sweep to discard or for the producing worker to complete.
        assert staging.exists()
        assert "http://example/partial" in staging.read_text(encoding="utf-8")

    def test_merge_discards_markerless_in_sweep_mode(self, tmp_path):
        """A markerless staging file is treated as a confirmed orphan under sweep_orphans=True.

        The pre-build sweep runs before any worker starts, so any
        markerless staging file present is from a previous build and
        is therefore an aborted-session partial recording. Folding it
        would poison canonical (this is the issue #115 mechanism);
        discarding restores the invariant.
        """
        pytest.importorskip("vcr")
        pytest.importorskip("filelock")
        from clm.workers.notebook.http_replay_cassette import (
            CassettePaths,
            merge_staging_into_canonical,
        )

        canonical = tmp_path / "slides.http-cassette.yaml"
        staging = tmp_path / "slides.http-cassette.yaml.staging-prev-build"
        _write_cassette(staging, uri="http://example/partial", body="PARTIAL")
        # Pre-existing canonical with an unrelated entry must survive
        # the sweep — discarding is per-staging, not "wipe canonical."
        _write_cassette(canonical, uri="http://example/keep", body="KEEP")

        paths = CassettePaths(canonical=canonical, staging=staging)
        merged = merge_staging_into_canonical(paths, sweep_orphans=True)

        assert merged == 0  # no markered files were folded
        assert not staging.exists()  # orphan discarded
        # Canonical must still contain its prior entry; the sweep
        # discards orphan-staging entries, not canonical entries.
        content = canonical.read_text(encoding="utf-8")
        assert "http://example/keep" in content
        assert "http://example/partial" not in content

    def test_merge_deletes_marker_after_successful_fold(self, tmp_path):
        """Folded markered staging cleans up both the staging file and the marker."""
        pytest.importorskip("vcr")
        pytest.importorskip("filelock")
        from clm.workers.notebook.http_replay_cassette import (
            CassettePaths,
            marker_path,
            merge_staging_into_canonical,
        )

        canonical = tmp_path / "slides.http-cassette.yaml"
        staging = tmp_path / "slides.http-cassette.yaml.staging-worker"
        _write_cassette(staging, uri="http://example/x", body="X")
        _touch_completion_marker(staging)
        assert marker_path(staging).exists()

        paths = CassettePaths(canonical=canonical, staging=staging)
        merge_staging_into_canonical(paths)

        # Both staging and its marker must be gone — leaving a
        # marker behind would mark a no-longer-existing staging file
        # as completed and confuse future sweeps.
        assert not staging.exists()
        assert not marker_path(staging).exists()

    def test_persist_recorded_cassette_writes_marker_on_success(self, tmp_path):
        """On the success path the host writes the marker before merging.

        Goes through the real :meth:`_persist_recorded_cassette` host
        method rather than calling :func:`write_completion_marker`
        directly, to lock in the integration contract: a successful
        execution path always produces a marker, regardless of how
        the merge subsequently behaves.
        """
        pytest.importorskip("vcr")
        pytest.importorskip("filelock")
        from clm.workers.notebook.http_replay_cassette import (
            CassettePaths,
            has_completion_marker,
        )
        from clm.workers.notebook.notebook_processor import NotebookProcessor

        canonical = tmp_path / "slides.http-cassette.yaml"
        staging = tmp_path / "slides.http-cassette.yaml.staging-worker"
        _write_cassette(staging, uri="http://example/x", body="X")
        paths = CassettePaths(canonical=canonical, staging=staging)

        spec = CompletedOutput(format="html", language="en")
        processor = NotebookProcessor(spec)
        notebook_json = make_notebook_json([make_cell("code", "x = 1")])
        payload = make_payload(notebook_json, format_="html", kind="completed").model_copy(
            update={"http_replay_mode": "once"}
        )

        processor._persist_recorded_cassette(
            "cid-success",
            payload,
            paths,
            execution_succeeded=True,
        )

        # Marker was written → staging + marker were merged → both
        # cleaned up by the merge. The success-path contract is
        # observable through the canonical containing the entry.
        assert canonical.exists()
        assert "http://example/x" in canonical.read_text(encoding="utf-8")
        # Staging gone, marker gone — they were folded.
        assert not staging.exists()
        assert not has_completion_marker(staging)

    def test_persist_recorded_cassette_omits_marker_on_failure(self, tmp_path):
        """On the failure path the host must NOT write the marker.

        Without a marker the staging file is treated as a partial
        chain and is left on disk for the next build's pre-build
        sweep to discard. The merge is still invoked (so sibling
        completed workers can finish their folds) but this worker's
        staging is untouched.
        """
        pytest.importorskip("vcr")
        pytest.importorskip("filelock")
        from clm.workers.notebook.http_replay_cassette import (
            CassettePaths,
            has_completion_marker,
        )
        from clm.workers.notebook.notebook_processor import NotebookProcessor

        canonical = tmp_path / "slides.http-cassette.yaml"
        staging = tmp_path / "slides.http-cassette.yaml.staging-failed"
        _write_cassette(staging, uri="http://example/partial", body="PARTIAL")
        paths = CassettePaths(canonical=canonical, staging=staging)

        spec = CompletedOutput(format="html", language="en")
        processor = NotebookProcessor(spec)
        notebook_json = make_notebook_json([make_cell("code", "x = 1")])
        payload = make_payload(notebook_json, format_="html", kind="completed").model_copy(
            update={"http_replay_mode": "once"}
        )

        processor._persist_recorded_cassette(
            "cid-failure",
            payload,
            paths,
            execution_succeeded=False,
        )

        # No marker → merge left the staging file alone → canonical
        # was not touched.
        assert not has_completion_marker(staging)
        assert staging.exists(), (
            "Failure path must leave staging on disk so the next "
            "pre-build sweep can discard any partial chain."
        )
        assert not canonical.exists()

    def test_concurrent_workers_dont_consume_each_others_active_staging(self, tmp_path):
        """Worker A's merge must not delete or fold Worker B's still-recording staging file.

        Worker A finishes execution and starts its post-execution
        merge (default ``sweep_orphans=False``). Worker B is still
        recording — its staging is on disk but markerless. The
        contract: A's merge sees B's markerless staging, leaves it
        alone, and folds only its own markered staging. B can then
        complete and run its own merge unaffected.

        This is the structural fix that eliminates the latent
        seed/merge race the marker design addresses (the visible
        symptom in issue #115 is the chain-poisoning).
        """
        pytest.importorskip("vcr")
        pytest.importorskip("filelock")
        from clm.workers.notebook.http_replay_cassette import (
            CassettePaths,
            marker_path,
            merge_staging_into_canonical,
        )

        canonical = tmp_path / "slides.http-cassette.yaml"
        staging_a = tmp_path / "slides.http-cassette.yaml.staging-worker-a"
        staging_b = tmp_path / "slides.http-cassette.yaml.staging-worker-b"
        _write_cassette(staging_a, uri="http://example/a", body="A")
        _touch_completion_marker(staging_a)  # A finished cleanly.
        _write_cassette(staging_b, uri="http://example/b-partial", body="B")
        # B has no marker — still recording.

        paths_a = CassettePaths(canonical=canonical, staging=staging_a)
        merge_staging_into_canonical(paths_a)

        # A's entries are in canonical; A's staging + marker are gone.
        assert canonical.exists()
        assert "http://example/a" in canonical.read_text(encoding="utf-8")
        assert not staging_a.exists()
        assert not marker_path(staging_a).exists()

        # B's still-active staging must be untouched.
        assert staging_b.exists(), (
            "Worker A's merge deleted Worker B's still-active staging — "
            "the marker-discriminator contract was violated."
        )
        assert "http://example/b-partial" in staging_b.read_text(encoding="utf-8")
        # And B's entries must NOT have leaked into canonical (until
        # B writes its own marker and runs its own merge).
        assert "http://example/b-partial" not in canonical.read_text(encoding="utf-8")


def _write_two_interactions(
    path,
    first: tuple[str, str],
    second: tuple[str, str],
) -> None:
    """Write a 2-interaction vcrpy YAML cassette at ``path``."""
    body = (
        "interactions:\n"
        "- request:\n"
        "    method: GET\n"
        f"    uri: {first[0]}\n"
        "    headers: {}\n"
        "    body: null\n"
        "  response:\n"
        "    status: {code: 200, message: OK}\n"
        "    headers:\n"
        "      content-type: [text/plain]\n"
        f"    body: {{string: '{first[1]}'}}\n"
        "- request:\n"
        "    method: GET\n"
        f"    uri: {second[0]}\n"
        "    headers: {}\n"
        "    body: null\n"
        "  response:\n"
        "    status: {code: 200, message: OK}\n"
        "    headers:\n"
        "      content-type: [text/plain]\n"
        f"    body: {{string: '{second[1]}'}}\n"
        "version: 1\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


class TestIssue115PartialChainRegression:
    """End-to-end §C regression: aborted partial chains must not poison canonical.

    The bug filed as issue #115 is structural. An aborted recording
    session can leave a chain-opener entry on disk (request body B1,
    response text T1) without its chain-closer (whose request body
    would embed T1). On the next build, the pre-fix additive merger
    folded the orphan into canonical first-seen-wins, so a later
    completed session whose chain-opener had the same dedup key but a
    *different* response T2 was silently skipped — the chain in
    canonical then paired the chain-opener with T1 while the
    chain-closer (also folded, distinct dedup key) expected the
    response to have been T2. Replay broke on the chain-closer with
    ``CannotOverwriteExistingCassetteException``.

    These tests drive the merge layer end-to-end (no kernel needed)
    against realistic two-staging scenarios and lock in the
    marker-based fix: aborted sessions are markerless and either
    discarded by the next pre-build sweep or left strictly alone by
    the per-worker post-execution merge.
    """

    # Shared chain fixture used by both regression scenarios. The
    # chain-opener URI is identical across A's aborted recording and
    # B's completed recording, so the dedup key collides in exactly
    # the way that the pre-fix "first-seen wins" merger relied on.
    _OPENER_URI = "http://llm.example/v1/chat?prompt=clarify-Q3"
    _OPENER_RESPONSE_ABORTED = "CLARIFIED-T1-STALE"
    _OPENER_RESPONSE_GOOD = "CLARIFIED-T2-GOOD"
    _CLOSER_URI = "http://llm.example/v1/chat?prompt=tutor-CLARIFIED-T2-GOOD"
    _CLOSER_RESPONSE = "FINAL-ANSWER"

    def test_pre_build_sweep_then_completed_session_lands_consistent_chain(self, tmp_path):
        """Walk the §C scenario chronologically across two builds.

        Build 1: Session A's kernel dies after the chain-opener
        recorded but before the chain-closer ran. No marker is written
        (the host's success path is never reached).

        Build 2 start: the pre-build sweep discards A's markerless
        staging without touching canonical.

        Build 2 worker: Session B runs cleanly — both the chain-opener
        (same request, *different* response) and the chain-closer
        (request embedding B's opener response) are recorded, marker
        written. B's per-worker merge folds the chain into canonical.

        Post-fix outcome: canonical's chain-opener response is B's,
        the chain-closer is present, and the closer's URI references
        the response paired with the opener — the chain is internally
        consistent and a future replay can walk it.
        """
        pytest.importorskip("vcr")
        pytest.importorskip("filelock")
        from clm.workers.notebook.http_replay_cassette import (
            CassettePaths,
            marker_path,
            merge_staging_into_canonical,
        )

        canonical = tmp_path / "slides_010_prompt_templates.http-cassette.yaml"

        # Build 1: A aborts mid-chain. Staging on disk, no marker.
        # The ``a-aborted`` suffix sorts before ``b-completed`` so the
        # pre-fix alphabetical merge would have folded A first — the
        # exact slot that admitted the stale response.
        staging_a = canonical.parent / f"{canonical.name}.staging-a-aborted"
        _write_cassette(staging_a, uri=self._OPENER_URI, body=self._OPENER_RESPONSE_ABORTED)
        assert not marker_path(staging_a).exists()

        # Build 2 start: pre-build sweep runs single-threaded before
        # any worker. The staging field is irrelevant for the sweep;
        # it globs every ``*.staging-*`` sibling of canonical.
        sweep_paths = CassettePaths(
            canonical=canonical,
            staging=canonical.parent / f"{canonical.name}.staging-sweep",
        )
        merged = merge_staging_into_canonical(sweep_paths, sweep_orphans=True)

        assert merged == 0, "Sweep must not fold the markerless orphan."
        assert not staging_a.exists(), "Sweep must discard the markerless orphan."
        # Sweep saw no markered work, so it does not write canonical.
        assert not canonical.exists(), (
            "Sweep must not create or touch canonical when only "
            "markerless orphans were present — atomic-write churn for "
            "no semantic change would be a regression."
        )

        # Build 2 worker: B records the full chain and completes.
        staging_b = canonical.parent / f"{canonical.name}.staging-b-completed"
        _write_two_interactions(
            staging_b,
            (self._OPENER_URI, self._OPENER_RESPONSE_GOOD),
            (self._CLOSER_URI, self._CLOSER_RESPONSE),
        )
        _touch_completion_marker(staging_b)

        merged = merge_staging_into_canonical(CassettePaths(canonical=canonical, staging=staging_b))
        assert merged == 1

        content = canonical.read_text(encoding="utf-8")

        # Canonical's chain-opener stores B's response, not A's stale
        # one — the §C symptom would have flipped these assertions.
        assert self._OPENER_RESPONSE_GOOD in content
        assert self._OPENER_RESPONSE_ABORTED not in content, (
            "A's aborted-session response leaked into canonical — issue #115 regression."
        )
        # Chain-closer present, references B's opener response: the
        # chain in canonical is internally consistent.
        assert self._CLOSER_URI in content
        assert self._CLOSER_RESPONSE in content

        # No debris on disk after the two-step rollout completes.
        assert not staging_b.exists()
        assert not marker_path(staging_b).exists()
        assert not staging_a.exists()
        assert not marker_path(staging_a).exists()

    def test_aborted_and_completed_stagings_present_concurrently_keep_completed_response(
        self, tmp_path
    ):
        """Negative regression: with both A (aborted) and B (completed) on
        disk at merge time, canonical must hold B's response — not A's.

        Pre-fix, :func:`merge_staging_into_canonical` globbed every
        staging file, sorted alphabetically, and folded each in order
        with the first occurrence of a dedup key winning.
        ``staging-a-aborted`` sorts before ``staging-b-completed``, so
        canonical would have been seeded with A's chain-opener
        response (``T1-stale``); B's chain-opener — same dedup key —
        would have been skipped, and B's chain-closer would point at
        a response that no longer appears anywhere in canonical. That
        is the exact §C poisoning mechanism.

        Post-fix the per-worker merge leaves A (markerless) strictly
        alone — its entries never reach canonical, regardless of glob
        order. B (markered) folds cleanly and canonical holds B's
        consistent chain. A's staging stays on disk for the next
        single-threaded pre-build sweep to discard.
        """
        pytest.importorskip("vcr")
        pytest.importorskip("filelock")
        from clm.workers.notebook.http_replay_cassette import (
            CassettePaths,
            marker_path,
            merge_staging_into_canonical,
        )

        canonical = tmp_path / "slides_010_prompt_templates.http-cassette.yaml"

        # Both stagings present at the same instant. ``a-aborted`` sorts
        # alphabetically first — the exact slot the pre-fix merger
        # would have used to seed canonical with the stale T1.
        staging_a = canonical.parent / f"{canonical.name}.staging-a-aborted"
        staging_b = canonical.parent / f"{canonical.name}.staging-b-completed"
        _write_cassette(staging_a, uri=self._OPENER_URI, body=self._OPENER_RESPONSE_ABORTED)
        _write_two_interactions(
            staging_b,
            (self._OPENER_URI, self._OPENER_RESPONSE_GOOD),
            (self._CLOSER_URI, self._CLOSER_RESPONSE),
        )
        _touch_completion_marker(staging_b)

        # Per-worker merge: the context Worker B's post-execution
        # finally block uses to land its recording.
        merged = merge_staging_into_canonical(CassettePaths(canonical=canonical, staging=staging_b))

        # Only B was folded; A was left strictly alone.
        assert merged == 1
        content = canonical.read_text(encoding="utf-8")
        assert self._OPENER_RESPONSE_GOOD in content
        assert self._OPENER_RESPONSE_ABORTED not in content, (
            "Markerless A's response leaked into canonical — the "
            "pre-#115 'first-seen wins' behaviour has regressed."
        )
        assert self._CLOSER_URI in content
        assert self._CLOSER_RESPONSE in content

        # B's staging + marker cleaned up by the fold.
        assert not staging_b.exists()
        assert not marker_path(staging_b).exists()
        # A's staging stays on disk for the next pre-build sweep to
        # decide on. The per-worker merge must not touch it.
        assert staging_a.exists(), (
            "Worker B's merge deleted markerless A's staging — the "
            "'leave alone in per-worker mode' contract was violated."
        )
        assert not marker_path(staging_a).exists()


class TestBootstrapDurability:
    """The bootstrap must keep the cassette current even under forceful termination."""

    def test_bootstrap_eager_saves_after_each_interaction(self, tmp_path):
        """A recorded interaction must be on disk immediately, without waiting for atexit.

        Regression for the bug where vcrpy buffered every interaction in
        memory and only wrote them on graceful kernel shutdown — so a
        kernel killed forcibly mid-execution (typically because the
        build-level timeout fired and the parent worker was
        TerminateProcess'd) discarded every recording.
        """
        pytest.importorskip("vcr")
        import atexit as _atexit_module
        import socket
        import urllib.request
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from threading import Thread

        from clm.workers.notebook.notebook_processor import (
            _HTTP_REPLAY_BOOTSTRAP_TEMPLATE,
            _HTTP_REPLAY_MODE_TO_VCR_MODE,
        )

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"hello")

            def log_message(self, *args, **kwargs):
                pass

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        server = HTTPServer(("127.0.0.1", port), _Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()

        cassette_path = tmp_path / "test.http-cassette.yaml"
        source = _HTTP_REPLAY_BOOTSTRAP_TEMPLATE.format(
            record_mode=_HTTP_REPLAY_MODE_TO_VCR_MODE["refresh"],
            cassette_path=str(cassette_path),
            ignore_hosts=[],
        )

        # Capture atexit registrations so we can verify the cassette was
        # written BEFORE we ever ran them — the eager-save patch is the
        # only path that could have produced the file.
        registered: list = []

        def _capture(fn, *a, **kw):
            registered.append((fn, a, kw))

        import unittest.mock as _mock

        with _mock.patch.object(_atexit_module, "register", _capture):
            ns: dict = {}
            try:
                exec(compile(source, "<bootstrap>", "exec"), ns, ns)
                urllib.request.urlopen(f"http://127.0.0.1:{port}/").read()
            finally:
                server.shutdown()
                thread.join(timeout=2)

            # Cassette must already exist on disk — atexit hooks have not fired.
            assert cassette_path.exists(), (
                "cassette was not eagerly saved; forceful kernel kill would lose interactions"
            )
            body = cassette_path.read_text(encoding="utf-8")
            assert "interactions" in body
            assert "127.0.0.1" in body

    def test_bootstrap_eager_save_survives_force_exit(self, tmp_path):
        """A subprocess killed via os._exit (no atexit) must leave the cassette on disk."""
        pytest.importorskip("vcr")
        import subprocess
        import sys

        cassette_path = tmp_path / "force_exit.http-cassette.yaml"

        # Set up a tiny HTTP server inside the subprocess so the test does not
        # depend on external networking, and `os._exit(1)` after recording so
        # neither vcrpy's __exit__ nor the bootstrap's atexit hook can run.
        script = f"""
import os, socket, sys, threading, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

class _H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'hi')
    def log_message(self, *a, **kw):
        pass

s = socket.socket(); s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]; s.close()
srv = HTTPServer(('127.0.0.1', port), _H)
threading.Thread(target=srv.serve_forever, daemon=True).start()

from clm.workers.notebook.notebook_processor import (
    _HTTP_REPLAY_BOOTSTRAP_TEMPLATE, _HTTP_REPLAY_MODE_TO_VCR_MODE,
)
source = _HTTP_REPLAY_BOOTSTRAP_TEMPLATE.format(
    record_mode=_HTTP_REPLAY_MODE_TO_VCR_MODE['refresh'],
    cassette_path=r'''{cassette_path}''',
            ignore_hosts=[],
)
ns = {{}}
exec(compile(source, '<bootstrap>', 'exec'), ns, ns)
urllib.request.urlopen(f'http://127.0.0.1:{{port}}/').read()
# Force-exit so neither __exit__ nor atexit can write the cassette.
# If the cassette is on disk after this, the eager-save patch did it.
os._exit(0)
"""

        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"subprocess failed: stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert cassette_path.exists(), (
            "cassette missing after os._exit — eager-save patch did not run; "
            "forceful kernel kills would discard every recording"
        )
        body = cassette_path.read_text(encoding="utf-8")
        assert "interactions" in body
        assert "127.0.0.1" in body

    def test_bootstrap_replays_deduped_cassette_multiple_times(self, tmp_path):
        """A deduped canonical cassette (one entry per fingerprint) must
        still replay successfully when the deck issues the same request
        N times.

        The merge step in
        :func:`clm.workers.notebook.http_replay_cassette.merge_staging_into_canonical`
        deduplicates by ``(method, uri, body)``, so for a deck like
        ``slides_010v_requests_get.py`` (which calls ``get_post(1)``
        three times in a single workshop cell) the canonical cassette
        ends up with exactly one stored interaction. Under strict
        ``record_mode='none'`` playback, vcrpy normally consumes each
        cassette entry exactly once — so the second identical request
        would raise ``CannotOverwriteExistingCassetteException`` even
        though every matcher (method, scheme, host, port, path, query,
        body) succeeds.

        The bootstrap must opt into ``allow_playback_repeats=True`` to
        keep this scenario working. Regression for issue #95 (A).
        """
        pytest.importorskip("vcr")
        import urllib.request

        from clm.workers.notebook.notebook_processor import (
            _HTTP_REPLAY_BOOTSTRAP_TEMPLATE,
            _HTTP_REPLAY_MODE_TO_VCR_MODE,
        )

        # Pre-build a single-interaction cassette pinned to a fixed URL
        # — no live server needed. The body is the canonical YAML shape
        # vcrpy expects.
        cassette_path = tmp_path / "replay.http-cassette.yaml"
        cassette_path.write_text(
            "interactions:\n"
            "- request:\n"
            "    method: GET\n"
            "    uri: http://test.invalid/echo\n"
            "    headers: {}\n"
            "    body: null\n"
            "  response:\n"
            "    status: {code: 200, message: OK}\n"
            "    headers:\n"
            "      content-type: [text/plain]\n"
            "    body: {string: hello}\n"
            "version: 1\n",
            encoding="utf-8",
        )

        source = _HTTP_REPLAY_BOOTSTRAP_TEMPLATE.format(
            record_mode=_HTTP_REPLAY_MODE_TO_VCR_MODE["replay"],
            cassette_path=str(cassette_path),
            ignore_hosts=[],
        )

        ns: dict = {}
        exec(compile(source, "<bootstrap>", "exec"), ns, ns)

        # The same request three times. Without ``allow_playback_repeats``,
        # the second call would raise ``CannotOverwriteExistingCassetteException``
        # under ``record_mode='none'``.
        for _ in range(3):
            with urllib.request.urlopen("http://test.invalid/echo") as resp:
                assert resp.read() == b"hello"

    def test_bootstrap_scopes_vcr_force_reset_to_urllib3(self, tmp_path):
        """``vcr.patch.reset_patchers`` must skip the httpcore patchers.

        vcrpy's urllib3 stub opens ``vcr.patch.force_reset()`` on every
        urllib3 connection setup; ``force_reset()`` un-patches every
        stub including httpcore. When a foreground thread makes an
        httpcore call while a background thread is in that window, the
        call resolves to the original (unpatched) httpcore handler and
        bypasses vcr entirely -- silently losing cassette entries.

        The bootstrap replaces ``vcr.patch.reset_patchers`` with a
        filtered generator that yields all the patchers except the
        httpcore ones (the recursion guard ``force_reset()`` exists for
        only cares about urllib3). This test verifies the swap happened
        and that the urllib3 recursion guard is still intact.

        Regression for clm#129. Remove this test once vcrpy ships a
        scoped ``force_reset`` upstream and the bootstrap patch is
        retired.
        """
        pytest.importorskip("vcr")
        pytest.importorskip("httpcore")
        import importlib

        import httpcore
        import urllib3.connection
        import urllib3.connectionpool
        import vcr.patch

        from clm.workers.notebook.notebook_processor import (
            _HTTP_REPLAY_BOOTSTRAP_TEMPLATE,
            _HTTP_REPLAY_MODE_TO_VCR_MODE,
        )

        # Other tests in the same process may have already exec'd the
        # bootstrap and installed our scoped reset_patchers. Reload to
        # restore the upstream original so we can observe what the
        # bootstrap actually changes.
        importlib.reload(vcr.patch)
        original_reset_patchers = vcr.patch.reset_patchers
        original_targets = {(p.getter(), p.attribute) for p in original_reset_patchers()}
        # Sanity: upstream definition yields the httpcore patchers we're
        # going to strip. If this fails the upstream behavior changed and
        # our patch may no longer be needed.
        assert (httpcore.ConnectionPool, "handle_request") in original_targets
        assert (
            httpcore.AsyncConnectionPool,
            "handle_async_request",
        ) in original_targets

        try:
            source = _HTTP_REPLAY_BOOTSTRAP_TEMPLATE.format(
                record_mode=_HTTP_REPLAY_MODE_TO_VCR_MODE["replay"],
                cassette_path=str(tmp_path / "scope.http-cassette.yaml"),
                ignore_hosts=[],
            )
            # Exec only the patch prefix -- no need to open a cassette
            # just to inspect the swapped reset_patchers.
            prefix, _, _ = source.partition("class _ClmDeepCopyPersister")
            ns: dict = {}
            exec(compile(prefix, "<bootstrap-prefix>", "exec"), ns, ns)

            # The bootstrap must have replaced reset_patchers.
            assert vcr.patch.reset_patchers is not original_reset_patchers
            assert getattr(vcr.patch.reset_patchers, "_clm_scoped", False)

            patchers = list(vcr.patch.reset_patchers())
            targets = {(p.getter(), p.attribute) for p in patchers}

            # httpcore patchers must NOT be present -- that's the bug we're fixing.
            assert (httpcore.ConnectionPool, "handle_request") not in targets
            assert (
                httpcore.AsyncConnectionPool,
                "handle_async_request",
            ) not in targets

            # urllib3 patchers MUST still be present -- they're the recursion
            # guard force_reset() actually needs.
            assert (urllib3.connection, "HTTPConnection") in targets
            assert (urllib3.connection, "HTTPSConnection") in targets
            assert (
                urllib3.connectionpool.HTTPConnectionPool,
                "ConnectionCls",
            ) in targets
            assert (
                urllib3.connectionpool.HTTPSConnectionPool,
                "ConnectionCls",
            ) in targets
        finally:
            # Re-install scoped wrapper for any subsequent tests in this
            # worker, so they get the same environment as the kernel.
            ns2: dict = {}
            exec(compile(prefix, "<bootstrap-prefix>", "exec"), ns2, ns2)

    def test_bootstrap_force_reset_does_not_strip_httpcore_patch(self, tmp_path):
        """Holding ``force_reset()`` open must leave httpcore patched.

        End-to-end behavior check that complements the unit test above:
        open a real cassette, then enter ``vcr.patch.force_reset()`` and
        confirm ``httpcore.ConnectionPool.handle_request`` is *still*
        vcr's wrapper (not the original handler).

        Regression for clm#129. The unscoped upstream ``force_reset()``
        restores the original handler here, which is exactly what lets
        concurrent foreground httpcore calls escape the cassette.
        """
        pytest.importorskip("vcr")
        pytest.importorskip("httpcore")
        import atexit as _atexit_module
        import importlib
        import unittest.mock as _mock

        import httpcore
        import vcr.patch

        from clm.workers.notebook.notebook_processor import (
            _HTTP_REPLAY_BOOTSTRAP_TEMPLATE,
            _HTTP_REPLAY_MODE_TO_VCR_MODE,
        )

        # Drop any scoped wrapper a previous test in this worker may have
        # installed so we observe the bootstrap's actual effect.
        importlib.reload(vcr.patch)

        cassette_path = tmp_path / "force_reset.http-cassette.yaml"
        source = _HTTP_REPLAY_BOOTSTRAP_TEMPLATE.format(
            record_mode=_HTTP_REPLAY_MODE_TO_VCR_MODE["refresh"],
            cassette_path=str(cassette_path),
            ignore_hosts=[],
        )
        # Suppress the bootstrap's atexit registration so the cassette
        # context exits cleanly at the end of the test rather than at
        # interpreter shutdown (which would fire after `__cassette` is
        # already nulled and log a confusing AttributeError).
        ns: dict = {}
        with _mock.patch.object(_atexit_module, "register", lambda *a, **kw: None):
            exec(compile(source, "<bootstrap>", "exec"), ns, ns)
        try:
            # Outside force_reset(), httpcore is patched by vcr.
            patched_handler = httpcore.ConnectionPool.handle_request
            import vcr.patch

            with vcr.patch.force_reset():
                # With the scoped patch, force_reset() must leave the
                # httpcore wrapper intact. Without the patch, it would
                # be restored to vcr.patch._HttpcoreConnectionPool_handle_request.
                assert httpcore.ConnectionPool.handle_request is patched_handler, (
                    "force_reset() restored the original httpcore handler; "
                    "scoped patch is not in effect (clm#129)"
                )
        finally:
            ns["_clm_ctx"].__exit__(None, None, None)


# ============================================================================
# skip_evaluation honors topic ``evaluate="no"``
# ============================================================================


class TestSkipEvaluation:
    """``payload.skip_evaluation=True`` must bypass kernel and cache reuse.

    The opt-in propagates from the ``evaluate="no"`` topic spec attribute.
    A topic that opts out should still produce HTML/notebook/code output
    but never spawn a kernel and never consult the executed-notebook cache.
    """

    @pytest.mark.asyncio
    async def test_html_skips_kernel_and_cache_reuse(self):
        """Completed HTML normally executes — with skip_evaluation it must not."""
        cells = [
            make_cell("markdown", "# Title"),
            make_cell("code", "x = 1"),
        ]
        notebook_json = make_notebook_json(cells)

        spec = CompletedOutput(format="html", language="en")
        cache = MagicMock()
        cache.get = MagicMock()  # would be consulted on a normal run
        processor = NotebookProcessor(spec, cache=cache)
        payload = make_payload(notebook_json, format_="html", kind="completed").model_copy(
            update={"skip_evaluation": True}
        )

        with (
            patch("clm.workers.notebook.notebook_processor.TrackingExecutePreprocessor") as MockEP,
            patch("clm.workers.notebook.notebook_processor.HTMLExporter") as MockExporter,
        ):
            mock_exporter = MagicMock()
            mock_exporter.from_notebook_node.return_value = ("<html>ok</html>", {})
            MockExporter.return_value = mock_exporter

            result = await processor.process_notebook(payload)

        # No kernel was constructed — execution skipped entirely.
        MockEP.assert_not_called()
        # No cache lookup for cached executed notebooks.
        cache.get.assert_not_called()
        # HTML still produced.
        assert "<html>" in result

    @pytest.mark.asyncio
    async def test_recording_does_not_populate_cache(self):
        """Recording HTML normally caches its executed notebook; skip_evaluation must suppress that."""
        cells = [make_cell("code", "x = 1")]
        notebook_json = make_notebook_json(cells)

        spec = SpeakerOutput(format="html", language="en")
        cache = MagicMock()
        processor = NotebookProcessor(spec, cache=cache)
        payload = make_payload(notebook_json, format_="html", kind="speaker").model_copy(
            update={"skip_evaluation": True}
        )

        with (
            patch("clm.workers.notebook.notebook_processor.TrackingExecutePreprocessor") as MockEP,
            patch("clm.workers.notebook.notebook_processor.HTMLExporter") as MockExporter,
        ):
            mock_exporter = MagicMock()
            mock_exporter.from_notebook_node.return_value = ("<html>ok</html>", {})
            MockExporter.return_value = mock_exporter

            await processor.process_notebook(payload)

        # Neither executed nor cached.
        MockEP.assert_not_called()
        cache.store.assert_not_called()

    @pytest.mark.asyncio
    async def test_notebook_format_unaffected(self):
        """Notebook format already skips execution; skip_evaluation is a no-op there."""
        cells = [make_cell("code", "x = 1")]
        notebook_json = make_notebook_json(cells)

        spec = CompletedOutput(format="notebook", language="en")
        processor = NotebookProcessor(spec)
        payload = make_payload(notebook_json, format_="notebook", kind="completed").model_copy(
            update={"skip_evaluation": True}
        )

        with patch("clm.workers.notebook.notebook_processor.TrackingExecutePreprocessor") as MockEP:
            result = await processor.process_notebook(payload)

        MockEP.assert_not_called()
        # Notebook output is non-empty JSON.
        assert result and result.strip()


class TestEffectiveCellTimeout:
    """Per-cell timeout resolution (issue #143 defense-in-depth, Option F).

    An explicit CLM_CELL_TIMEOUT_SECONDS always wins; otherwise replay-engaged
    jobs get a generous default so a replay-layer hang fails as a clean
    CellTimeoutError instead of stalling to the job timeout, while non-replay
    builds keep the historical no-timeout behavior.
    """

    def _payload(self, mode):
        p = make_payload("", kind="speaker", format_="html")
        if mode is not None:
            p = p.model_copy(update={"http_replay_mode": mode})
        return p

    def test_disabled_mode_has_no_timeout(self):
        with patch.object(notebook_processor_module, "CELL_EXECUTION_TIMEOUT", None):
            assert _effective_cell_timeout(self._payload("disabled")) is None

    def test_no_mode_has_no_timeout(self):
        with patch.object(notebook_processor_module, "CELL_EXECUTION_TIMEOUT", None):
            assert _effective_cell_timeout(self._payload(None)) is None

    @pytest.mark.parametrize("mode", ["once", "replay", "new-episodes", "refresh"])
    def test_replay_modes_get_the_default(self, mode):
        with patch.object(notebook_processor_module, "CELL_EXECUTION_TIMEOUT", None):
            assert (
                _effective_cell_timeout(self._payload(mode))
                == notebook_processor_module._HTTP_REPLAY_DEFAULT_CELL_TIMEOUT
            )

    def test_explicit_env_timeout_wins_even_for_replay(self):
        with patch.object(notebook_processor_module, "CELL_EXECUTION_TIMEOUT", 42):
            assert _effective_cell_timeout(self._payload("once")) == 42

    def test_explicit_env_timeout_applies_to_disabled_too(self):
        with patch.object(notebook_processor_module, "CELL_EXECUTION_TIMEOUT", 42):
            assert _effective_cell_timeout(self._payload("disabled")) == 42

    def test_replay_default_can_be_opted_out(self):
        with (
            patch.object(notebook_processor_module, "CELL_EXECUTION_TIMEOUT", None),
            patch.object(notebook_processor_module, "_HTTP_REPLAY_DEFAULT_CELL_TIMEOUT", None),
        ):
            assert _effective_cell_timeout(self._payload("once")) is None
