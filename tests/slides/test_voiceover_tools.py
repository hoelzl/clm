"""Tests for voiceover extraction and inlining."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from clm.notebooks.slide_parser import parse_cells
from clm.slides.split import split_text
from clm.slides.voiceover_tools import (
    PairedExtractionResult,
    VoiceoverError,
    companion_path,
    extract_voiceover,
    extract_voiceover_pair,
    inline_voiceover,
    merge_voiceover_text,
    read_companion_baselines,
    render_companion_update,
    resolve_companion,
    update_companion_narrative,
)


def _for_slide_set(path: Path) -> set[str]:
    return {
        c.metadata.for_slide
        for c in parse_cells(path.read_text(encoding="utf-8"))
        if c.metadata.for_slide
    }


def _slide_ids(path: Path) -> list[str]:
    return [
        c.metadata.slide_id
        for c in parse_cells(path.read_text(encoding="utf-8"))
        if c.metadata.is_slide_start
    ]


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
# companion_name / resolve_companion (folder-or-sibling layout)
# ---------------------------------------------------------------------------


def test_companion_name_is_directory_independent(tmp_path: Path):
    from clm.slides.voiceover_tools import companion_name

    assert companion_name(tmp_path / "slides_intro.de.py") == "voiceover_intro.de.py"
    assert companion_name(Path("a/b/c/topic_overview.py")) == "voiceover_overview.py"


def test_resolve_companion_none_when_absent(tmp_path: Path):
    from clm.slides.voiceover_tools import resolve_companion

    assert resolve_companion(tmp_path / "slides_intro.py") is None


def test_resolve_companion_finds_sibling(tmp_path: Path):
    from clm.slides.voiceover_tools import resolve_companion

    slide = tmp_path / "slides_intro.py"
    sibling = tmp_path / "voiceover_intro.py"
    sibling.write_text('# %% [markdown] tags=["voiceover"]\n# hi\n', encoding="utf-8")
    assert resolve_companion(slide) == sibling


def test_resolve_companion_prefers_voiceover_subdir(tmp_path: Path):
    from clm.slides.voiceover_tools import COMPANION_SUBDIR, resolve_companion

    slide = tmp_path / "slides_intro.py"
    (tmp_path / COMPANION_SUBDIR).mkdir()
    nested = tmp_path / COMPANION_SUBDIR / "voiceover_intro.py"
    nested.write_text('# %% [markdown] tags=["voiceover"]\n# hi\n', encoding="utf-8")
    # A stale sibling must not win — the relocated companion takes precedence.
    (tmp_path / "voiceover_intro.py").write_text("# sibling\n", encoding="utf-8")
    assert resolve_companion(slide) == nested


def test_inline_reads_and_deletes_voiceover_subdir_companion(tmp_path: Path):
    """A companion relocated into ``voiceover/`` is inlined and removed there."""
    from clm.slides.voiceover_tools import COMPANION_SUBDIR

    slide = tmp_path / "slides_intro.py"
    slide.write_text(SLIDE_WITH_SLIDE_IDS, encoding="utf-8")

    # Extract to a sibling (forced), then relocate the companion into voiceover/.
    extract_voiceover(slide, layout="sibling")
    sibling = tmp_path / "voiceover_intro.py"
    assert sibling.exists()
    subdir = tmp_path / COMPANION_SUBDIR
    subdir.mkdir()
    nested = subdir / "voiceover_intro.py"
    nested.write_text(sibling.read_text(encoding="utf-8"), encoding="utf-8")
    sibling.unlink()

    result = inline_voiceover(slide)

    assert result.cells_inlined > 0
    assert result.companion_deleted
    assert not nested.exists()  # deleted from the subdir, not re-created as a sibling
    assert not sibling.exists()
    assert 'tags=["voiceover"]' in slide.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# expected_companion + extract/split/unify write target (Phase 3)
# ---------------------------------------------------------------------------


def test_expected_companion_layouts(tmp_path: Path):
    from clm.slides.voiceover_tools import expected_companion

    slide = tmp_path / "slides_intro.py"
    # auto, nothing present -> subdir (new default for a brand-new companion)
    assert expected_companion(slide) == tmp_path / "voiceover" / "voiceover_intro.py"
    # explicit subdir -> nested even without the dir present
    assert (
        expected_companion(slide, layout="subdir") == tmp_path / "voiceover" / "voiceover_intro.py"
    )
    # explicit sibling -> sibling
    assert expected_companion(slide, layout="sibling") == tmp_path / "voiceover_intro.py"
    # auto, an existing sibling companion for THIS deck -> keep it a sibling
    sibling = tmp_path / "voiceover_intro.py"
    sibling.write_text("# companion\n", encoding="utf-8")
    assert expected_companion(slide) == sibling
    sibling.unlink()
    # auto, voiceover/ dir present -> nested (dir presence still wins)
    (tmp_path / "voiceover").mkdir()
    assert expected_companion(slide, layout="sibling") == tmp_path / "voiceover_intro.py"
    assert expected_companion(slide) == tmp_path / "voiceover" / "voiceover_intro.py"


def test_extract_layout_subdir_creates_folder(tmp_path: Path):
    slide = tmp_path / "slides_intro.py"
    slide.write_text(SLIDE_WITH_SLIDE_IDS, encoding="utf-8")
    result = extract_voiceover(slide, layout="subdir")
    nested = tmp_path / "voiceover" / "voiceover_intro.py"
    assert nested.exists()
    assert not (tmp_path / "voiceover_intro.py").exists()
    assert result.companion_file == str(nested)
    assert 'tags=["voiceover"]' not in slide.read_text(encoding="utf-8")


def test_extract_auto_detects_existing_voiceover_dir(tmp_path: Path):
    slide = tmp_path / "slides_intro.py"
    slide.write_text(SLIDE_WITH_SLIDE_IDS, encoding="utf-8")
    (tmp_path / "voiceover").mkdir()
    extract_voiceover(slide)  # layout=None -> auto-detect the dir
    assert (tmp_path / "voiceover" / "voiceover_intro.py").exists()
    assert not (tmp_path / "voiceover_intro.py").exists()


def test_extract_refuses_when_companion_in_other_layout(tmp_path: Path):
    slide = tmp_path / "slides_intro.py"
    slide.write_text(SLIDE_WITH_SLIDE_IDS, encoding="utf-8")
    # A stale sibling companion + a voiceover/ dir: extract --layout subdir must
    # refuse (resolve finds the sibling) rather than create a second copy.
    (tmp_path / "voiceover_intro.py").write_text("# stale\n", encoding="utf-8")
    (tmp_path / "voiceover").mkdir()
    with pytest.raises(VoiceoverError):
        extract_voiceover(slide, layout="subdir")


def test_extract_force_relocates_and_prunes_stale(tmp_path: Path):
    slide = tmp_path / "slides_intro.py"
    slide.write_text(SLIDE_WITH_SLIDE_IDS, encoding="utf-8")
    (tmp_path / "voiceover_intro.py").write_text("# stale\n", encoding="utf-8")
    (tmp_path / "voiceover").mkdir()
    extract_voiceover(slide, layout="subdir", force=True)
    assert (tmp_path / "voiceover" / "voiceover_intro.py").exists()
    assert not (tmp_path / "voiceover_intro.py").exists()  # stale sibling pruned


def test_inline_removes_emptied_voiceover_dir(tmp_path: Path):
    slide = tmp_path / "slides_intro.py"
    slide.write_text(SLIDE_WITH_SLIDE_IDS, encoding="utf-8")
    extract_voiceover(slide, layout="subdir")
    subdir = tmp_path / "voiceover"
    assert (subdir / "voiceover_intro.py").exists()

    result = inline_voiceover(slide)
    assert result.companion_deleted
    assert not subdir.exists()  # emptied folder removed on full inline


def test_split_keeps_companion_in_voiceover_dir(tmp_path: Path):
    from clm.slides.split import split_in_file

    slide = tmp_path / "slides_intro.py"
    slide.write_text(SLIDE_WITH_VOICEOVER, encoding="utf-8")
    extract_voiceover(slide, layout="subdir")  # foldered bilingual companion
    assert (tmp_path / "voiceover" / "voiceover_intro.py").exists()

    split_in_file(slide)  # companion halves follow the source companion into voiceover/
    assert (tmp_path / "voiceover" / "voiceover_intro.de.py").exists()
    assert (tmp_path / "voiceover" / "voiceover_intro.en.py").exists()
    assert not (tmp_path / "voiceover_intro.de.py").exists()
    assert not (tmp_path / "voiceover_intro.en.py").exists()


def test_unify_keeps_companion_in_voiceover_dir(tmp_path: Path):
    from clm.slides.split import split_in_file, unify_in_file

    slide = tmp_path / "slides_intro.py"
    slide.write_text(SLIDE_WITH_VOICEOVER, encoding="utf-8")
    extract_voiceover(slide, layout="subdir")
    split_in_file(slide)
    # Drop the bilingual companion so the assertion proves unify *recreates* it
    # in voiceover/ (not as a sibling) from the foldered de/en halves.
    (tmp_path / "voiceover" / "voiceover_intro.py").unlink()

    unify_in_file(tmp_path / "slides_intro.de.py", tmp_path / "slides_intro.en.py", force=True)
    assert (tmp_path / "voiceover" / "voiceover_intro.py").exists()
    assert not (tmp_path / "voiceover_intro.py").exists()


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

        result = extract_voiceover(slide_file, layout="sibling")

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
        assert resolve_companion(slide_file) is None

    def test_preserves_existing_slide_ids(self, tmp_path: Path):
        slide_file = tmp_path / "slides_intro.py"
        slide_file.write_text(SLIDE_WITH_SLIDE_IDS, encoding="utf-8")

        result = extract_voiceover(slide_file, layout="sibling")

        assert result.cells_extracted == 2

        comp = tmp_path / "voiceover_intro.py"
        comp_text = comp.read_text(encoding="utf-8")
        # Companion cells should reference the existing slide_id
        assert 'for_slide="thema-eins"' in comp_text

    def test_companion_has_for_slide_metadata(self, tmp_path: Path):
        slide_file = tmp_path / "slides_intro.py"
        slide_file.write_text(SLIDE_WITH_VOICEOVER, encoding="utf-8")

        extract_voiceover(slide_file, layout="sibling")

        comp = tmp_path / "voiceover_intro.py"
        comp_text = comp.read_text(encoding="utf-8")
        assert "for_slide=" in comp_text

    def test_slide_file_has_slide_ids_after_extraction(self, tmp_path: Path):
        slide_file = tmp_path / "slides_intro.py"
        slide_file.write_text(SLIDE_WITH_VOICEOVER, encoding="utf-8")

        extract_voiceover(slide_file)

        slide_text = slide_file.read_text(encoding="utf-8")
        assert "slide_id=" in slide_text

    def test_refuses_to_clobber_existing_companion_without_force(self, tmp_path: Path):
        """Re-extracting onto an existing companion would discard content that
        lives only in the companion — refuse without ``force`` (Tier-1 fix)."""
        slide_file = tmp_path / "slides_intro.py"
        slide_file.write_text(SLIDE_WITH_VOICEOVER, encoding="utf-8")
        extract_voiceover(slide_file, layout="sibling")
        comp = tmp_path / "voiceover_intro.py"
        # A hand-edit that lives ONLY in the companion (not in the slide).
        comp.write_text(
            comp.read_text(encoding="utf-8")
            + '\n# %% [markdown] lang="de" tags=["voiceover"] for_slide="x"\n# hand edit\n',
            encoding="utf-8",
        )
        before = comp.read_text(encoding="utf-8")
        # Re-add a voiceover cell so the empty-vo early-return does not fire.
        slide_file.write_text(
            slide_file.read_text(encoding="utf-8")
            + '# %% [markdown] lang="de" tags=["voiceover"] slide_id="thema"\n# new vo\n',
            encoding="utf-8",
        )
        with pytest.raises(VoiceoverError, match="refusing to overwrite"):
            extract_voiceover(slide_file, layout="sibling")
        # The companion (and its hand-edit) survives untouched.
        assert comp.read_text(encoding="utf-8") == before

    def test_force_rebuilds_existing_companion(self, tmp_path: Path):
        slide_file = tmp_path / "slides_intro.py"
        slide_file.write_text(SLIDE_WITH_VOICEOVER, encoding="utf-8")
        extract_voiceover(slide_file, layout="sibling")
        comp = tmp_path / "voiceover_intro.py"
        comp.write_text("# stale placeholder\n", encoding="utf-8")
        # Re-add a voiceover cell so there is something to extract.
        slide_file.write_text(
            slide_file.read_text(encoding="utf-8")
            + '# %% [markdown] lang="de" tags=["voiceover"] slide_id="thema"\n# fresh vo\n',
            encoding="utf-8",
        )
        result = extract_voiceover(slide_file, force=True, layout="sibling")
        assert result.cells_extracted >= 1
        rebuilt = comp.read_text(encoding="utf-8")
        assert "stale placeholder" not in rebuilt
        assert "fresh vo" in rebuilt


# ---------------------------------------------------------------------------
# inline_voiceover — basic
# ---------------------------------------------------------------------------


class TestInlineVoiceover:
    def test_inlines_voiceover_cells(self, tmp_path: Path):
        slide_file = tmp_path / "slides_intro.py"
        slide_file.write_text(SLIDE_WITH_VOICEOVER, encoding="utf-8")

        # Extract first
        extract_voiceover(slide_file, layout="sibling")
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

        extract_voiceover(slide_file, layout="sibling")
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

    def test_partial_inline_retains_only_unmatched(self, tmp_path: Path):
        """Matched cells are inlined; the companion is rewritten to hold only the
        unmatched remainder (recoverable), never destroyed (Tier-1 fix)."""
        slide_file = tmp_path / "slides_intro.py"
        slide_file.write_text(
            '# %% [markdown] lang="de" tags=["slide"] slide_id="one"\n# ## One\n'
            '# %% [markdown] lang="de" tags=["voiceover"] slide_id="one"\n# VO one\n'
            '# %% [markdown] lang="de" tags=["slide"] slide_id="two"\n# ## Two\n'
            '# %% [markdown] lang="de" tags=["voiceover"] slide_id="two"\n# VO two\n',
            encoding="utf-8",
        )
        extract_voiceover(slide_file, layout="sibling")
        comp = tmp_path / "voiceover_intro.py"
        # Rename slide "two" so its companion cell can no longer match.
        slide_file.write_text(
            slide_file.read_text(encoding="utf-8").replace(
                'slide_id="two"', 'slide_id="renamed-two"'
            ),
            encoding="utf-8",
        )

        result = inline_voiceover(slide_file)

        assert result.cells_inlined == 1
        assert result.unmatched_cells == 1
        assert result.companion_deleted is False
        assert result.companion_retained is True

        slide_text = slide_file.read_text(encoding="utf-8")
        assert "VO one" in slide_text  # matched cell inlined
        assert "VO two" not in slide_text  # unmatched NOT stranded in the slide

        comp_text = comp.read_text(encoding="utf-8")
        assert "VO two" in comp_text  # unmatched kept in companion
        assert "VO one" not in comp_text  # matched removed from companion


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

        result = extract_voiceover(slide_file, layout="sibling")
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
        """Unmatched companion cells are *retained in the companion*, not dumped
        into the slide nor destroyed (Tier-1 data-loss fix)."""
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
        # The companion is preserved (recoverable source of truth), not deleted.
        assert result.companion_deleted is False
        assert result.companion_retained is True
        assert comp.exists()
        assert "This has no matching slide." in comp.read_text(encoding="utf-8")
        # The unmatched narration is NOT stranded in the slide file.
        assert "This has no matching slide." not in slide_file.read_text(encoding="utf-8")

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
# Title-greeting voiceover (#242)
#
# The title slide is generated by the j2 ``header``/``header_de``/``header_en``
# macros and carries no slide_id of its own. The greeting voiceover attaches by
# the ``slide_id="title"`` convention. Extract must emit ``for_slide="title"``
# and the build merge / inline must anchor it to the title macro cell — both of
# which were previously broken (the narration was dropped at build, or stranded
# on inline). is_title_macro_cell / TITLE_SLIDE_ID is the shared anchor.
# ---------------------------------------------------------------------------


TITLE_SLIDE_SPLIT_DE = """\
# j2 from 'macros.j2' import header_de
# {{ header_de("Titel") }}

