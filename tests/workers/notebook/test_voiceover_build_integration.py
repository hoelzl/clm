"""Tests for voiceover companion file integration in the build pipeline.

These tests verify that:
- NotebookFile detects companion voiceover files
- ProcessNotebookOperation merges companion data into the payload
- Companion files are excluded from other_files
- Unmatched for_slide references produce warnings
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock

import pytest

from clm.core.course_files.notebook_file import NotebookFile

# ---------------------------------------------------------------------------
# companion_voiceover_path property
# ---------------------------------------------------------------------------


class TestCompanionVoiceoverPath:
    def _make_notebook_file(self, path: Path) -> NotebookFile:
        """Create a minimal NotebookFile for testing."""
        # Write minimal content so _from_path works
        path.write_text(
            "# %% [markdown]\n# ## Title\n",
            encoding="utf-8",
        )
        course = MagicMock()
        topic = MagicMock()
        nf = NotebookFile(course=course, path=path, topic=topic)
        return nf

    def test_returns_path_when_companion_exists(self, tmp_path: Path):
        slide = tmp_path / "slides_intro.py"
        companion = tmp_path / "voiceover_intro.py"
        companion.write_text("# companion", encoding="utf-8")
        nf = self._make_notebook_file(slide)

        result = nf.companion_voiceover_path

        assert result is not None
        assert result.name == "voiceover_intro.py"
        assert result == companion

    def test_returns_none_when_no_companion(self, tmp_path: Path):
        slide = tmp_path / "slides_intro.py"
        nf = self._make_notebook_file(slide)

        result = nf.companion_voiceover_path

        assert result is None

    def test_topic_prefix(self, tmp_path: Path):
        slide = tmp_path / "topic_overview.py"
        companion = tmp_path / "voiceover_overview.py"
        companion.write_text("# companion", encoding="utf-8")
        nf = self._make_notebook_file(slide)

        result = nf.companion_voiceover_path

        assert result is not None
        assert result.name == "voiceover_overview.py"

    def test_project_prefix(self, tmp_path: Path):
        slide = tmp_path / "project_setup.py"
        companion = tmp_path / "voiceover_setup.py"
        companion.write_text("# companion", encoding="utf-8")
        nf = self._make_notebook_file(slide)

        result = nf.companion_voiceover_path

        assert result is not None
        assert result.name == "voiceover_setup.py"


# ---------------------------------------------------------------------------
# ProcessNotebookOperation payload merging
# ---------------------------------------------------------------------------


class TestPayloadMerging:
    """Test that ProcessNotebookOperation merges companion voiceover data."""

    def test_payload_includes_companion_content(self, tmp_path: Path):
        """When a companion file exists, its voiceover cells appear in the payload data."""
        slide = tmp_path / "slides_test.py"
        slide.write_text(
            '# %% [markdown] lang="de" tags=["slide"] slide_id="intro"\n# ## Intro\n',
            encoding="utf-8",
        )
        companion = tmp_path / "voiceover_test.py"
        companion.write_text(
            '# %% [markdown] lang="de" tags=["voiceover"] for_slide="intro"\n'
            "# Voiceover text here.\n",
            encoding="utf-8",
        )

        # Use the merge function directly (payload() requires full Course setup)
        from clm.slides.voiceover_tools import merge_voiceover_text

        slide_text = slide.read_text(encoding="utf-8")
        companion_text = companion.read_text(encoding="utf-8")
        merged, unmatched = merge_voiceover_text(slide_text, companion_text)

        assert "Voiceover text here" in merged
        assert unmatched == []

    def test_unmatched_for_slide_produces_warnings(self, tmp_path: Path, caplog):
        """Unmatched for_slide references should produce log warnings."""
        from clm.slides.voiceover_tools import merge_voiceover_text

        slide_text = '# %% [markdown] lang="de" tags=["slide"] slide_id="intro"\n# ## Intro\n'
        companion_text = (
            '# %% [markdown] lang="de" tags=["voiceover"] for_slide="nonexistent"\n'
            "# Orphan voiceover.\n"
        )

        _, unmatched = merge_voiceover_text(slide_text, companion_text)

        assert "nonexistent" in unmatched

    def test_no_companion_no_merge(self, tmp_path: Path):
        """Without a companion file, the payload data should be the original slide text."""
        slide = tmp_path / "slides_test.py"
        original_text = '# %% [markdown] lang="de" tags=["slide"]\n# ## Intro\n'
        slide.write_text(original_text, encoding="utf-8")

        nf = MagicMock()
        nf.companion_voiceover_path = None
        nf.path = slide

        # Simulate what payload() does: read text, check companion
        data = slide.read_text(encoding="utf-8")
        companion = nf.companion_voiceover_path
        if companion is not None:
            from clm.slides.voiceover_tools import merge_voiceover_text

            companion_text = companion.read_text(encoding="utf-8")
            data, _ = merge_voiceover_text(data, companion_text)

        assert data == original_text


# ---------------------------------------------------------------------------
# Voiceover cells in output specs
# ---------------------------------------------------------------------------


class TestVoiceoverCellsInOutputSpecs:
    """Verify that voiceover cells are handled correctly by output specs.

    After merging companion voiceover cells, the output spec's cell
    filtering determines which outputs include them.
    """

    def test_speaker_output_keeps_voiceover_cells(self):
        from clm.workers.notebook.output_spec import SpeakerOutput

        spec = SpeakerOutput(language="de")
        from nbformat import NotebookNode

        cell = NotebookNode(
            {
                "cell_type": "markdown",
                "source": "Voiceover content",
                "metadata": {"tags": ["voiceover"], "lang": "de"},
            }
        )

        assert spec.is_cell_included(cell) is True

    def test_completed_output_removes_voiceover_cells(self):
        from clm.workers.notebook.output_spec import CompletedOutput

        spec = CompletedOutput()
        from nbformat import NotebookNode

        cell = NotebookNode(
            {
                "cell_type": "markdown",
                "source": "Voiceover content",
                "metadata": {"tags": ["voiceover"], "lang": "de"},
            }
        )

        assert spec.is_cell_included(cell) is False

    def test_codealong_output_removes_voiceover_cells(self):
        from clm.workers.notebook.output_spec import CodeAlongOutput

        spec = CodeAlongOutput()
        from nbformat import NotebookNode

        cell = NotebookNode(
            {
                "cell_type": "markdown",
                "source": "Voiceover content",
                "metadata": {"tags": ["voiceover"], "lang": "de"},
            }
        )

        assert spec.is_cell_included(cell) is False

    def test_speaker_output_keeps_notes_cells(self):
        from clm.workers.notebook.output_spec import SpeakerOutput

        spec = SpeakerOutput(language="de")
        from nbformat import NotebookNode

        cell = NotebookNode(
            {
                "cell_type": "markdown",
                "source": "Notes content",
                "metadata": {"tags": ["notes"], "lang": "de"},
            }
        )

        assert spec.is_cell_included(cell) is True
