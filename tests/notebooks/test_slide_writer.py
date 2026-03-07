"""Tests for the slide writer."""

from __future__ import annotations

import pytest

from clm.notebooks.slide_writer import format_notes_cell, update_notes

SIMPLE_FILE = """\
# j2 from 'macros.j2' import header
# {{ header("Kurs Titel", "Course Title") }}

# %% [markdown] lang="de" tags=["slide"]
# ## Erste Folie
#
# - Punkt eins

# %% tags=["subslide"]
print("hello")

# %% [markdown] lang="de" tags=["slide"]
# ## Zweite Folie
#
# - Punkt zwei

# %% tags=["subslide"]
x = 42
"""

FILE_WITH_NOTES = """\
# j2 from 'macros.j2' import header
# {{ header("Titel", "Title") }}

# %% [markdown] lang="de" tags=["slide"]
# ## Erste Folie
#
# - Punkt eins

# %% [markdown] lang="de" tags=["notes"]
#
# - Alte Notizen hier.

# %% [markdown] lang="de" tags=["slide"]
# ## Zweite Folie
#
# - Punkt zwei
"""

BILINGUAL_FILE = """\
# j2 from 'macros.j2' import header
# {{ header("Titel", "Title") }}

# %% [markdown] lang="de" tags=["slide"]
# ## Deutsche Folie

# %% [markdown] lang="en" tags=["slide"]
# ## English Slide

# %% [markdown] lang="de" tags=["slide"]
# ## Zweite Deutsche Folie
"""


class TestFormatNotesCell:
    def test_simple_text(self):
        result = format_notes_cell("First sentence.\nSecond sentence.", "de")
        assert '# %% [markdown] lang="de" tags=["notes"]' in result
        assert "# - First sentence." in result
        assert "# - Second sentence." in result

    def test_already_has_bullets(self):
        result = format_notes_cell("- Already a bullet.\n- Another bullet.", "en")
        assert "# - Already a bullet." in result
        assert "# - Another bullet." in result

    def test_revisited_marker(self):
        result = format_notes_cell("First part.\n\n**[Revisited]**\nSecond part.", "de")
        assert "# - First part." in result
        assert "# **[Revisited]**" in result
        assert "# - Second part." in result

    def test_blank_lines_preserved(self):
        result = format_notes_cell("Part one.\n\nPart two.", "de")
        lines = result.split("\n")
        # Should have a blank comment line (#) for the empty line
        assert "#" in lines

    def test_lang_in_header(self):
        result_de = format_notes_cell("text", "de")
        result_en = format_notes_cell("text", "en")
        assert 'lang="de"' in result_de
        assert 'lang="en"' in result_en


class TestUpdateNotes:
    def test_insert_notes_into_simple_file(self):
        result = update_notes(SIMPLE_FILE, {1: "Notes for slide 1."}, "de")
        assert "# - Notes for slide 1." in result
        # Original content preserved
        assert "## Erste Folie" in result
        assert "## Zweite Folie" in result
        assert 'print("hello")' in result

    def test_insert_notes_for_multiple_slides(self):
        result = update_notes(
            SIMPLE_FILE,
            {1: "Notes for first.", 2: "Notes for second."},
            "de",
        )
        assert "# - Notes for first." in result
        assert "# - Notes for second." in result

    def test_replace_existing_notes(self):
        result = update_notes(
            FILE_WITH_NOTES,
            {1: "Neue Notizen."},
            "de",
        )
        assert "# - Neue Notizen." in result
        assert "Alte Notizen hier" not in result
        # Other content preserved
        assert "## Erste Folie" in result
        assert "## Zweite Folie" in result

    def test_empty_notes_map_returns_unchanged(self):
        result = update_notes(SIMPLE_FILE, {}, "de")
        assert result == SIMPLE_FILE

    def test_nonexistent_slide_index_warns(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING):
            result = update_notes(SIMPLE_FILE, {99: "Nothing."}, "de")
        assert "not found" in caplog.text
        # File should be unchanged
        assert result == SIMPLE_FILE

    def test_notes_cell_has_correct_header(self):
        result = update_notes(SIMPLE_FILE, {1: "Test."}, "de")
        assert '# %% [markdown] lang="de" tags=["notes"]' in result

    def test_preserves_other_language_slides(self):
        result = update_notes(BILINGUAL_FILE, {1: "German notes."}, "de")
        assert "# - German notes." in result
        # English slide should be untouched
        assert "## English Slide" in result

    def test_slide_indexing_with_header(self):
        # Header is index 0, first slide is index 1
        result = update_notes(SIMPLE_FILE, {1: "First slide notes."}, "de")
        # Notes should appear after the first slide group, not the header
        lines = result.split("\n")
        notes_line = next(i for i, line in enumerate(lines) if "First slide notes" in line)
        header_line = next(i for i, line in enumerate(lines) if "Erste Folie" in line)
        zweite_line = next(i for i, line in enumerate(lines) if "Zweite Folie" in line)
        assert header_line < notes_line < zweite_line
