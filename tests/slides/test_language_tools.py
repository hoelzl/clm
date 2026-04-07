"""Tests for clm.slides.language_tools."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from clm.slides.language_tools import get_language_view


def _write_slide(tmp_path: Path, name: str, content: str) -> Path:
    """Write a slide file and return its path."""
    p = tmp_path / name
    p.write_text(dedent(content), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Basic language filtering
# ---------------------------------------------------------------------------


BILINGUAL_SLIDE = """\
# j2 from 'macros.j2' import header
# {{ header("Methoden", "Methods") }}

# %% [markdown] lang="de" tags=["slide"]
# ## Methoden
#
# Klassen können Methoden enthalten.

# %% [markdown] lang="en" tags=["slide"]
# ## Methods
#
# Classes can contain methods.

# %% tags=["keep"]
class MyClass:
    def method(self):
        print("hello")

# %% [markdown] lang="de" tags=["subslide"]
# ## Beispiel
#
# Ein Beispiel.

# %% [markdown] lang="en" tags=["subslide"]
# ## Example
#
# An example.

# %%
my_object = MyClass()
my_object.method()
"""


class TestLanguageFiltering:
    def test_german_view_excludes_english(self, tmp_path):
        p = _write_slide(tmp_path, "slides_methods.py", BILINGUAL_SLIDE)
        result = get_language_view(p, "de")
        assert 'lang="de"' in result
        assert 'lang="en"' not in result

    def test_english_view_excludes_german(self, tmp_path):
        p = _write_slide(tmp_path, "slides_methods.py", BILINGUAL_SLIDE)
        result = get_language_view(p, "en")
        assert 'lang="en"' in result
        assert 'lang="de"' not in result

    def test_language_neutral_cells_included(self, tmp_path):
        p = _write_slide(tmp_path, "slides_methods.py", BILINGUAL_SLIDE)
        result = get_language_view(p, "de")
        assert "class MyClass:" in result
        assert "my_object = MyClass()" in result

    def test_j2_header_included(self, tmp_path):
        p = _write_slide(tmp_path, "slides_methods.py", BILINGUAL_SLIDE)
        result = get_language_view(p, "de")
        assert "# j2 from 'macros.j2' import header" in result
        assert '# {{ header("Methoden", "Methods") }}' in result

    def test_j2_header_has_no_line_annotation(self, tmp_path):
        p = _write_slide(tmp_path, "slides_methods.py", BILINGUAL_SLIDE)
        result = get_language_view(p, "de")
        lines = result.split("\n")
        # The j2 header lines should not be preceded by [original line]
        j2_idx = next(i for i, line in enumerate(lines) if line.startswith("# j2 "))
        assert j2_idx == 0 or "original line" not in lines[j2_idx - 1]


# ---------------------------------------------------------------------------
# Line annotations
# ---------------------------------------------------------------------------


class TestLineAnnotations:
    def test_annotations_present_for_non_j2_cells(self, tmp_path):
        p = _write_slide(tmp_path, "slides_methods.py", BILINGUAL_SLIDE)
        result = get_language_view(p, "de")
        assert "# [original line" in result

    def test_annotation_line_numbers_match_source(self, tmp_path):
        p = _write_slide(tmp_path, "slides_methods.py", BILINGUAL_SLIDE)
        result = get_language_view(p, "de")

        # The first DE markdown cell starts at line 4
        assert "# [original line 4]" in result

    def test_language_neutral_code_cell_annotated(self, tmp_path):
        p = _write_slide(tmp_path, "slides_methods.py", BILINGUAL_SLIDE)
        result = get_language_view(p, "en")
        # The "tags=["keep"]" code cell is at line 14 in the source
        assert "# [original line 14]" in result


# ---------------------------------------------------------------------------
# Voiceover / notes filtering
# ---------------------------------------------------------------------------


SLIDE_WITH_VOICEOVER = """\
# %% [markdown] lang="de" tags=["slide"]
# ## Thema
#
# Inhalt.

# %% [markdown] lang="de" tags=["voiceover"]
# Voiceover-Text auf Deutsch.

# %% [markdown] lang="en" tags=["slide"]
# ## Topic
#
# Content.

# %% [markdown] lang="en" tags=["voiceover"]
# Voiceover text in English.