# %% [markdown] lang="de" tags=["voiceover"] slide_id="title"
# - Herzlich willkommen zur neuen Woche!

# %% [markdown] lang="de" tags=["slide"] slide_id="first-real-slide"
# - Erste echte Folie
"""

TITLE_SLIDE_BILINGUAL = """\
# j2 from 'macros.j2' import header
# {{ header("Titel", "Title") }}

# %% [markdown] lang="de" tags=["voiceover"] slide_id="title"
# - Herzlich willkommen!

# %% [markdown] lang="en" tags=["voiceover"] slide_id="title"
# - Welcome!

# %% [markdown] lang="de" tags=["slide"] slide_id="first-real-slide"
# - Erste echte Folie

# %% [markdown] lang="en" tags=["slide"] slide_id="first-real-slide"
# - First real slide
"""


# #246 — a title greeting authored *before* the title slide's trailing
# ``keep``/code cells. The greeting's only predecessor is the slide_id-less j2
# title macro, so it must be anchored to the macro (``tm:``) and restored at the
# *start* of the title group, not dumped at the end.
TITLE_BEFORE_KEEP_EN = """\
# j2 from 'macros.j2' import header_en
# {{ header_en("LangChain Tracing with LangSmith") }}

# %% [markdown] lang="en" tags=["voiceover"] slide_id="title"
# - Welcome back!

