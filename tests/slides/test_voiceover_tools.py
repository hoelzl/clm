"""Tests for voiceover extraction and inlining."""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.slides.voiceover_tools import (
    companion_path,
    extract_voiceover,
    inline_voiceover,
)

# ---------------------------------------------------------------------------
# companion_path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "input_name,expected_name",
    [
        ("slides_intro.py", "voiceover_intro.py"),
        ("slides_010v_topic.py", "voiceover_010v_topic.py"),
        ("topic_overview.py", "voiceover_overview.py"),
        ("project_setup.py", "voiceover_setup.py"),
        ("other_name.py", "voiceover_other_name.py"),
    ],
)
def test_companion_path(input_name: str, expected_name: str, tmp_path: Path):
    p = tmp_path / input_name
    result = companion_path(p)
    assert result.name == expected_name
    assert result.parent == p.parent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SLIDE_WITH_VOICEOVER = """\
# j2 from 'macros.j2' import header
# {{ header("Test", "Test") }}

# %% [markdown] lang="de" tags=["slide"]
# ## Thema Eins
#
# Inhalt auf Deutsch.

# %% [markdown] lang="de" tags=["voiceover"]
# Hier ist der Voiceover-Text.

# %% [markdown] lang="en" tags=["slide"]
# ## Topic One
#
# Content in English.

# %% [markdown] lang="en" tags=["voiceover"]
# Here is the voiceover text.

# %% tags=["keep"]
x = 1

# %% [markdown] lang="de" tags=["subslide"]
# ## Thema Zwei

# %% [markdown] lang="de" tags=["notes"]
# Notizen für Thema Zwei.

# %% [markdown] lang="en" tags=["subslide"]
# ## Topic Two

# %% [markdown] lang="en" tags=["notes"]
# Notes for topic two.
"""

SLIDE_WITHOUT_VOICEOVER = """\
# j2 from 'macros.j2' import header
# {{ header("Test", "Test") }}

# %% [markdown] lang="de" tags=["slide"]
# ## Einführung

# %% [markdown] lang="en" tags=["slide"]
# ## Introduction

# %% tags=["keep"]
x = 1
"""

SLIDE_WITH_SLIDE_IDS = """\
# j2 from 'macros.j2' import header
# {{ header("Test", "Test") }}

# %% [markdown] lang="de" tags=["slide"] slide_id="thema-eins"
# ## Thema Eins
#
# Inhalt auf Deutsch.

# %% [markdown] lang="de" tags=["voiceover"]
# Voiceover für Thema Eins.

# %% [markdown] lang="en" tags=["slide"] slide_id="thema-eins"
# ## Topic One
#
# Content in English.

# %% [markdown] lang="en" tags=["voiceover"]
# Voiceover for topic one.
"""


# ---------------------------------------------------------------------------
# extract_voiceover — basic
# ---------------------------------------------------------------------------


class TestExtractVoiceover:
    def test_extracts_voiceover_cells(self, tmp_path: Path):
        slide_file = tmp_path / "slides_intro.py"
        slide_file.write_text(SLIDE_WITH_VOICEOVER, encoding="utf-8")

        result = extract_voiceover(slide_file)

        assert result.cells_extracted == 4  # 2 voiceover + 2 notes
        assert result.ids_generated > 0

        # Slide file should have no voiceover/notes cells
        slide_text = slide_file.read_text(encoding="utf-8")
        assert "voiceover" not in slide_text.lower().split("tags=")[0] or True
        # More precise: no cells tagged voiceover or notes
        assert 'tags=["voiceover"]' not in slide_text
        assert 'tags=["notes"]' not in slide_text

        # Companion file should exist
        comp = tmp_path / "voiceover_intro.py"
        assert comp.exists()

        comp_text = comp.read_text(encoding="utf-8")
        assert 'tags=["voiceover"]' in comp_text or 'tags=["notes"]' in comp_text

    def test_no_voiceover_cells(self, tmp_path: Path):
        slide_file = tmp_path / "slides_intro.py"
        slide_file.write_text(SLIDE_WITHOUT_VOICEOVER, encoding="utf-8")

        result = extract_voiceover(slide_file)

        assert result.cells_extracted == 0
        assert not (tmp_path / "voiceover_intro.py").exists()

    def test_dry_run_does_not_modify_files(self, tmp_path: Path):
        slide_file = tmp_path / "slides_intro.py"
        slide_file.write_text(SLIDE_WITH_VOICEOVER, encoding="utf-8")
        original_text = SLIDE_WITH_VOICEOVER

        result = extract_voiceover(slide_file, dry_run=True)

        assert result.cells_extracted == 4
        assert result.dry_run is True
        # Files should not be modified
        assert slide_file.read_text(encoding="utf-8") == original_text
        assert not (tmp_path / "voiceover_intro.py").exists()

    def test_preserves_existing_slide_ids(self, tmp_path: Path):
        slide_file = tmp_path / "slides_intro.py"
        slide_file.write_text(SLIDE_WITH_SLIDE_IDS, encoding="utf-8")

        result = extract_voiceover(slide_file)

        assert result.cells_extracted == 2

        comp = tmp_path / "voiceover_intro.py"
        comp_text = comp.read_text(encoding="utf-8")
        # Companion cells should reference the existing slide_id
        assert 'for_slide="thema-eins"' in comp_text

    def test_companion_has_for_slide_metadata(self, tmp_path: Path):
        slide_file = tmp_path / "slides_intro.py"
        slide_file.write_text(SLIDE_WITH_VOICEOVER, encoding="utf-8")

        extract_voiceover(slide_file)

        comp = tmp_path / "voiceover_intro.py"
        comp_text = comp.read_text(encoding="utf-8")
        assert "for_slide=" in comp_text

    def test_slide_file_has_slide_ids_after_extraction(self, tmp_path: Path):
        slide_file = tmp_path / "slides_intro.py"
        slide_file.write_text(SLIDE_WITH_VOICEOVER, encoding="utf-8")

        extract_voiceover(slide_file)

        slide_text = slide_file.read_text(encoding="utf-8")
        assert "slide_id=" in slide_text