# %% tags=["keep"]
x = 1
"""


class TestVoiceoverFiltering:
    def test_voiceover_excluded_by_default(self, tmp_path):
        p = _write_slide(tmp_path, "slides_vo.py", SLIDE_WITH_VOICEOVER)
        result = get_language_view(p, "de")
        assert "Voiceover-Text" not in result
        assert "Inhalt." in result

    def test_voiceover_included_when_requested(self, tmp_path):
        p = _write_slide(tmp_path, "slides_vo.py", SLIDE_WITH_VOICEOVER)
        result = get_language_view(p, "de", include_voiceover=True)
        assert "Voiceover-Text" in result

    def test_voiceover_other_language_still_excluded(self, tmp_path):
        p = _write_slide(tmp_path, "slides_vo.py", SLIDE_WITH_VOICEOVER)
        result = get_language_view(p, "de", include_voiceover=True)
        assert "Voiceover text in English" not in result


SLIDE_WITH_NOTES = """\
# %% [markdown] lang="de" tags=["slide"]
# ## Thema

# %% [markdown] lang="de" tags=["notes"]
# Notizen auf Deutsch.

# %% [markdown] lang="en" tags=["slide"]
# ## Topic

# %% [markdown] lang="en" tags=["notes"]
# Notes in English.

# %% tags=["keep"]
x = 1
"""


class TestNotesFiltering:
    def test_notes_excluded_by_default(self, tmp_path):
        p = _write_slide(tmp_path, "slides_notes.py", SLIDE_WITH_NOTES)
        result = get_language_view(p, "en")
        assert "Notes in English" not in result

    def test_notes_included_when_requested(self, tmp_path):
        p = _write_slide(tmp_path, "slides_notes.py", SLIDE_WITH_NOTES)
        result = get_language_view(p, "en", include_notes=True)
        assert "Notes in English" in result

    def test_notes_other_language_excluded(self, tmp_path):
        p = _write_slide(tmp_path, "slides_notes.py", SLIDE_WITH_NOTES)
        result = get_language_view(p, "en", include_notes=True)
        assert "Notizen auf Deutsch" not in result


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_monolingual_file(self, tmp_path):
        content = """\
        # %% [markdown] tags=["slide"]
        # ## Only one language
        #
        # No lang attribute at all.

        # %% tags=["keep"]
        x = 1
        """
        p = _write_slide(tmp_path, "slides_mono.py", content)
        result = get_language_view(p, "de")
        # All cells are language-neutral, so all should be included
        assert "Only one language" in result
        assert "x = 1" in result

    def test_empty_file(self, tmp_path):
        p = _write_slide(tmp_path, "slides_empty.py", "")
        result = get_language_view(p, "de")
        assert result == ""

    def test_code_only_file(self, tmp_path):
        content = """\
        # %% tags=["keep"]
        x = 1

        # %%
        y = 2
        """
        p = _write_slide(tmp_path, "slides_code.py", content)
        result = get_language_view(p, "en")
        assert "x = 1" in result
        assert "y = 2" in result

    def test_voiceover_and_notes_both_requested(self, tmp_path):
        content = """\
        # %% [markdown] lang="de" tags=["slide"]
        # ## Thema

        # %% [markdown] lang="de" tags=["voiceover"]
        # VO text.

        # %% [markdown] lang="de" tags=["notes"]
        # Notes text.

        # %% tags=["keep"]
        x = 1
        """
        p = _write_slide(tmp_path, "slides_both.py", content)
        result = get_language_view(p, "de", include_voiceover=True, include_notes=True)
        assert "VO text." in result
        assert "Notes text." in result

    def test_preserves_raw_cell_content(self, tmp_path):
        """The output should contain the original file text, not reconstructed text."""
        content = """\
        # %% [markdown] lang="de" tags=["slide"]
        # ## Überschrift
        #
        # Etwas **fetter** Text.
        """
        p = _write_slide(tmp_path, "slides_raw.py", content)
        result = get_language_view(p, "de")
        assert "# Etwas **fetter** Text." in result


# ---------------------------------------------------------------------------
# Integration with fixtures
# ---------------------------------------------------------------------------


class TestWithFixtures:
    """Test against the shared fixture files."""

    @pytest.fixture()
    def fixtures_dir(self) -> Path:
        return Path(__file__).parent / "fixtures"

    def test_well_formed_de(self, fixtures_dir):
        p = fixtures_dir / "well_formed.py"
        result = get_language_view(p, "de")
        assert "Einführung" in result
        assert "Introduction" not in result
        assert "x = 1" in result  # language-neutral code

    def test_well_formed_en(self, fixtures_dir):
        p = fixtures_dir / "well_formed.py"
        result = get_language_view(p, "en")
        assert "Introduction" in result
        assert "Einführung" not in result
        assert "x = 1" in result

    def test_with_voiceover_excludes_vo_by_default(self, fixtures_dir):
        p = fixtures_dir / "with_voiceover.py"
        result = get_language_view(p, "de")
        assert "Thema Eins" in result
        assert "Voiceover-Text" not in result

    def test_with_voiceover_includes_vo_when_requested(self, fixtures_dir):
        p = fixtures_dir / "with_voiceover.py"
        result = get_language_view(p, "de", include_voiceover=True)
        assert "Voiceover-Text" in result
        assert "voiceover text for topic one" not in result  # EN excluded


# ===========================================================================
# suggest_sync tests
# ===========================================================================

import subprocess

from clm.slides.language_tools import SyncResult, SyncSuggestion, suggest_sync


def _init_git_repo(tmp_path: Path) -> Path:
    """Initialize a git repo in tmp_path and return it."""
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(tmp_path),
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(tmp_path),
        capture_output=True,
        check=True,
    )
    return tmp_path


def _commit_file(repo: Path, file_path: Path, content: str) -> None:
    """Write content to file and commit it."""
    file_path.write_text(dedent(content), encoding="utf-8")
    subprocess.run(
        ["git", "add", str(file_path)],
        cwd=str(repo),
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "test commit"],
        cwd=str(repo),
        capture_output=True,
        check=True,
    )


BILINGUAL_ORIGINAL = """\
# %% [markdown] lang="de" tags=["slide"]
# ## Methoden
#
# Klassen können Methoden enthalten.