# %% tags=["keep"]
import os

# %% tags=["keep"]
from langchain_core.messages import HumanMessage

# %% [markdown] lang="en" tags=["slide"] slide_id="what-is-langsmith"
# - What Is LangSmith?
"""

# The same deck but with the greeting authored *after* a trailing ``keep`` cell:
# its predecessor is a real content cell, so a normal ``fp:`` anchor restores it.
TITLE_AFTER_KEEP_EN = """\
# j2 from 'macros.j2' import header_en
# {{ header_en("LangChain Tracing with LangSmith") }}

# %% tags=["keep"]
import os

# %% [markdown] lang="en" tags=["voiceover"] slide_id="title"
# - Welcome back!

# %% [markdown] lang="en" tags=["slide"] slide_id="what-is-langsmith"
# - What Is LangSmith?
"""


def _strip_for_slide(text: str) -> str:
    """Drop the ``for_slide`` attribute — the build worker strips it from output,
    so this is how we compare extract+merge against the inline-authored source."""
    return re.sub(r'\s*for_slide="[^"]*"', "", text)


def _vo_anchor_of(comp: Path) -> str | None:
    """The ``vo_anchor`` token on the companion's title voiceover cell, if any."""
    for cell in parse_cells(comp.read_text(encoding="utf-8")):
        if cell.metadata.is_narrative and cell.metadata.for_slide == "title":
            m = re.search(r'vo_anchor="([^"]*)"', cell.header)
            return m.group(1) if m else None
    return None


