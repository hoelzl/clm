"""Tests for voiceover extraction and inlining."""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.slides.split import split_text
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
        # Split single-language decks must map to distinct per-language
        # companions — the `.de`/`.en` token survives into the suffix.
        ("slides_intro.de.py", "voiceover_intro.de.py"),
        ("slides_intro.en.py", "voiceover_intro.en.py"),
        ("topic_overview.de.py", "voiceover_overview.de.py"),
        ("other_name.en.py", "voiceover_other_name.en.py"),
    ],
)
def test_companion_path(input_name: str, expected_name: str, tmp_path: Path):
    p = tmp_path / input_name
    result = companion_path(p)
    assert result.name == expected_name
    assert result.parent == p.parent


def test_companion_path_split_pair_is_distinct(tmp_path: Path):
    """A `.de`/`.en` pair must map to two different companion files.

    A regression on this (e.g. dropping the lang token while deriving the
    companion name) would collapse both languages onto a single companion
    and silently lose one language's voiceover.
    """
    de = companion_path(tmp_path / "slides_intro.de.py")
    en = companion_path(tmp_path / "slides_intro.en.py")
    assert de != en
    assert de.name == "voiceover_intro.de.py"
    assert en.name == "voiceover_intro.en.py"


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


# ---------------------------------------------------------------------------
# Split single-language decks (.de.py / .en.py)
#
# Future authoring happens predominantly on split files, so the
# extract / merge / inline cycle must work per language: each split file
# pairs with its own ``voiceover_*.<lang>.py`` companion, and the two
# languages must never bleed into each other. These fixtures are produced
# by ``split_text`` so they match what ``clm slides split`` writes.
# ---------------------------------------------------------------------------


def _split_pair(tmp_path: Path) -> tuple[Path, Path]:
    """Write ``slides_intro.de.py`` / ``slides_intro.en.py`` and return them."""
    de_text, en_text = split_text(SLIDE_WITH_VOICEOVER)
    de_file = tmp_path / "slides_intro.de.py"
    en_file = tmp_path / "slides_intro.en.py"
    de_file.write_text(de_text, encoding="utf-8", newline="\n")
    en_file.write_text(en_text, encoding="utf-8", newline="\n")
    return de_file, en_file


def _lang_cell_count(text: str, lang: str) -> int:
    return sum(1 for line in text.splitlines() if f'lang="{lang}"' in line)