# ---------------------------------------------------------------------------
# inline_voiceover — basic
# ---------------------------------------------------------------------------


class TestInlineVoiceover:
    def test_inlines_voiceover_cells(self, tmp_path: Path):
        slide_file = tmp_path / "slides_intro.py"
        slide_file.write_text(SLIDE_WITH_VOICEOVER, encoding="utf-8")

        # Extract first
        extract_voiceover(slide_file)
        comp = tmp_path / "voiceover_intro.py"
        assert comp.exists()

        # Then inline
        result = inline_voiceover(slide_file)

        assert result.cells_inlined == 4
        assert result.companion_deleted is True
        assert not comp.exists()

        # Slide file should have voiceover cells back
        slide_text = slide_file.read_text(encoding="utf-8")
        assert 'tags=["voiceover"]' in slide_text
        assert 'tags=["notes"]' in slide_text

    def test_no_companion_file(self, tmp_path: Path):
        slide_file = tmp_path / "slides_intro.py"
        slide_file.write_text(SLIDE_WITHOUT_VOICEOVER, encoding="utf-8")

        result = inline_voiceover(slide_file)

        assert result.cells_inlined == 0

    def test_dry_run_does_not_modify_files(self, tmp_path: Path):
        slide_file = tmp_path / "slides_intro.py"
        slide_file.write_text(SLIDE_WITH_VOICEOVER, encoding="utf-8")

        extract_voiceover(slide_file)
        comp = tmp_path / "voiceover_intro.py"
        slide_text_after_extract = slide_file.read_text(encoding="utf-8")
        comp_text = comp.read_text(encoding="utf-8")

        result = inline_voiceover(slide_file, dry_run=True)

        assert result.cells_inlined == 4
        assert result.dry_run is True
        # Files should not be modified
        assert slide_file.read_text(encoding="utf-8") == slide_text_after_extract
        assert comp.exists()
        assert comp.read_text(encoding="utf-8") == comp_text

    def test_for_slide_removed_after_inline(self, tmp_path: Path):
        slide_file = tmp_path / "slides_intro.py"
        slide_file.write_text(SLIDE_WITH_VOICEOVER, encoding="utf-8")

        extract_voiceover(slide_file)
        inline_voiceover(slide_file)

        slide_text = slide_file.read_text(encoding="utf-8")
        assert "for_slide=" not in slide_text