class TestTitleGreetingVoiceover:
    """#242 — a voiceover for the macro-generated (slide_id-less) title slide."""

    def test_extract_split_half_emits_for_slide_title(self, tmp_path: Path):
        de = tmp_path / "slides_intro.de.py"
        de.write_text(TITLE_SLIDE_SPLIT_DE, encoding="utf-8")

        extract_voiceover(de, force=True, layout="sibling")

        comp = tmp_path / "voiceover_intro.de.py"
        assert comp.exists()
        assert _for_slide_set(comp) == {"title"}

    def test_extract_bilingual_emits_for_slide_title_on_both(self, tmp_path: Path):
        slide = tmp_path / "slides_intro.py"
        slide.write_text(TITLE_SLIDE_BILINGUAL, encoding="utf-8")

        extract_voiceover(slide, force=True, layout="sibling")

        comp = tmp_path / "voiceover_intro.py"
        cells = [
            c for c in parse_cells(comp.read_text(encoding="utf-8")) if c.metadata.is_narrative
        ]
        assert len(cells) == 2
        assert all(c.metadata.for_slide == "title" for c in cells)

    def test_paired_extract_stamps_title_for_slide_on_both_halves(self, tmp_path: Path):
        de = tmp_path / "slides_intro.de.py"
        en = tmp_path / "slides_intro.en.py"
        de.write_text(TITLE_SLIDE_SPLIT_DE, encoding="utf-8")
        en.write_text(
            TITLE_SLIDE_SPLIT_DE.replace("header_de", "header_en")
            .replace('"Titel"', '"Title"')
            .replace('lang="de"', 'lang="en"')
            .replace("Herzlich willkommen zur neuen Woche!", "Welcome to the new week!")
            .replace("Erste echte Folie", "First real slide"),
            encoding="utf-8",
        )

        extract_voiceover_pair(de, en, force=True, layout="sibling")

        de_comp = tmp_path / "voiceover_intro.de.py"
        en_comp = tmp_path / "voiceover_intro.en.py"
        assert _for_slide_set(de_comp) == {"title"}
        assert _for_slide_set(de_comp) == _for_slide_set(en_comp)

    def test_merge_anchors_title_voiceover_after_macro(self):
        slide = """\
# j2 from 'macros.j2' import header_de
# {{ header_de("Titel") }}

# %% [markdown] lang="de" tags=["slide"] slide_id="first-real-slide"
# - Erste echte Folie
"""
        companion = (
            '# %% [markdown] lang="de" tags=["voiceover"] slide_id="title" '
            'for_slide="title"\n# - Herzlich willkommen!\n'
        )
        merged, unmatched = merge_voiceover_text(slide, companion)

        assert unmatched == []
        # Greeting lands after the title macro, before the first real slide.
        assert merged.index("Herzlich willkommen") < merged.index("Erste echte Folie")
        assert merged.index("header_de") < merged.index("Herzlich willkommen")

    def test_merge_accepts_legacy_companion_without_for_slide(self):
        """A pre-#242 companion carries slide_id="title" but no for_slide; it must
        still merge so already-converted decks build without a re-extract."""
        slide = """\
# j2 from 'macros.j2' import header_de
# {{ header_de("Titel") }}

# %% [markdown] lang="de" tags=["slide"] slide_id="first-real-slide"
# - Erste echte Folie
"""
        legacy = '# %% [markdown] lang="de" tags=["voiceover"] slide_id="title"\n# - Greeting.\n'
        merged, unmatched = merge_voiceover_text(slide, legacy)

        assert unmatched == []
        assert merged.index("Greeting") < merged.index("Erste echte Folie")

    def test_merge_two_title_voiceovers_preserve_order(self):
        slide = """\
# j2 from 'macros.j2' import header_de
# {{ header_de("Titel") }}

# %% [markdown] lang="de" tags=["slide"] slide_id="first-real-slide"
# - Folie
"""
        companion = (
            '# %% [markdown] lang="de" tags=["voiceover"] for_slide="title"\n# - First.\n\n'
            '# %% [markdown] lang="de" tags=["voiceover"] for_slide="title"\n# - Second.\n'
        )
        merged, unmatched = merge_voiceover_text(slide, companion)

        assert unmatched == []
        assert merged.index("First.") < merged.index("Second.")

    def test_merge_title_without_macro_stays_unmatched(self):
        """for_slide="title" but the deck has no header macro (mis-authored): the
        cell is reported unmatched rather than guessed at — and must not crash."""
        slide = '# %% [markdown] lang="de" tags=["slide"] slide_id="real"\n# - Real\n'
        companion = '# %% [markdown] lang="de" tags=["voiceover"] for_slide="title"\n# - Greeting\n'
        merged, unmatched = merge_voiceover_text(slide, companion)

        assert unmatched == ["title"]

    def test_extract_then_merge_byte_identical_to_inline(self, tmp_path: Path):
        """The #242 acceptance criterion at the source level: extract+merge yields
        the inline-authored deck back, modulo the for_slide the worker strips."""
        for name, inline in (
            ("slides_intro.de.py", TITLE_SLIDE_SPLIT_DE),
            ("slides_intro.py", TITLE_SLIDE_BILINGUAL),
        ):
            slide = tmp_path / name
            slide.write_text(inline, encoding="utf-8")
            extract_voiceover(slide, force=True, layout="sibling")
            comp = slide.with_name(slide.name.replace("slides_", "voiceover_"))
            merged, unmatched = merge_voiceover_text(
                slide.read_text(encoding="utf-8"), comp.read_text(encoding="utf-8")
            )
            assert unmatched == []
            assert _strip_for_slide(merged).strip() == inline.strip()

    def test_extract_then_inline_restores_title_voiceover(self, tmp_path: Path):
        de = tmp_path / "slides_intro.de.py"
        de.write_text(TITLE_SLIDE_SPLIT_DE, encoding="utf-8")

        extract_voiceover(de, force=True)
        result = inline_voiceover(de)

        assert result.unmatched_cells == 0
        assert result.companion_deleted
        assert de.read_text(encoding="utf-8").strip() == TITLE_SLIDE_SPLIT_DE.strip()

    def test_merge_places_title_after_continuation_cell(self):
        """A title-owned continuation cell (e.g. a ``keep`` code cell with no
        slide_id) between the macro and the greeting: the voiceover lands at the
        end of the title group, after that cell — matching the inline layout."""
        slide = """\
# j2 from 'macros.j2' import header_de
# {{ header_de("Titel") }}

# %% tags=["keep"]
agenda = 1

# %% [markdown] lang="de" tags=["slide"] slide_id="first-real-slide"
# - Folie
"""
        companion = (
            '# %% [markdown] lang="de" tags=["voiceover"] for_slide="title"\n# - Greeting.\n'
        )
        merged, unmatched = merge_voiceover_text(slide, companion)

        assert unmatched == []
        assert merged.index("agenda = 1") < merged.index("Greeting.")
        assert merged.index("Greeting.") < merged.index("first-real-slide")

    # -- #246: title greeting authored before trailing keep cells ----------

    def test_extract_title_before_keep_emits_macro_anchor(self, tmp_path: Path):
        """A title greeting with no content predecessor (authored before the
        title slide's keep cells) is stamped with the ``tm:`` title-macro anchor
        so the merge can restore its position rather than appending at the end of
        the title group (#246)."""
        slide = tmp_path / "slides_015.en.py"
        slide.write_text(TITLE_BEFORE_KEEP_EN, encoding="utf-8")

        extract_voiceover(slide, force=True, layout="sibling")

        comp = slide.with_name("voiceover_015.en.py")
        assert _vo_anchor_of(comp) == "tm:title#0"

    def test_extract_then_merge_title_before_keep_byte_identical(self, tmp_path: Path):
        """#246 acceptance (build path): extract + the build merge restore the
        greeting to its authored slot — immediately after the title slide and
        *before* the trailing keep cells — byte-identically."""
        slide = tmp_path / "slides_015.en.py"
        slide.write_text(TITLE_BEFORE_KEEP_EN, encoding="utf-8")
        extract_voiceover(slide, force=True, layout="sibling")
        comp = slide.with_name("voiceover_015.en.py")

        merged, unmatched = merge_voiceover_text(
            slide.read_text(encoding="utf-8"), comp.read_text(encoding="utf-8")
        )

        assert unmatched == []
        # Greeting immediately after the title slide, before the keep cells.
        assert merged.index("Welcome back") < merged.index("import os")
        assert _strip_for_slide(merged).strip() == TITLE_BEFORE_KEEP_EN.strip()

    def test_extract_then_inline_title_before_keep_byte_identical(self, tmp_path: Path):
        """#246 acceptance (inline path): extract then inline restores the deck
        exactly, with the greeting back before the keep cells and no relocation
        reported."""
        slide = tmp_path / "slides_015.en.py"
        slide.write_text(TITLE_BEFORE_KEEP_EN, encoding="utf-8")

        extract_voiceover(slide, force=True)
        result = inline_voiceover(slide)

        assert result.unmatched_cells == 0
        assert result.relocated_cells == 0
        assert result.companion_deleted
        assert slide.read_text(encoding="utf-8").strip() == TITLE_BEFORE_KEEP_EN.strip()

    def test_extract_title_after_keep_roundtrips_via_fp_anchor(self, tmp_path: Path):
        """A greeting authored *after* a keep cell has a real content predecessor,
        so it gets an ``fp:`` anchor (not ``tm:``) and still round-trips exactly —
        the title-group bounds now resolve for the slide_id-less title slide."""
        slide = tmp_path / "slides_015.en.py"
        slide.write_text(TITLE_AFTER_KEEP_EN, encoding="utf-8")

        extract_voiceover(slide, force=True, layout="sibling")
        comp = slide.with_name("voiceover_015.en.py")
        anchor = _vo_anchor_of(comp)
        assert anchor is not None and anchor.startswith("fp:")

        merged, unmatched = merge_voiceover_text(
            slide.read_text(encoding="utf-8"), comp.read_text(encoding="utf-8")
        )
        assert unmatched == []
        assert merged.index("import os") < merged.index("Welcome back")
        assert _strip_for_slide(merged).strip() == TITLE_AFTER_KEEP_EN.strip()

    def test_legacy_anchorless_title_before_keep_unchanged(self):
        """A *legacy* companion (``for_slide="title"`` with no ``vo_anchor``) keeps
        the documented group-end fallback even when the title slide has trailing
        cells — the #246 fix only affects freshly-extracted, anchored companions,
        so already-built decks are unaffected."""
        slide = """\
# j2 from 'macros.j2' import header_en
# {{ header_en("T") }}

# %% tags=["keep"]
import os

# %% [markdown] lang="en" tags=["slide"] slide_id="real"
# - Real
"""
        legacy = '# %% [markdown] lang="en" tags=["voiceover"] for_slide="title"\n# - Greeting.\n'
        merged, unmatched = merge_voiceover_text(slide, legacy)

        assert unmatched == []
        # No anchor to honour → falls back to the end of the title group.
        assert merged.index("import os") < merged.index("Greeting.")
        assert merged.index("Greeting.") < merged.index("real")

    def test_extract_title_with_cell_before_macro_uses_macro_anchor(self, tmp_path: Path):
        """A content cell authored *before* the j2 title macro (e.g. a top-of-deck
        import) is out of the title group. The predecessor walk skips the macro
        and would otherwise land on that out-of-group cell, producing an ``fp:``
        anchor that silently can't resolve within the title bounds at merge —
        dumping the greeting at the group end. The title greeting must instead get
        the ``tm:`` macro anchor and round-trip byte-identically (#246 regression
        of the fix itself)."""
        deck = """\
# %% tags=["keep"]
import preamble

# j2 from 'macros.j2' import header_en
# {{ header_en("Title") }}

# %% [markdown] lang="en" tags=["voiceover"] slide_id="title"
# - Welcome!

# %% tags=["keep"]
import os

# %% [markdown] lang="en" tags=["slide"] slide_id="what-is-langsmith"
# - What Is LangSmith?
"""
        slide = tmp_path / "slides_015.en.py"
        slide.write_text(deck, encoding="utf-8")

        extract_voiceover(slide, force=True, layout="sibling")
        comp = slide.with_name("voiceover_015.en.py")
        assert _vo_anchor_of(comp) == "tm:title#0"

        merged, unmatched = merge_voiceover_text(
            slide.read_text(encoding="utf-8"), comp.read_text(encoding="utf-8")
        )
        assert unmatched == []
        # Greeting after the macro, before the *in-group* trailing keep cell.
        assert merged.index("Welcome!") < merged.index("import os")
        assert _strip_for_slide(merged).strip() == deck.strip()

        # And the inline round-trip restores it exactly, no relocation.
        result = inline_voiceover(slide)
        assert result.relocated_cells == 0
        assert result.unmatched_cells == 0
        assert slide.read_text(encoding="utf-8").strip() == deck.strip()


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

        de_res = extract_voiceover(de_file, layout="sibling")
        en_res = extract_voiceover(en_file, layout="sibling")

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

        extract_voiceover(de_file, layout="sibling")
        extract_voiceover(en_file, layout="sibling")

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
        extract_voiceover(de_file, layout="sibling")
        extract_voiceover(en_file, layout="sibling")

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


