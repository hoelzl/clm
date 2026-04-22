"""Tests for voiceover extraction and inlining."""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.slides.voiceover_tools import (
    companion_path,
    extract_voiceover,
    inline_voiceover,
    merge_voiceover_text,
    read_companion_baselines,
    render_companion_update,
    update_companion_narrative,
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


# ---------------------------------------------------------------------------
# merge_voiceover_text — in-memory merge for build pipeline
# ---------------------------------------------------------------------------


SLIDE_WITH_IDS = """\
# %% [markdown] lang="de" tags=["slide"] slide_id="thema-eins"
# ## Thema Eins
#
# Inhalt.

# %% [markdown] lang="en" tags=["slide"] slide_id="thema-eins"
# ## Topic One
#
# Content.

# %% tags=["keep"] slide_id="code-eins"
x = 1

# %% [markdown] lang="de" tags=["subslide"] slide_id="thema-zwei"
# ## Thema Zwei

# %% [markdown] lang="en" tags=["subslide"] slide_id="thema-zwei"
# ## Topic Two
"""

COMPANION_WITH_FOR_SLIDE = """\
# %% [markdown] lang="de" tags=["voiceover"] for_slide="thema-eins"
# Voiceover DE für Thema Eins.

# %% [markdown] lang="en" tags=["voiceover"] for_slide="thema-eins"
# Voiceover EN for Topic One.

# %% [markdown] lang="de" tags=["notes"] for_slide="thema-zwei"
# Notizen für Thema Zwei.

# %% [markdown] lang="en" tags=["notes"] for_slide="thema-zwei"
# Notes for Topic Two.
"""


class TestMergeVoiceoverText:
    def test_merges_voiceover_cells_into_correct_positions(self):
        merged, unmatched = merge_voiceover_text(SLIDE_WITH_IDS, COMPANION_WITH_FOR_SLIDE)

        assert unmatched == []
        # Voiceover cells should appear in the merged text
        assert "Voiceover DE" in merged
        assert "Voiceover EN" in merged
        assert "Notizen" in merged
        assert "Notes for Topic Two" in merged

    def test_voiceover_after_owning_slide(self):
        merged, _ = merge_voiceover_text(SLIDE_WITH_IDS, COMPANION_WITH_FOR_SLIDE)
        lines = merged.split("\n")

        # Find positions of key content
        de_slide_idx = next(
            i for i, line in enumerate(lines) if "Thema Eins" in line and "slide_id" not in line
        )
        de_vo_idx = next(i for i, line in enumerate(lines) if "Voiceover DE" in line)
        en_slide_idx = next(
            i for i, line in enumerate(lines) if "Topic One" in line and "slide_id" not in line
        )
        en_vo_idx = next(i for i, line in enumerate(lines) if "Voiceover EN" in line)

        # DE voiceover should be after DE slide but before EN slide
        assert de_slide_idx < de_vo_idx < en_slide_idx
        # EN voiceover should be after EN slide
        assert en_slide_idx < en_vo_idx

    def test_empty_companion_returns_original(self):
        merged, unmatched = merge_voiceover_text(SLIDE_WITH_IDS, "")

        assert merged == SLIDE_WITH_IDS
        assert unmatched == []

    def test_no_companion_cells_returns_original(self):
        merged, unmatched = merge_voiceover_text(SLIDE_WITH_IDS, "# just a comment\n")

        assert merged == SLIDE_WITH_IDS
        assert unmatched == []

    def test_unmatched_for_slide_reported(self):
        companion = """\
# %% [markdown] lang="de" tags=["voiceover"] for_slide="nonexistent"
# This won't match.
"""
        merged, unmatched = merge_voiceover_text(SLIDE_WITH_IDS, companion)

        assert unmatched == ["nonexistent"]

    def test_missing_for_slide_reported(self):
        companion = """\
# %% [markdown] lang="de" tags=["voiceover"]
# No for_slide attribute.
"""
        merged, unmatched = merge_voiceover_text(SLIDE_WITH_IDS, companion)

        assert unmatched == ["<no for_slide>"]

    def test_does_not_modify_inputs(self):
        slide_copy = SLIDE_WITH_IDS
        companion_copy = COMPANION_WITH_FOR_SLIDE

        merge_voiceover_text(slide_copy, companion_copy)

        # Original strings should be unchanged (they're immutable,
        # but verify no mutation of the slide text reference)
        assert slide_copy == SLIDE_WITH_IDS
        assert companion_copy == COMPANION_WITH_FOR_SLIDE

    def test_slide_without_ids_returns_unmatched(self):
        slide = """\
# %% [markdown] lang="de" tags=["slide"]
# ## No slide_id here
"""
        companion = """\
# %% [markdown] lang="de" tags=["voiceover"] for_slide="some-id"
# Voiceover.
"""
        merged, unmatched = merge_voiceover_text(slide, companion)

        assert unmatched == ["some-id"]


# ---------------------------------------------------------------------------
# read_companion_baselines
# ---------------------------------------------------------------------------


COMPANION_BILINGUAL = """\
# %% [markdown] lang="de" tags=["voiceover"] for_slide="intro"
# Deutsche Stimme eins.
# - Zweiter Punkt.

# %% [markdown] lang="en" tags=["voiceover"] for_slide="intro"
# English voice one.

# %% [markdown] lang="de" tags=["voiceover"] for_slide="details"
# Deutsche Details.

# %% [markdown] lang="de" tags=["notes"] for_slide="intro"
# Ein Notizblock.
"""


class TestReadCompanionBaselines:
    def test_returns_empty_for_missing_file(self, tmp_path: Path):
        comp = tmp_path / "voiceover_missing.py"
        assert read_companion_baselines(comp, "de") == {}

    def test_reads_voiceover_cells_by_slide_id(self, tmp_path: Path):
        comp = tmp_path / "voiceover_test.py"
        comp.write_text(COMPANION_BILINGUAL, encoding="utf-8")

        de_baselines = read_companion_baselines(comp, "de")

        assert set(de_baselines.keys()) == {"intro", "details"}
        assert "Stimme eins" in de_baselines["intro"]
        assert "Zweiter Punkt" in de_baselines["intro"]
        assert "Details" in de_baselines["details"]

    def test_filters_by_language(self, tmp_path: Path):
        comp = tmp_path / "voiceover_test.py"
        comp.write_text(COMPANION_BILINGUAL, encoding="utf-8")

        en_baselines = read_companion_baselines(comp, "en")

        assert set(en_baselines.keys()) == {"intro"}
        assert "English voice one" in en_baselines["intro"]

    def test_filters_by_tag(self, tmp_path: Path):
        comp = tmp_path / "voiceover_test.py"
        comp.write_text(COMPANION_BILINGUAL, encoding="utf-8")

        notes_baselines = read_companion_baselines(comp, "de", tag="notes")

        assert set(notes_baselines.keys()) == {"intro"}
        assert "Notizblock" in notes_baselines["intro"]

    def test_skips_cells_without_for_slide(self, tmp_path: Path):
        comp = tmp_path / "voiceover_test.py"
        comp.write_text(
            """\
# %% [markdown] lang="de" tags=["voiceover"]
# Ohne for_slide.

# %% [markdown] lang="de" tags=["voiceover"] for_slide="ok"
# Mit for_slide.
""",
            encoding="utf-8",
        )

        result = read_companion_baselines(comp, "de")

        assert set(result.keys()) == {"ok"}


# ---------------------------------------------------------------------------
# render_companion_update / update_companion_narrative
# ---------------------------------------------------------------------------


class TestRenderCompanionUpdate:
    def test_empty_input_returns_unchanged(self):
        text = '# %% [markdown] lang="de" tags=["voiceover"] for_slide="a"\n# Hi.\n'
        assert render_companion_update(text, {}, "de") == text

    def test_replaces_existing_cell_body(self):
        original = (
            '# %% [markdown] lang="de" tags=["voiceover"] for_slide="intro"\n# Alte Version.\n'
        )
        updated = render_companion_update(original, {"intro": "Neue Version"}, "de")

        assert 'for_slide="intro"' in updated
        assert "Neue Version" in updated
        assert "Alte Version" not in updated

    def test_appends_cell_for_unknown_slide_id(self):
        original = '# %% [markdown] lang="de" tags=["voiceover"] for_slide="intro"\n# Intro text.\n'
        updated = render_companion_update(original, {"details": "Detail text"}, "de")

        assert 'for_slide="intro"' in updated  # preserved
        assert 'for_slide="details"' in updated  # added
        assert "Intro text" in updated
        assert "Detail text" in updated

    def test_preserves_other_language_cells(self):
        original = (
            '# %% [markdown] lang="de" tags=["voiceover"] for_slide="intro"\n'
            "# Deutsch.\n"
            '\n# %% [markdown] lang="en" tags=["voiceover"] for_slide="intro"\n'
            "# English.\n"
        )
        updated = render_companion_update(original, {"intro": "Deutsch neu"}, "de")

        assert "English" in updated  # other-lang cell untouched
        assert "Deutsch neu" in updated
        assert "Deutsch.\n" not in updated  # old body replaced

    def test_inserts_into_empty_file(self):
        updated = render_companion_update("", {"intro": "Hello"}, "de")

        assert 'for_slide="intro"' in updated
        assert 'lang="de"' in updated
        assert "Hello" in updated


class TestUpdateCompanionNarrative:
    def test_writes_new_file_when_missing(self, tmp_path: Path):
        comp = tmp_path / "voiceover_new.py"

        update_companion_narrative(comp, {"a": "First bullet"}, "de")

        assert comp.exists()
        text = comp.read_text(encoding="utf-8")
        assert 'for_slide="a"' in text
        assert "First bullet" in text

    def test_updates_existing_file(self, tmp_path: Path):
        comp = tmp_path / "voiceover_existing.py"
        comp.write_text(
            '# %% [markdown] lang="de" tags=["voiceover"] for_slide="a"\n# Old.\n',
            encoding="utf-8",
        )

        update_companion_narrative(comp, {"a": "New"}, "de")

        text = comp.read_text(encoding="utf-8")
        assert "New" in text
        assert "Old" not in text

    def test_roundtrip_through_read_companion_baselines(self, tmp_path: Path):
        comp = tmp_path / "voiceover_rt.py"

        update_companion_narrative(
            comp,
            {"intro": "- Ein Punkt.\n- Zwei Punkte.", "details": "Detail."},
            "de",
        )
        baselines = read_companion_baselines(comp, "de")

        assert "Ein Punkt" in baselines["intro"]
        assert "Zwei Punkte" in baselines["intro"]
        assert "Detail" in baselines["details"]