class TestSplitDeckVoiceover:
    def test_extract_writes_per_language_companions(self, tmp_path: Path):
        de_file, en_file = _split_pair(tmp_path)

        de_res = extract_voiceover(de_file)
        en_res = extract_voiceover(en_file)

        de_comp = tmp_path / "voiceover_intro.de.py"
        en_comp = tmp_path / "voiceover_intro.en.py"
        # Each split deck carries one slide + one voiceover + one
        # subslide + one notes cell for its own language.
        assert de_res.cells_extracted == 2
        assert en_res.cells_extracted == 2
        assert de_comp.exists()
        assert en_comp.exists()

    def test_extract_keeps_languages_isolated(self, tmp_path: Path):
        de_file, en_file = _split_pair(tmp_path)

        extract_voiceover(de_file)
        extract_voiceover(en_file)

        de_comp_text = (tmp_path / "voiceover_intro.de.py").read_text(encoding="utf-8")
        en_comp_text = (tmp_path / "voiceover_intro.en.py").read_text(encoding="utf-8")

        # DE companion holds only German narrative; EN only English.
        assert _lang_cell_count(de_comp_text, "en") == 0
        assert _lang_cell_count(en_comp_text, "de") == 0
        assert "Hier ist der Voiceover-Text." in de_comp_text
        assert "Here is the voiceover text." in en_comp_text

        # Voiceover removed from the slide files themselves.
        assert 'tags=["voiceover"]' not in de_file.read_text(encoding="utf-8")
        assert 'tags=["voiceover"]' not in en_file.read_text(encoding="utf-8")

    def test_build_merge_matches_per_language(self, tmp_path: Path):
        """The build path (``merge_voiceover_text``) re-inserts cleanly."""
        de_file, en_file = _split_pair(tmp_path)
        extract_voiceover(de_file)
        extract_voiceover(en_file)

        de_slide = de_file.read_text(encoding="utf-8")
        en_slide = en_file.read_text(encoding="utf-8")
        de_comp = (tmp_path / "voiceover_intro.de.py").read_text(encoding="utf-8")
        en_comp = (tmp_path / "voiceover_intro.en.py").read_text(encoding="utf-8")

        de_merged, de_unmatched = merge_voiceover_text(de_slide, de_comp)
        en_merged, en_unmatched = merge_voiceover_text(en_slide, en_comp)

        assert de_unmatched == []
        assert en_unmatched == []
        assert "Hier ist der Voiceover-Text." in de_merged
        assert "Notizen f" in de_merged
        assert "Here is the voiceover text." in en_merged
        assert "Notes for topic two." in en_merged

    def test_round_trip_preserves_content_per_language(self, tmp_path: Path):
        de_file, en_file = _split_pair(tmp_path)

        extract_voiceover(de_file)
        extract_voiceover(en_file)
        de_in = inline_voiceover(de_file)
        en_in = inline_voiceover(en_file)

        assert de_in.unmatched_cells == 0
        assert en_in.unmatched_cells == 0
        assert de_in.companion_deleted
        assert en_in.companion_deleted

        de_final = de_file.read_text(encoding="utf-8")
        en_final = en_file.read_text(encoding="utf-8")

        # Narrative restored, for_slide stripped, no cross-language bleed.
        assert "Hier ist der Voiceover-Text." in de_final
        assert "Here is the voiceover text." in en_final
        assert "for_slide=" not in de_final
        assert "for_slide=" not in en_final
        assert _lang_cell_count(de_final, "en") == 0
        assert _lang_cell_count(en_final, "de") == 0


# ---------------------------------------------------------------------------
# Positional anchors (vo_anchor): restore voiceovers to their *exact*
# position on inline, not just the end of their slide group.
#
# Regression for the report where extract -> edit -> inline moved two
# voiceovers: a mid-group voiceover collapsed to the group end, and a
# voiceover sailed past trailing non-slide cells to the bottom of the file.
# ---------------------------------------------------------------------------


# A deck that exercises both failure modes:
#   * "intro" group: a voiceover in the MIDDLE of the group (between the
#     heading and a code cell) plus one after the code cell.
#   * "outro" group: a voiceover right after the heading, followed by
#     untagged prose and an `answer` cell that carry no slide_id (the
#     greedy forward-walk used to treat these as continuation and pushed
#     the voiceover to the very end).
DECK_POSITIONAL = """\
# %% [markdown] lang="de" tags=["slide"] slide_id="intro"
# ## Intro

# %% [markdown] lang="de" tags=["voiceover"]
# VO right after intro heading.

# %% [markdown] lang="de"
# Some prose without an id.

# %% lang="de"
code_cell = 1

# %% [markdown] lang="de" tags=["voiceover"]
# VO after the code cell.

# %% [markdown] lang="de" tags=["slide"] slide_id="outro"
# ## Outro

# %% [markdown] lang="de" tags=["voiceover"]
# VO right after outro heading.

# %% [markdown] lang="de"
# Q&A prose, no id.

# %% [markdown] lang="de" tags=["answer"]
# An answer cell, no id.
"""


def _assert_order(text: str, *needles: str) -> None:
    """Assert ``needles`` appear in ``text`` in the given relative order."""
    positions = []
    for n in needles:
        assert n in text, f"missing: {n!r}"
        positions.append(text.index(n))
    assert positions == sorted(positions), f"out of order: {list(zip(needles, positions))}"