class TestExtractTwinAware:
    """``extract`` id generation is twin-aware on a split half (#162 defensive).

    Extracting the ``.de`` and ``.en`` halves separately must not mint
    divergent slide_ids — otherwise the two companions' ``for_slide`` sets
    diverge and one language silently ships missing narration at build.
    """

    @staticmethod
    def _born_split_with_vo(tmp_path: Path) -> tuple[Path, Path]:
        """A born-split (both halves id-less) pair with one inline voiceover
        per half, written via ``split_text`` so the headers are real."""
        de_vo = '# %% [markdown] lang="de" tags=["voiceover"]\n#\n# VO DE\n\n'
        en_vo = '# %% [markdown] lang="en" tags=["voiceover"]\n#\n# VO EN\n\n'
        bilingual = (
            "# j2 from 'macros.j2' import header\n"
            '# {{ header("Titel", "Title") }}\n\n'
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Mein Thema\n\n'
            + de_vo
            + '# %% [markdown] lang="en" tags=["slide"]\n# ## My Topic\n\n'
            + en_vo
        )
        de_text, en_text = split_text(bilingual)
        de = tmp_path / "slides_x.de.py"
        en = tmp_path / "slides_x.en.py"
        de.write_text(de_text, encoding="utf-8", newline="\n")
        en.write_text(en_text, encoding="utf-8", newline="\n")
        return de, en

    def test_per_language_extract_converges_to_parity(self, tmp_path: Path):
        de, en = self._born_split_with_vo(tmp_path)
        extract_voiceover(de, layout="sibling")
        extract_voiceover(
            en, layout="sibling"
        )  # twin-aware: adopts the DE half's freshly-minted id

        de_after = de.read_text(encoding="utf-8")
        en_after = en.read_text(encoding="utf-8")
        # Both halves end on the same id (DE-authority here, since DE was
        # extracted first) — the #162 invariant holds.
        assert 'slide_id="mein-thema"' in de_after
        assert 'slide_id="mein-thema"' in en_after
        assert 'slide_id="my-topic"' not in en_after

        de_comp = (tmp_path / "voiceover_x.de.py").read_text(encoding="utf-8")
        en_comp = (tmp_path / "voiceover_x.en.py").read_text(encoding="utf-8")
        assert 'for_slide="mein-thema"' in de_comp
        assert 'for_slide="mein-thema"' in en_comp

    def test_adopts_existing_twin_id(self, tmp_path: Path):
        # EN half already has an id; the id-less DE half must adopt it on
        # extract rather than minting "mein-thema" from its own heading.
        de = tmp_path / "slides_x.de.py"
        en = tmp_path / "slides_x.en.py"
        de.write_text(
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Mein Thema\n\n'
            '# %% [markdown] lang="de" tags=["voiceover"]\n# VO DE\n',
            encoding="utf-8",
            newline="\n",
        )
        en.write_text(
            '# %% [markdown] lang="en" tags=["slide"] slide_id="custom-id"\n# ## My Topic\n',
            encoding="utf-8",
            newline="\n",
        )
        extract_voiceover(de, layout="sibling")

        assert 'slide_id="custom-id"' in de.read_text(encoding="utf-8")
        de_comp = (tmp_path / "voiceover_x.de.py").read_text(encoding="utf-8")
        assert 'for_slide="custom-id"' in de_comp

    def test_mismatched_slide_count_mints_normally(self, tmp_path: Path):
        # Structurally misaligned halves (different slide counts): positional
        # reuse is unsafe, so extract mints normally (no crash, no wrong id) —
        # the validator's #162 detective surfaces the divergence instead.
        de = tmp_path / "slides_x.de.py"
        en = tmp_path / "slides_x.en.py"
        de.write_text(
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Mein Thema\n\n'
            '# %% [markdown] lang="de" tags=["voiceover"]\n# VO DE\n',
            encoding="utf-8",
            newline="\n",
        )
        en.write_text(
            '# %% [markdown] lang="en" tags=["slide"] slide_id="a"\n# ## A\n\n'
            '# %% [markdown] lang="en" tags=["slide"] slide_id="b"\n# ## B\n',
            encoding="utf-8",
            newline="\n",
        )
        extract_voiceover(de)  # must not raise
        de_after = de.read_text(encoding="utf-8")
        assert 'slide_id="mein-thema"' in de_after  # minted from its own heading

    def test_bilingual_extract_unaffected(self, tmp_path: Path):
        # A bilingual (non-split) file has no twin; twin_ids is None and id
        # generation is unchanged.
        p = tmp_path / "slides_x.py"
        p.write_text(
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Mein Thema\n\n'
            '# %% [markdown] lang="de" tags=["voiceover"]\n# VO DE\n',
            encoding="utf-8",
            newline="\n",
        )
        res = extract_voiceover(p)
        assert res.cells_extracted == 1
        assert 'slide_id="mein-thema"' in p.read_text(encoding="utf-8")