# %% [markdown] lang="en" tags=["slide"]
# ## Methods
#
# Classes can contain methods.

# %% tags=["keep"]
class MyClass:
    pass
"""

BILINGUAL_DE_MODIFIED = """\
# %% [markdown] lang="de" tags=["slide"]
# ## Methoden und Attribute
#
# Klassen können Methoden und Attribute enthalten.

# %% [markdown] lang="en" tags=["slide"]
# ## Methods
#
# Classes can contain methods.

# %% tags=["keep"]
class MyClass:
    pass
"""


class TestSuggestSyncBasic:
    def test_no_changes_returns_in_sync(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        slide = repo / "slides_test.py"
        _commit_file(repo, slide, BILINGUAL_ORIGINAL)

        result = suggest_sync(slide)
        assert not result.sync_needed
        assert result.unmodified_pairs == 1
        assert len(result.suggestions) == 0

    def test_de_modified_suggests_en_update(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        slide = repo / "slides_test.py"
        _commit_file(repo, slide, BILINGUAL_ORIGINAL)
        # Modify only the DE cell
        slide.write_text(dedent(BILINGUAL_DE_MODIFIED), encoding="utf-8")

        result = suggest_sync(slide, source_language="de")
        assert result.sync_needed
        assert result.source_language == "de"
        assert result.target_language == "en"
        assert len(result.suggestions) == 1
        assert result.suggestions[0].type == "modified"

    def test_auto_detects_source_language(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        slide = repo / "slides_test.py"
        _commit_file(repo, slide, BILINGUAL_ORIGINAL)
        slide.write_text(dedent(BILINGUAL_DE_MODIFIED), encoding="utf-8")

        result = suggest_sync(slide)
        # DE was modified, so source should be auto-detected as DE
        assert result.source_language == "de"
        assert result.sync_needed

    def test_result_dataclass_fields(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        slide = repo / "slides_test.py"
        _commit_file(repo, slide, BILINGUAL_ORIGINAL)
        slide.write_text(dedent(BILINGUAL_DE_MODIFIED), encoding="utf-8")

        result = suggest_sync(slide, source_language="de")
        assert result.file == str(slide)
        assert result.pairing_method == "positional"

    def test_both_languages_modified_not_flagged(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        slide = repo / "slides_test.py"
        _commit_file(repo, slide, BILINGUAL_ORIGINAL)

        both_modified = """\
# %% [markdown] lang="de" tags=["slide"]
# ## Neue Methoden
#
# Überarbeitet.

# %% [markdown] lang="en" tags=["slide"]
# ## New Methods
#
# Revised.

# %% tags=["keep"]
class MyClass:
    pass
"""
        slide.write_text(both_modified, encoding="utf-8")

        result = suggest_sync(slide, source_language="de")
        # Both changed => considered in sync
        assert not result.sync_needed
        assert result.unmodified_pairs == 1


class TestSuggestSyncAdded:
    def test_added_cell_suggests_target(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        slide = repo / "slides_test.py"
        _commit_file(repo, slide, BILINGUAL_ORIGINAL)

        with_added = """\