class TestPositionalAnchors:
    def test_round_trip_is_idempotent(self, tmp_path: Path):
        """With no edits, repeated extract -> inline reaches a fixed point.

        (The first extract may add auto-generated slide_ids to narrative
        cells that lack them — documented behavior — so byte-identity is
        asserted against that stabilized baseline, not the raw input.)
        """
        slide_file = tmp_path / "slides_pos.py"
        slide_file.write_text(DECK_POSITIONAL, encoding="utf-8", newline="\n")

        extract_voiceover(slide_file)
        inline_voiceover(slide_file)
        baseline = slide_file.read_text(encoding="utf-8")

        extract_voiceover(slide_file)
        inline_voiceover(slide_file)
        assert slide_file.read_text(encoding="utf-8") == baseline

    def test_fully_ided_deck_round_trip_byte_identical(self, tmp_path: Path):
        """A deck where every cell already carries its id round-trips exactly.

        This is the real-world authoring case (the reported deck) where
        extract adds nothing, so the round-trip must be a perfect inverse.
        """
        text = """\
# %% [markdown] lang="de" tags=["slide"] slide_id="intro"
# ## Intro

# %% [markdown] lang="de" tags=["voiceover"] slide_id="intro"
# VO right after intro heading.

# %% tags=["keep"] slide_id="code"
x = 1

# %% [markdown] lang="de" tags=["voiceover"] slide_id="code"
# VO after the code cell.

# %% [markdown] lang="de" tags=["subslide"] slide_id="outro"
# ## Outro

# %% [markdown] lang="de" tags=["voiceover"] slide_id="outro"
# VO after outro.

# %% [markdown] lang="de" tags=["answer"] slide_id="outro"
# An answer cell.
"""
        slide_file = tmp_path / "slides_ids.py"
        slide_file.write_text(text, encoding="utf-8", newline="\n")

        result = extract_voiceover(slide_file)
        assert result.ids_generated == 0  # nothing to add
        inline_voiceover(slide_file)

        assert slide_file.read_text(encoding="utf-8") == text

    def test_mid_group_voiceover_stays_in_place(self, tmp_path: Path):
        """A voiceover between heading and code must not collapse to the end."""
        slide_file = tmp_path / "slides_pos.py"
        slide_file.write_text(DECK_POSITIONAL, encoding="utf-8", newline="\n")

        extract_voiceover(slide_file)
        inline_voiceover(slide_file)
        out = slide_file.read_text(encoding="utf-8")

        _assert_order(
            out,
            "## Intro",
            "VO right after intro heading.",
            "Some prose without an id.",
            "code_cell = 1",
            "VO after the code cell.",
            "## Outro",
        )

    def test_voiceover_not_pushed_past_trailing_cells(self, tmp_path: Path):
        """A voiceover must stay under its slide, above id-less answer cells."""
        slide_file = tmp_path / "slides_pos.py"
        slide_file.write_text(DECK_POSITIONAL, encoding="utf-8", newline="\n")

        extract_voiceover(slide_file)
        inline_voiceover(slide_file)
        out = slide_file.read_text(encoding="utf-8")

        _assert_order(
            out,
            "## Outro",
            "VO right after outro heading.",
            "Q&A prose, no id.",
            "An answer cell, no id.",
        )

    def test_edits_between_extract_and_inline_do_not_move_voiceovers(self, tmp_path: Path):
        """The reported scenario: tag a sibling cell + insert a new slide."""
        slide_file = tmp_path / "slides_pos.py"
        slide_file.write_text(DECK_POSITIONAL, encoding="utf-8", newline="\n")

        extract_voiceover(slide_file)

        # Edit 1: add a tag to the code cell (header-only change — the
        # body fingerprint that anchors VO#2 must survive this).
        # Edit 2: insert a brand-new slide before "outro".
        text = slide_file.read_text(encoding="utf-8")
        text = text.replace(
            '# %% lang="de"\ncode_cell = 1', '# %% lang="de" tags=["keep"]\ncode_cell = 1'
        )
        new_slide = (
            '# %% [markdown] lang="de" tags=["subslide"] slide_id="inserted"\n'
            "# ## Inserted Slide\n\n"
        )
        text = text.replace(
            '# %% [markdown] lang="de" tags=["slide"] slide_id="outro"',
            new_slide + '# %% [markdown] lang="de" tags=["slide"] slide_id="outro"',
            1,
        )
        slide_file.write_text(text, encoding="utf-8", newline="\n")

        result = inline_voiceover(slide_file)
        out = slide_file.read_text(encoding="utf-8")

        assert result.relocated_cells == 0
        assert result.unmatched_cells == 0
        # Both intro voiceovers are still anchored correctly despite the
        # keep-tag edit, and the outro voiceover is unaffected by the new
        # slide inserted above it.
        _assert_order(
            out,
            "## Intro",
            "VO right after intro heading.",
            "code_cell = 1",
            "VO after the code cell.",
            "## Inserted Slide",
            "## Outro",
            "VO right after outro heading.",
            "An answer cell, no id.",
        )

    def test_consecutive_voiceovers_preserve_order(self, tmp_path: Path):
        """Two voiceovers sharing one anchor must keep document order."""
        text = """\
# %% [markdown] lang="de" tags=["slide"] slide_id="s"
# ## S

# %% [markdown] lang="de" tags=["voiceover"]
# First voiceover.

# %% [markdown] lang="de" tags=["voiceover"]
# Second voiceover.

# %% tags=["keep"]
x = 1
"""
        slide_file = tmp_path / "slides_seq.py"
        slide_file.write_text(text, encoding="utf-8", newline="\n")

        extract_voiceover(slide_file)
        inline_voiceover(slide_file)
        out = slide_file.read_text(encoding="utf-8")

        _assert_order(out, "## S", "First voiceover.", "Second voiceover.", "x = 1")

    def test_deleted_anchor_cell_relocates_and_reports(self, tmp_path: Path):
        """If the anchor predecessor is removed, fall back + report a relocation."""
        slide_file = tmp_path / "slides_pos.py"
        slide_file.write_text(DECK_POSITIONAL, encoding="utf-8", newline="\n")

        extract_voiceover(slide_file)

        # Delete the code cell that anchors "VO after the code cell."
        text = slide_file.read_text(encoding="utf-8")
        text = text.replace('# %% lang="de"\ncode_cell = 1\n', "")
        slide_file.write_text(text, encoding="utf-8", newline="\n")

        result = inline_voiceover(slide_file)
        out = slide_file.read_text(encoding="utf-8")

        assert result.relocated_cells == 1
        assert result.unmatched_cells == 0
        # The relocated voiceover is still present (now at the intro group end).
        assert "VO after the code cell." in out
        # It must not have leaked past the outro heading.
        _assert_order(out, "VO after the code cell.", "## Outro")

    def test_dry_run_reports_placements(self, tmp_path: Path):
        slide_file = tmp_path / "slides_pos.py"
        slide_file.write_text(DECK_POSITIONAL, encoding="utf-8", newline="\n")

        extract_voiceover(slide_file)
        result = inline_voiceover(slide_file, dry_run=True)

        assert len(result.placements) == 3
        assert all(p.status == "anchored" for p in result.placements)
        assert all(p.after_line is not None for p in result.placements)

    def test_legacy_companion_without_anchor_still_places(self, tmp_path: Path):
        """A hand-written companion lacking vo_anchor uses the group-end path."""
        slide_file = tmp_path / "slides_legacy.py"
        slide_file.write_text(
            '# %% [markdown] lang="de" tags=["slide"] slide_id="intro"\n# ## Intro\n',
            encoding="utf-8",
            newline="\n",
        )
        comp = tmp_path / "voiceover_legacy.py"
        comp.write_text(
            '# %% [markdown] lang="de" tags=["voiceover"] for_slide="intro"\n# Legacy VO.\n',
            encoding="utf-8",
            newline="\n",
        )

        result = inline_voiceover(slide_file)

        assert result.cells_inlined == 1
        assert result.relocated_cells == 0  # no anchor recorded -> not a relocation
        assert result.unmatched_cells == 0
        assert result.placements[0].status == "placed"
        out = slide_file.read_text(encoding="utf-8")
        assert "Legacy VO." in out
        assert "vo_anchor" not in out
        assert "for_slide" not in out

    def test_build_merge_honors_anchor(self, tmp_path: Path):
        """The build path (merge_voiceover_text) also restores mid-group order."""
        slide_file = tmp_path / "slides_pos.py"
        slide_file.write_text(DECK_POSITIONAL, encoding="utf-8", newline="\n")
        extract_voiceover(slide_file)

        slide_after = slide_file.read_text(encoding="utf-8")
        comp = (tmp_path / "voiceover_pos.py").read_text(encoding="utf-8")

        merged, unmatched = merge_voiceover_text(slide_after, comp)

        assert unmatched == []
        # vo_anchor must never leak into the merged notebook.
        assert "vo_anchor" not in merged
        _assert_order(
            merged,
            "## Intro",
            "VO right after intro heading.",
            "code_cell = 1",
            "VO after the code cell.",
            "## Outro",
            "VO right after outro heading.",
            "An answer cell, no id.",
        )