class TestPairedExtract:
    """``extract_voiceover_pair`` — one-op, EN-authority extraction over both
    halves of a split deck (the §8 'F later' paired extract).
    """

    @staticmethod
    def _born_split_with_vo(tmp_path: Path) -> tuple[Path, Path]:
        """A born-split (both halves id-less) pair, DE heading 'Mein Thema',
        EN heading 'My Topic', one voiceover per half. Written via split_text so
        the headers are real."""
        de_vo = '# %% [markdown] lang="de" tags=["voiceover"]\n#\n# VO DE\n\n'
        en_vo = '# %% [markdown] lang="en" tags=["voiceover"]\n#\n# VO EN\n\n'
        bilingual = (
            "# j2 from 'macros.j2' import header\n"
            '# {{ header("Titel", "Title") }}\n\n'
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Mein Thema\n\n'
            + de_vo
            + '# %% [markdown] lang="en" tags=["slide"]\n# ## My Topic\n\n'
            + en_vo
        )
        de_text, en_text = split_text(bilingual)
        de = tmp_path / "slides_x.de.py"
        en = tmp_path / "slides_x.en.py"
        de.write_text(de_text, encoding="utf-8", newline="\n")
        en.write_text(en_text, encoding="utf-8", newline="\n")
        return de, en

    def test_writes_both_companions_with_for_slide_parity(self, tmp_path: Path):
        de, en = self._born_split_with_vo(tmp_path)
        result = extract_voiceover_pair(de, en, layout="sibling")

        assert isinstance(result, PairedExtractionResult)
        de_comp = tmp_path / "voiceover_x.de.py"
        en_comp = tmp_path / "voiceover_x.en.py"
        assert de_comp.exists() and en_comp.exists()
        # for_slide sets agree across the two companions (the whole point).
        assert _for_slide_set(de_comp) == _for_slide_set(en_comp)
        # slide_id parity on the slide files (#162 invariant).
        assert _slide_ids(de) == _slide_ids(en)

    def test_en_authority_regardless_of_order(self, tmp_path: Path):
        # The slug comes from the EN heading ('My Topic' -> 'my-topic') and is
        # stamped on BOTH halves — unlike the per-language path, which is
        # DE-authority-by-order (see TestExtractTwinAware). Passing the halves in
        # either order yields the same EN-authority id.
        de, en = self._born_split_with_vo(tmp_path)
        extract_voiceover_pair(en, de)  # deliberately swapped order

        assert 'slide_id="my-topic"' in de.read_text(encoding="utf-8")
        assert 'slide_id="my-topic"' in en.read_text(encoding="utf-8")
        assert 'slide_id="mein-thema"' not in de.read_text(encoding="utf-8")

    def test_force_is_all_or_nothing(self, tmp_path: Path):
        # A pre-existing DE companion must block the paired extract even though
        # the EN companion does not exist yet — all-or-nothing over both halves.
        de, en = self._born_split_with_vo(tmp_path)
        (tmp_path / "voiceover_x.de.py").write_text("# stale\n", encoding="utf-8")
        assert not (tmp_path / "voiceover_x.en.py").exists()

        with pytest.raises(VoiceoverError):
            extract_voiceover_pair(de, en, layout="sibling")

        # --force rebuilds both from the current slide voiceover cells.
        result = extract_voiceover_pair(de, en, force=True, layout="sibling")
        assert (tmp_path / "voiceover_x.de.py").exists()
        assert (tmp_path / "voiceover_x.en.py").exists()
        assert result.de.cells_extracted >= 1 and result.en.cells_extracted >= 1
        assert _for_slide_set(tmp_path / "voiceover_x.de.py") == _for_slide_set(
            tmp_path / "voiceover_x.en.py"
        )

    def test_dry_run_writes_nothing(self, tmp_path: Path):
        de, en = self._born_split_with_vo(tmp_path)
        de_before = de.read_text(encoding="utf-8")
        en_before = en.read_text(encoding="utf-8")

        result = extract_voiceover_pair(de, en, dry_run=True)

        assert result.dry_run
        # No companions, and the slide files (incl. their ids) are untouched —
        # the pre-mint runs report-only under dry_run.
        assert not (tmp_path / "voiceover_x.de.py").exists()
        assert not (tmp_path / "voiceover_x.en.py").exists()
        assert de.read_text(encoding="utf-8") == de_before
        assert en.read_text(encoding="utf-8") == en_before

    def test_refuses_non_round_trippable_pair(self, tmp_path: Path):
        # Divergent SHARED (no-lang) cells -> unify is not byte-faithful -> the
        # EN-authority mint can't guarantee parity -> refuse loudly.
        de = tmp_path / "slides_x.de.py"
        en = tmp_path / "slides_x.en.py"
        de.write_text(
            "# %%\nx = 1\n\n"
            '# %% [markdown] lang="de" tags=["slide"]\n# ## A\n\n'
            '# %% [markdown] lang="de" tags=["voiceover"]\n# VO\n',
            encoding="utf-8",
            newline="\n",
        )
        en.write_text(
            "# %%\nx = 2\n\n"  # divergent shared cell -> unify refuses
            '# %% [markdown] lang="en" tags=["slide"]\n# ## A\n',
            encoding="utf-8",
            newline="\n",
        )
        de_before, en_before = de.read_text(encoding="utf-8"), en.read_text(encoding="utf-8")
        with pytest.raises(VoiceoverError, match="not structurally aligned"):
            extract_voiceover_pair(de, en)
        # Nothing written: no companions and the slide halves are byte-unchanged
        # (the refuse fires before any id-stamp or extraction).
        assert not (tmp_path / "voiceover_x.de.py").exists()
        assert not (tmp_path / "voiceover_x.en.py").exists()
        assert de.read_text(encoding="utf-8") == de_before
        assert en.read_text(encoding="utf-8") == en_before

    def test_no_op_when_no_voiceover_even_with_stale_companion(self, tmp_path: Path):
        # A split deck with a pre-existing companion but zero voiceover cells (the
        # idempotent post-extract state) is a clean no-op — the no-VO short-circuit
        # runs BEFORE the all-or-nothing force guard, matching the single-file path.
        de = tmp_path / "slides_x.de.py"
        en = tmp_path / "slides_x.en.py"
        de.write_text(
            '# %% [markdown] lang="de" tags=["slide"] slide_id="t"\n# ## Thema\n', encoding="utf-8"
        )
        en.write_text(
            '# %% [markdown] lang="en" tags=["slide"] slide_id="t"\n# ## Topic\n', encoding="utf-8"
        )
        (tmp_path / "voiceover_x.de.py").write_text("# stale\n", encoding="utf-8")

        result = extract_voiceover_pair(de, en)  # no force, but no VO → no raise
        assert all(r.cells_extracted == 0 for r in result.results)

    def test_mint_ids_false_rejects_non_parity_pair(self, tmp_path: Path):
        # mint_ids=False skips the EN-authority pre-mint; on an id-less pair the
        # per-half mint would diverge (#162), so it must refuse loudly rather than
        # silently violate the documented "already in parity" contract.
        de, en = self._born_split_with_vo(tmp_path)  # both halves id-less
        with pytest.raises(VoiceoverError, match="slide_id parity"):
            extract_voiceover_pair(de, en, mint_ids=False)

    def test_dry_run_and_real_report_same_ids(self, tmp_path: Path):
        # Per-half ids_generated is 0 on the paired path (the pre-mint owns id
        # minting, reported via ids_minted) — identical in dry-run and real, so a
        # dry-run preview does not over-report ids the real run won't produce.
        de, en = self._born_split_with_vo(tmp_path)
        dry = extract_voiceover_pair(de, en, dry_run=True)
        assert all(r.ids_generated == 0 for r in dry.results)
        real = extract_voiceover_pair(de, en)
        assert all(r.ids_generated == 0 for r in real.results)
        assert dry.ids_minted == real.ids_minted == 1  # one distinct slide_id

    def test_rejects_invalid_pair(self, tmp_path: Path):
        # Two same-language halves are not a valid de/en pair.
        a = tmp_path / "slides_x.de.py"
        b = tmp_path / "slides_y.de.py"
        a.write_text('# %% [markdown] lang="de" tags=["slide"]\n# ## A\n', encoding="utf-8")
        b.write_text('# %% [markdown] lang="de" tags=["slide"]\n# ## B\n', encoding="utf-8")
        with pytest.raises(VoiceoverError):
            extract_voiceover_pair(a, b)

    def test_no_voiceover_is_noop(self, tmp_path: Path):
        # Neither half has voiceover cells: do nothing — don't id-stamp either.
        de = tmp_path / "slides_x.de.py"
        en = tmp_path / "slides_x.en.py"
        de.write_text(
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Mein Thema\n', encoding="utf-8"
        )
        en.write_text('# %% [markdown] lang="en" tags=["slide"]\n# ## My Topic\n', encoding="utf-8")
        de_before, en_before = de.read_text(encoding="utf-8"), en.read_text(encoding="utf-8")

        result = extract_voiceover_pair(de, en)

        assert all(r.cells_extracted == 0 for r in result.results)
        assert not (tmp_path / "voiceover_x.de.py").exists()
        # No id-stamping side effect.
        assert de.read_text(encoding="utf-8") == de_before
        assert en.read_text(encoding="utf-8") == en_before

    def test_mint_ids_false_skips_premint(self, tmp_path: Path):
        # With ids already in parity, mint_ids=False just extracts both halves.
        de, en = self._born_split_with_vo(tmp_path)
        # Pre-id both halves to parity via the generative pass directly.
        from clm.slides.assign_ids import AssignOptions, assign_ids_in_split_pair

        assign_ids_in_split_pair(de, en, AssignOptions())
        result = extract_voiceover_pair(de, en, force=True, mint_ids=False, layout="sibling")

        assert result.ids_minted == 0
        assert _for_slide_set(tmp_path / "voiceover_x.de.py") == _for_slide_set(
            tmp_path / "voiceover_x.en.py"
        )


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
        extract_voiceover(slide_file, layout="sibling")

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
        extract_voiceover(f, layout="sibling")
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
        extract_voiceover(f, layout="sibling")
        comp = companion_path(f)
        # Rename the owning slide between extract and inline.
        t = f.read_text(encoding="utf-8").replace('slide_id="two"', 'slide_id="two-renamed"')
        f.write_text(t, encoding="utf-8", newline="\n")

        res = inline_voiceover(f)
        out = f.read_text(encoding="utf-8")

        assert res.unmatched_cells == 1
        assert res.relocated_cells == 0
        # Never inserted into the foreign group one — and, per the Tier-1 fix,
        # not stranded in the slide at all: it is retained in the companion
        # (recoverable) so the author can fix the slide_id and re-run inline.
        assert res.companion_retained is True
        assert "VO belongs to group TWO." not in out
        assert "VO belongs to group TWO." in comp.read_text(encoding="utf-8")

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