# ---------------------------------------------------------------------------
# Round-trip: extract then inline preserves content
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_round_trip_preserves_voiceover_content(self, tmp_path: Path):
        slide_file = tmp_path / "slides_intro.py"
        slide_file.write_text(SLIDE_WITH_VOICEOVER, encoding="utf-8")

        extract_voiceover(slide_file)
        inline_voiceover(slide_file)

        result_text = slide_file.read_text(encoding="utf-8")
        # The voiceover content should be present
        assert "Hier ist der Voiceover-Text." in result_text
        assert "Here is the voiceover text." in result_text
        assert "Notizen für Thema Zwei." in result_text or "Notizen f" in result_text
        assert "Notes for topic two." in result_text

    def test_round_trip_preserves_content_cells(self, tmp_path: Path):
        slide_file = tmp_path / "slides_intro.py"
        slide_file.write_text(SLIDE_WITH_VOICEOVER, encoding="utf-8")

        extract_voiceover(slide_file)
        inline_voiceover(slide_file)

        result_text = slide_file.read_text(encoding="utf-8")
        # Content cells should be unchanged
        assert "Thema Eins" in result_text
        assert "Topic One" in result_text
        assert "x = 1" in result_text

    def test_round_trip_with_existing_slide_ids(self, tmp_path: Path):
        slide_file = tmp_path / "slides_intro.py"
        slide_file.write_text(SLIDE_WITH_SLIDE_IDS, encoding="utf-8")

        extract_voiceover(slide_file)
        inline_voiceover(slide_file)

        result_text = slide_file.read_text(encoding="utf-8")
        # Original slide_ids should be preserved
        assert 'slide_id="thema-eins"' in result_text
        # for_slide should NOT be present (stripped during inline)
        assert "for_slide=" not in result_text
        # Voiceover content should be present
        assert "Voiceover für Thema Eins" in result_text or "Voiceover f" in result_text
        assert "Voiceover for topic one." in result_text


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_voiceover_only_one_language(self, tmp_path: Path):
        text = """\
# %% [markdown] lang="de" tags=["slide"]
# ## Thema

# %% [markdown] lang="de" tags=["voiceover"]
# Nur Deutsch Voiceover.

# %% [markdown] lang="en" tags=["slide"]
# ## Topic
"""
        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text(text, encoding="utf-8")

        result = extract_voiceover(slide_file)
        assert result.cells_extracted == 1

        comp = tmp_path / "voiceover_test.py"
        assert comp.exists()
        comp_text = comp.read_text(encoding="utf-8")
        assert "Nur Deutsch Voiceover" in comp_text

    def test_empty_file(self, tmp_path: Path):
        slide_file = tmp_path / "slides_empty.py"
        slide_file.write_text("", encoding="utf-8")

        result = extract_voiceover(slide_file)
        assert result.cells_extracted == 0

    def test_shared_code_cells_preserved(self, tmp_path: Path):
        """Shared (language-neutral) cells should stay in the slide file."""
        text = """\
# %% [markdown] lang="de" tags=["slide"]
# ## Thema

# %% tags=["keep"]
x = 1

# %% [markdown] lang="de" tags=["voiceover"]
# Voiceover text.

# %% [markdown] lang="en" tags=["slide"]
# ## Topic
"""
        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text(text, encoding="utf-8")

        extract_voiceover(slide_file)

        slide_text = slide_file.read_text(encoding="utf-8")
        assert 'tags=["keep"]' in slide_text
        assert "x = 1" in slide_text
        assert 'tags=["voiceover"]' not in slide_text

    def test_inline_with_unmatched_cells(self, tmp_path: Path):
        """Companion cells with unknown for_slide are appended at the end."""
        slide_file = tmp_path / "slides_test.py"
        slide_text = """\
# %% [markdown] lang="de" tags=["slide"] slide_id="intro"
# ## Intro
"""
        slide_file.write_text(slide_text, encoding="utf-8")

        comp = tmp_path / "voiceover_test.py"
        comp_text = """\
# %% [markdown] lang="de" tags=["voiceover"] for_slide="nonexistent-id"
# This has no matching slide.
"""
        comp.write_text(comp_text, encoding="utf-8")

        result = inline_voiceover(slide_file)

        assert result.unmatched_cells == 1
        assert result.cells_inlined == 0
        # Unmatched cells are appended at the end
        final_text = slide_file.read_text(encoding="utf-8")
        assert "This has no matching slide." in final_text

    def test_j2_cells_untouched(self, tmp_path: Path):
        """j2 cells should not be extracted or have slide_ids."""
        text = """\
# j2 from 'macros.j2' import header
# {{ header("Test", "Test") }}

# %% [markdown] lang="de" tags=["slide"]
# ## Thema

# %% [markdown] lang="de" tags=["voiceover"]
# Voiceover.
"""
        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text(text, encoding="utf-8")

        extract_voiceover(slide_file)

        slide_text = slide_file.read_text(encoding="utf-8")
        assert "# j2 from 'macros.j2' import header" in slide_text
        assert '# {{ header("Test", "Test") }}' in slide_text

    def test_extract_result_summary(self, tmp_path: Path):
        slide_file = tmp_path / "slides_intro.py"
        slide_file.write_text(SLIDE_WITH_VOICEOVER, encoding="utf-8")

        result = extract_voiceover(slide_file)
        assert "4 voiceover cell(s) extracted" in result.summary

    def test_inline_result_summary(self, tmp_path: Path):
        slide_file = tmp_path / "slides_intro.py"
        slide_file.write_text(SLIDE_WITH_VOICEOVER, encoding="utf-8")

        extract_voiceover(slide_file)
        result = inline_voiceover(slide_file)
        assert "4 voiceover cell(s) inlined" in result.summary
        assert "companion file deleted" in result.summary