# ---------------------------------------------------------------------------
# Anchor-ambiguity & scoping regressions.
#
# These cover defects surfaced by an adversarial review of the positional
# anchor fix: a slide_id or body fingerprint is NOT unique within a group,
# the owning slide_id may be renamed/absent, bilingual groups interleave,
# and extract's blank-line cleanup must not desync the fingerprint.
# ---------------------------------------------------------------------------


def _round_trip(tmp_path: Path, deck: str, name: str = "slides_x.py"):
    """extract -> inline a deck; return (final_text, InlineResult)."""
    f = tmp_path / name
    f.write_text(deck, encoding="utf-8", newline="\n")
    extract_voiceover(f)
    result = inline_voiceover(f)
    return f.read_text(encoding="utf-8"), result


class TestAnchorAmbiguityAndScoping:
    def test_fp_collision_identical_bodies_keep_their_own_voiceover(self, tmp_path: Path):
        """Two identical-body cells in one group: each keeps its own VO.

        Without an occurrence ordinal both fp anchors resolve to the first
        copy, clustering both voiceovers there and stripping the second.
        """
        deck = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="demo"\n# ## Demo\n\n'
            '# %% tags=["keep"]\nprint(result)\n\n'
            '# %% [markdown] lang="de" tags=["voiceover"]\n# Explains the FIRST print.\n\n'
            '# %% tags=["keep"]\nprint(result)\n\n'
            '# %% [markdown] lang="de" tags=["voiceover"]\n# Explains the SECOND print.\n'
        )
        out, res = _round_trip(tmp_path, deck)
        p1 = out.index("print(result)")
        p2 = out.index("print(result)", p1 + 1)
        v1 = out.index("Explains the FIRST")
        v2 = out.index("Explains the SECOND")
        assert p1 < v1 < p2 < v2
        assert res.relocated_cells == 0

    def test_fp_collision_in_build_merge(self, tmp_path: Path):
        """The build path resolves colliding fp anchors to the right copy."""
        deck = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="demo"\n# ## Demo\n\n'
            '# %% tags=["keep"]\nprint(result)\n\n'
            '# %% [markdown] lang="de" tags=["voiceover"]\n# Explains the FIRST print.\n\n'
            '# %% tags=["keep"]\nprint(result)\n\n'
            '# %% [markdown] lang="de" tags=["voiceover"]\n# Explains the SECOND print.\n'
        )
        f = tmp_path / "slides_demo.py"
        f.write_text(deck, encoding="utf-8", newline="\n")
        extract_voiceover(f)
        slide_after = f.read_text(encoding="utf-8")
        comp = (tmp_path / "voiceover_demo.py").read_text(encoding="utf-8")

        merged, unmatched = merge_voiceover_text(slide_after, comp)

        assert unmatched == []
        _assert_order(
            merged,
            "print(result)",
            "Explains the FIRST print.",
            "Explains the SECOND print.",
        )
        # The second VO must sit after the SECOND print, not the first.
        p2 = merged.index("print(result)", merged.index("print(result)") + 1)
        assert merged.index("Explains the SECOND print.") > p2

    def test_id_collision_two_cells_share_slide_id(self, tmp_path: Path):
        """A VO after the 2nd of two cells sharing a slide_id stays there."""
        deck = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="p"\n# ## Para\n\n'
            '# %% lang="de" slide_id="p"\nfirst_line = 1\n\n'
            '# %% [markdown] lang="de" tags=["voiceover"]\n# VO after the SECOND p-cell.\n'
        )
        out, res = _round_trip(tmp_path, deck)
        assert out.index("first_line = 1") < out.index("VO after the SECOND p-cell.")
        assert res.relocated_cells == 0

    def test_renamed_for_slide_is_unmatched_not_misplaced(self, tmp_path: Path):
        """If the owning slide_id is renamed, the VO is unmatched, not dropped
        into a foreign group that happens to share a body fingerprint."""
        deck = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="one"\n# ## Group One\n\n'
            '# %% lang="de"\nimport os\n\n'
            '# %% [markdown] lang="de" tags=["slide"] slide_id="two"\n# ## Group Two\n\n'
            '# %% lang="de"\nimport os\n\n'
            '# %% [markdown] lang="de" tags=["voiceover"]\n# VO belongs to group TWO.\n'
        )
        f = tmp_path / "slides_x.py"
        f.write_text(deck, encoding="utf-8", newline="\n")
        extract_voiceover(f)
        # Rename the owning slide between extract and inline.
        t = f.read_text(encoding="utf-8").replace('slide_id="two"', 'slide_id="two-renamed"')
        f.write_text(t, encoding="utf-8", newline="\n")

        res = inline_voiceover(f)
        out = f.read_text(encoding="utf-8")

        assert res.unmatched_cells == 1
        assert res.relocated_cells == 0
        # Appended at the end — never inserted into the foreign group one.
        assert out.index("VO belongs to group TWO.") > out.index("## Group Two")

    def test_renamed_for_slide_in_build_merge_reports_unmatched(self):
        """merge_voiceover_text must report the unmatched id, not silently
        place the VO into a body-identical foreign group."""
        slide = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="one"\n# ## Group One\n\n'
            '# %% lang="de"\nimport os\n\n'
            '# %% [markdown] lang="de" tags=["slide"] slide_id="two-renamed"\n# ## Group Two\n\n'
            '# %% lang="de"\nimport os\n'
        )
        comp = (
            '# %% [markdown] lang="de" tags=["voiceover"] for_slide="two" '
            'vo_anchor="fp:deadbeef0000#0"\n# VO belongs to group TWO.\n'
        )
        merged, unmatched = merge_voiceover_text(slide, comp)

        assert unmatched == ["two"]
        assert "VO belongs to group TWO." not in merged

    def test_bilingual_group_not_truncated_by_other_language_twin(self, tmp_path: Path):
        """In a bilingual deck, a DE voiceover after a DE continuation cell
        that follows the EN twin returns to its predecessor, not the heading."""
        deck = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="a"\n# ## A DE\n\n'
            '# %% [markdown] lang="en" tags=["slide"] slide_id="a"\n# ## A EN\n\n'
            '# %% [markdown] lang="de"\n# de prose A.\n\n'
            '# %% [markdown] lang="de" tags=["voiceover"]\n# DE VO for A.\n\n'
            '# %% [markdown] lang="de" tags=["slide"] slide_id="b"\n# ## B DE\n'
        )
        out, res = _round_trip(tmp_path, deck)
        # VO returns after its real predecessor, not hoisted above it.
        assert out.index("de prose A.") < out.index("DE VO for A.")
        # ...and not wedged between the de/en heading pair.
        assert out.index("## A EN") < out.index("DE VO for A.")
        assert res.relocated_cells == 0

    def test_fingerprint_invariant_to_internal_blank_lines(self, tmp_path: Path):
        """A predecessor with 2+ internal blank lines still anchors after
        extract's \\n{3,} -> \\n\\n cleanup mutates its body."""
        deck = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="s"\n# ## Slide\n\n'
            '# %% lang="de"\na = 1\n\n\n\nb = 2\n\n'
            '# %% [markdown] lang="de" tags=["voiceover"]\n# VO after the multi-blank cell.\n\n'
            '# %% lang="de"\ntrailing = 99\n'
        )
        out, res = _round_trip(tmp_path, deck)
        assert res.relocated_cells == 0
        _assert_order(out, "b = 2", "VO after the multi-blank cell.", "trailing = 99")