# ---------------------------------------------------------------------------
# #247 — a j2 cell embedded mid-slide-group is no longer an invisible anchor
# barrier. A voiceover authored after such a cell (e.g. an inline widget macro)
# must anchor *to* the j2 cell, so extract/merge/inline restore its exact
# position instead of re-inserting it before the j2.
# ---------------------------------------------------------------------------


def _anchor_for_body(comp: Path, needle: str) -> str | None:
    """The ``vo_anchor`` token of the companion cell whose body contains ``needle``."""
    for cell in parse_cells(comp.read_text(encoding="utf-8")):
        if needle in cell.content:
            m = re.search(r'vo_anchor="([^"]*)"', cell.header)
            return m.group(1) if m else None
    return None


# A real j2 cell header must start with ``# {{ `` or ``# j2 ``; ``widget(...)``
# stands in for any inline macro a deck might embed between content and its
# voiceover.
J2_MID_GROUP_DECK = """\
# %% [markdown] lang="de" tags=["slide"] slide_id="real"
# ## Real

# {{ widget("demo") }}

# %% [markdown] lang="de" tags=["voiceover"] slide_id="real"
# VO after the widget.

# %% [markdown] lang="de" tags=["slide"] slide_id="next"
# ## Next
"""


class TestMidGroupJ2Anchor:
    """#247 — mid-group j2 cells are anchorable predecessors / insert targets."""

    def test_extract_anchors_voiceover_to_mid_group_j2(self, tmp_path: Path):
        """The recorded anchor is an ``fp:`` of the j2 cell, not the content
        cell above it. The old predecessor walk skipped the j2 and mis-anchored
        to the heading, so the merge hoisted the voiceover above the j2."""
        f = tmp_path / "slides_w.py"
        f.write_text(J2_MID_GROUP_DECK, encoding="utf-8", newline="\n")

        extract_voiceover(f, layout="sibling")

        anchor = _anchor_for_body(companion_path(f), "VO after the widget.")
        assert anchor is not None and anchor.startswith("fp:")

    def test_merge_keeps_voiceover_after_mid_group_j2(self, tmp_path: Path):
        """The build path (merge) restores the voiceover after the j2 widget,
        byte-identically, with no anchor leak."""
        f = tmp_path / "slides_w.py"
        f.write_text(J2_MID_GROUP_DECK, encoding="utf-8", newline="\n")
        extract_voiceover(f, layout="sibling")

        merged, unmatched = merge_voiceover_text(
            f.read_text(encoding="utf-8"), companion_path(f).read_text(encoding="utf-8")
        )

        assert unmatched == []
        assert "vo_anchor" not in merged
        _assert_order(merged, "## Real", 'widget("demo")', "VO after the widget.", "## Next")
        assert _strip_for_slide(merged).strip() == J2_MID_GROUP_DECK.strip()

    def test_inline_round_trip_with_mid_group_j2_byte_identical(self, tmp_path: Path):
        """extract -> inline restores the deck exactly (the voiceover already
        carries its slide_id, so extract adds nothing) and reports no
        relocation."""
        f = tmp_path / "slides_w.py"
        f.write_text(J2_MID_GROUP_DECK, encoding="utf-8", newline="\n")

        extract_voiceover(f)
        res = inline_voiceover(f)

        assert res.relocated_cells == 0
        assert res.unmatched_cells == 0
        assert res.companion_deleted
        assert f.read_text(encoding="utf-8") == J2_MID_GROUP_DECK

    def test_voiceovers_around_mid_group_j2_keep_order(self, tmp_path: Path):
        """``content / VO_A / j2 widget / VO_B``: VO_A anchors to the heading,
        VO_B to the widget, so both keep their slots across the round-trip."""
        deck = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="s"\n# ## Slide\n\n'
            '# %% [markdown] lang="de" tags=["voiceover"]\n# VO before the widget.\n\n'
            '# {{ widget("demo") }}\n\n'
            '# %% [markdown] lang="de" tags=["voiceover"]\n# VO after the widget.\n\n'
            '# %% tags=["keep"]\nx = 1\n'
        )
        out, res = _round_trip(tmp_path, deck)

        assert res.relocated_cells == 0
        _assert_order(
            out,
            "## Slide",
            "VO before the widget.",
            'widget("demo")',
            "VO after the widget.",
            "x = 1",
        )

    def test_duplicate_mid_group_j2_occ_disambiguates(self, tmp_path: Path):
        """Two identical j2 widgets in one group: a VO after the second anchors
        to the second via the occurrence ordinal, not the first — proving the
        ordinal counts j2 cells consistently at extract and merge time."""
        deck = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="s"\n# ## Slide\n\n'
            '# {{ widget("x") }}\n\n'
            '# %% [markdown] lang="de" tags=["voiceover"]\n# VO after the FIRST widget.\n\n'
            '# {{ widget("x") }}\n\n'
            '# %% [markdown] lang="de" tags=["voiceover"]\n# VO after the SECOND widget.\n'
        )
        out, res = _round_trip(tmp_path, deck)

        assert res.relocated_cells == 0
        w1 = out.index('widget("x")')
        w2 = out.index('widget("x")', w1 + 1)
        v1 = out.index("VO after the FIRST widget.")
        v2 = out.index("VO after the SECOND widget.")
        assert w1 < v1 < w2 < v2

    def test_legacy_anchorless_voiceover_lands_after_mid_group_j2(self):
        """A *legacy* companion (``for_slide``, no ``vo_anchor``) for a slide
        with a mid-group j2 now lands at the true group end — after the j2 —
        rather than being stranded before it. The group-end fallback no longer
        breaks on a j2 continuation cell (#247)."""
        slide = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="real"\n# ## Real\n\n'
            '# {{ widget("demo") }}\n\n'
            '# %% [markdown] lang="de" tags=["slide"] slide_id="next"\n# ## Next\n'
        )
        legacy = '# %% [markdown] lang="de" tags=["voiceover"] for_slide="real"\n# Legacy VO.\n'

        merged, unmatched = merge_voiceover_text(slide, legacy)

        assert unmatched == []
        _assert_order(merged, "## Real", 'widget("demo")', "Legacy VO.", "## Next")

    def test_title_greeting_under_macro_still_uses_tm_anchor(self, tmp_path: Path):
        """The j2 title macro is now an eligible predecessor (it is a j2 cell),
        yet it keeps its dedicated ``tm:`` anchor — not an ``fp:`` of the
        ``header()`` call — so the #246 title behaviour is preserved. This guards
        the interaction between the #247 j2-aware walk and the #246 macro anchor.
        """
        slide = tmp_path / "slides_015.en.py"
        slide.write_text(TITLE_BEFORE_KEEP_EN, encoding="utf-8")

        extract_voiceover(slide, force=True, layout="sibling")

        comp = slide.with_name("voiceover_015.en.py")
        assert _vo_anchor_of(comp) == "tm:title#0"

    def test_title_slide_widget_after_macro_anchors_via_fp(self, tmp_path: Path):
        """A j2 widget on the title slide (after the header macro) followed by a
        greeting: the greeting anchors to the *widget* via ``fp:`` (the widget is
        not the title macro) and round-trips — exercising the now-j2-aware title
        group bounds and candidate set."""
        deck = """\
# j2 from 'macros.j2' import header_en
# {{ header_en("T") }}

# {{ widget("demo") }}

# %% [markdown] lang="en" tags=["voiceover"] slide_id="title"
# - Greeting after widget.

# %% [markdown] lang="en" tags=["slide"] slide_id="real"
# - Real
"""
        slide = tmp_path / "slides_015.en.py"
        slide.write_text(deck, encoding="utf-8")

        extract_voiceover(slide, force=True, layout="sibling")
        comp = slide.with_name("voiceover_015.en.py")
        anchor = _vo_anchor_of(comp)
        assert anchor is not None and anchor.startswith("fp:")

        merged, unmatched = merge_voiceover_text(
            slide.read_text(encoding="utf-8"), comp.read_text(encoding="utf-8")
        )
        assert unmatched == []
        _assert_order(
            merged,
            'header_en("T")',
            'widget("demo")',
            "Greeting after widget.",
            "- Real",
        )
        assert _strip_for_slide(merged).strip() == deck.strip()

    def test_inserting_different_j2_above_anchor_does_not_misplace(self, tmp_path: Path):
        """A j2 anchor must track its *specific* macro across edits, not degrade
        to 'the N-th j2 cell'. Inserting an unrelated j2 cell above the anchored
        widget between extract and the build merge must not hoist the voiceover
        in front of its true predecessor — the fingerprint folds in the j2
        header so distinct macros get distinct anchors (#247)."""
        deck = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="s"\n# ## S\n\n'
            '# {{ realwidget("anchor") }}\n\n'
            '# %% [markdown] lang="de" tags=["voiceover"] slide_id="s"\n# VO for the REAL widget.\n\n'
            '# %% [markdown] lang="de" tags=["slide"] slide_id="next"\n# ## Next\n'
        )
        f = tmp_path / "slides_e.py"
        f.write_text(deck, encoding="utf-8", newline="\n")
        extract_voiceover(f, layout="sibling")
        comp = companion_path(f).read_text(encoding="utf-8")

        # Author inserts an unrelated j2 widget ABOVE the anchored one.
        edited = f.read_text(encoding="utf-8").replace(
            '# {{ realwidget("anchor") }}',
            '# {{ newwidget("inserted") }}\n\n# {{ realwidget("anchor") }}',
        )
        merged, unmatched = merge_voiceover_text(edited, comp)

        assert unmatched == []
        # The voiceover stays after its OWN widget, not the freshly-inserted one.
        _assert_order(
            merged,
            'newwidget("inserted")',
            'realwidget("anchor")',
            "VO for the REAL widget.",
            "## Next",
        )

    def test_reordering_distinct_j2_widgets_does_not_swap_narration(self, tmp_path: Path):
        """Two distinct widgets, each with its own voiceover: swapping the
        widgets between extract and inline moves each narration WITH its widget
        rather than leaving them pinned by ordinal. Guards the shared-fingerprint
        defect the #247 review caught (all j2 cells once hashed identically)."""
        deck = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="s"\n# ## S\n\n'
            '# {{ widget("alpha") }}\n\n'
            '# %% [markdown] lang="de" tags=["voiceover"] slide_id="s"\n# VO for ALPHA.\n\n'
            '# {{ widget("beta") }}\n\n'
            '# %% [markdown] lang="de" tags=["voiceover"] slide_id="s"\n# VO for BETA.\n'
        )
        f = tmp_path / "slides_r.py"
        f.write_text(deck, encoding="utf-8", newline="\n")
        extract_voiceover(f)

        # Swap the two widgets in the (voiceover-stripped) slide file.
        sentinel = "@@SWAP@@"
        t = f.read_text(encoding="utf-8")
        t = (
            t.replace('# {{ widget("alpha") }}', sentinel)
            .replace('# {{ widget("beta") }}', '# {{ widget("alpha") }}')
            .replace(sentinel, '# {{ widget("beta") }}')
        )
        f.write_text(t, encoding="utf-8", newline="\n")

        res = inline_voiceover(f)
        out = f.read_text(encoding="utf-8")

        assert res.unmatched_cells == 0
        # Each narration tracks its widget across the swap (not swapped by ordinal).
        _assert_order(
            out,
            'widget("beta")',
            "VO for BETA.",
            'widget("alpha")',
            "VO for ALPHA.",
        )