# %% [markdown] lang="de" tags=["slide"]
# ## Methoden
#
# Klassen können Methoden enthalten.

# %% [markdown] lang="de" tags=["subslide"]
# ## Neues Thema
#
# Neue Inhalte.

# %% [markdown] lang="en" tags=["slide"]
# ## Methods
#
# Classes can contain methods.

# %% tags=["keep"]
class MyClass:
    pass
"""
        slide.write_text(with_added, encoding="utf-8")

        result = suggest_sync(slide, source_language="de")
        assert result.sync_needed
        added = [s for s in result.suggestions if s.type == "added"]
        assert len(added) == 1
        assert "DE" in added[0].suggestion


class TestSuggestSyncDeleted:
    def test_deleted_cell_suggests_removal(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        slide = repo / "slides_test.py"

        original_two_pairs = """\
# %% [markdown] lang="de" tags=["slide"]
# ## Methoden
#
# Text.

# %% [markdown] lang="de" tags=["subslide"]
# ## Beispiel

# %% [markdown] lang="en" tags=["slide"]
# ## Methods
#
# Text.

# %% [markdown] lang="en" tags=["subslide"]
# ## Example

# %% tags=["keep"]
x = 1
"""
        _commit_file(repo, slide, original_two_pairs)

        # Delete one DE cell
        after_delete = """\
# %% [markdown] lang="de" tags=["slide"]
# ## Methoden
#
# Text.

# %% [markdown] lang="en" tags=["slide"]
# ## Methods
#
# Text.

# %% [markdown] lang="en" tags=["subslide"]
# ## Example

# %% tags=["keep"]
x = 1
"""
        slide.write_text(after_delete, encoding="utf-8")

        result = suggest_sync(slide, source_language="de")
        assert result.sync_needed
        deleted = [s for s in result.suggestions if s.type == "deleted"]
        assert len(deleted) == 1
        assert "deleted" in deleted[0].suggestion.lower()


class TestSuggestSyncSlideId:
    def test_slide_id_pairing(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        slide = repo / "slides_test.py"

        original = """\
# %% [markdown] lang="de" tags=["slide"] slide_id="intro"
# ## Einführung

# %% [markdown] lang="en" tags=["slide"] slide_id="intro"
# ## Introduction

# %% tags=["keep"]
x = 1
"""
        _commit_file(repo, slide, original)

        modified = """\
# %% [markdown] lang="de" tags=["slide"] slide_id="intro"
# ## Neue Einführung

# %% [markdown] lang="en" tags=["slide"] slide_id="intro"
# ## Introduction

# %% tags=["keep"]
x = 1
"""
        slide.write_text(modified, encoding="utf-8")

        result = suggest_sync(slide, source_language="de")
        assert result.pairing_method == "slide_id"
        assert result.sync_needed
        assert len(result.suggestions) == 1
        assert result.suggestions[0].slide_id == "intro"

    def test_mixed_pairing(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        slide = repo / "slides_test.py"

        original = """\
# %% [markdown] lang="de" tags=["slide"] slide_id="intro"
# ## Einführung

# %% [markdown] lang="de" tags=["subslide"]
# ## Ohne ID

# %% [markdown] lang="en" tags=["slide"] slide_id="intro"
# ## Introduction

# %% [markdown] lang="en" tags=["subslide"]
# ## Without ID

# %% tags=["keep"]
x = 1
"""
        _commit_file(repo, slide, original)

        result = suggest_sync(slide, source_language="de")
        assert result.pairing_method == "mixed"


class TestSuggestSyncUntracked:
    def test_untracked_file_treated_as_all_new(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        slide = repo / "slides_new.py"
        slide.write_text(BILINGUAL_ORIGINAL, encoding="utf-8")
        # Not committed — untracked

        result = suggest_sync(slide, source_language="de")
        assert result.source_language == "de"
        # All cells are new, so the DE cell is "modified" (content differs from empty HEAD)
        # and the EN cell is unmodified (also new but it's the target)
        # The exact behavior depends on the pairing, but it should not crash
        assert isinstance(result, SyncResult)

    def test_no_git_repo(self, tmp_path):
        # Not a git repo at all
        slide = tmp_path / "slides_test.py"
        slide.write_text(BILINGUAL_ORIGINAL, encoding="utf-8")

        result = suggest_sync(slide, source_language="de")
        # Should handle gracefully
        assert result.source_language == "de"
        assert isinstance(result, SyncResult)
