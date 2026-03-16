"""Tests for the slide parser."""

from __future__ import annotations

import textwrap

import pytest

from clm.notebooks.slide_parser import (
    Cell,
    CellMetadata,
    SlideGroup,
    group_slides,
    parse_cell_header,
    parse_cells,
)

# ---------------------------------------------------------------------------
# parse_cell_header
# ---------------------------------------------------------------------------


class TestParseCellHeader:
    def test_simple_code_cell(self):
        meta = parse_cell_header("# %%")
        assert meta.cell_type == "code"
        assert meta.lang is None
        assert meta.tags == []

    def test_markdown_cell_with_lang_and_tags(self):
        meta = parse_cell_header('# %% [markdown] lang="de" tags=["slide"]')
        assert meta.cell_type == "markdown"
        assert meta.lang == "de"
        assert meta.tags == ["slide"]
        assert meta.is_slide is True
        assert meta.is_subslide is False

    def test_subslide_tag(self):
        meta = parse_cell_header('# %% [markdown] lang="en" tags=["subslide"]')
        assert meta.cell_type == "markdown"
        assert meta.lang == "en"
        assert meta.is_subslide is True
        assert meta.is_slide_start is True

    def test_notes_tag(self):
        meta = parse_cell_header('# %% [markdown] lang="de" tags=["notes"]')
        assert meta.is_narrative is True
        assert meta.is_slide_start is False

    def test_voiceover_tag(self):
        meta = parse_cell_header('# %% [markdown] lang="de" tags=["voiceover"]')
        assert meta.is_narrative is True
        assert meta.is_slide_start is False

    def test_multiple_tags(self):
        meta = parse_cell_header('# %% [markdown] lang="de" tags=["subslide", "keep"]')
        assert meta.tags == ["subslide", "keep"]
        assert meta.is_subslide is True

    def test_code_cell_with_tags(self):
        meta = parse_cell_header('# %% tags=["keep"]')
        assert meta.cell_type == "code"
        assert meta.tags == ["keep"]

    def test_code_cell_with_lang(self):
        meta = parse_cell_header('# %% lang="de"')
        assert meta.cell_type == "code"
        assert meta.lang == "de"

    def test_j2_import(self):
        meta = parse_cell_header("# j2 from 'macros.j2' import header")
        assert meta.is_j2 is True
        assert meta.cell_type == "j2"

    def test_j2_header_call(self):
        meta = parse_cell_header('# {{ header("Funktionen", "Functions") }}')
        assert meta.is_j2 is True

    def test_slide_type_property(self):
        assert parse_cell_header('# %% [markdown] tags=["slide"]').slide_type == "slide"
        assert parse_cell_header('# %% [markdown] tags=["subslide"]').slide_type == "subslide"
        assert parse_cell_header('# %% [markdown] tags=["notes"]').slide_type is None
        assert parse_cell_header("# %%").slide_type is None


# ---------------------------------------------------------------------------
# parse_cells
# ---------------------------------------------------------------------------


class TestParseCells:
    def test_simple_file(self):
        text = textwrap.dedent("""\
            # %% [markdown] lang="de" tags=["slide"]
            # # Title
            #
            # Some content.

            # %%
            x = 42

            # %% [markdown] lang="de" tags=["notes"]
            #
            # - Speaker notes here.
        """)
        cells = parse_cells(text)
        assert len(cells) == 3
        assert cells[0].metadata.is_slide is True
        assert cells[0].line_number == 1
        assert "Title" in cells[0].content
        assert cells[1].metadata.cell_type == "code"
        assert "x = 42" in cells[1].content
        assert cells[2].metadata.is_narrative is True

    def test_j2_directives_are_cells(self):
        text = textwrap.dedent("""\
            # j2 from 'macros.j2' import header
            # {{ header("Titel", "Title") }}

            # %% [markdown] lang="de" tags=["slide"]
            # # First Slide
        """)
        cells = parse_cells(text)
        assert len(cells) == 3
        assert cells[0].metadata.is_j2 is True
        assert cells[1].metadata.is_j2 is True
        assert cells[2].metadata.is_slide is True

    def test_preserves_content(self):
        text = textwrap.dedent("""\
            # %% [markdown] lang="de" tags=["slide"]
            # # Heading
            #
            # - Bullet one
            # - Bullet two

            # %% tags=["keep"]
            def foo():
                return 42
        """)
        cells = parse_cells(text)
        assert "Bullet one" in cells[0].content
        assert "Bullet two" in cells[0].content
        assert "def foo():" in cells[1].content


# ---------------------------------------------------------------------------
# group_slides
# ---------------------------------------------------------------------------


SAMPLE_SLIDES = textwrap.dedent("""\
    # j2 from 'macros.j2' import header
    # {{ header("Funktionen", "Functions") }}

    # %% [markdown] lang="de" tags=["slide"]
    # # Funktionsdefinition
    #
    # - Schlüsselwort `def`
    # - Name der Funktion

    # %% [markdown] lang="en" tags=["slide"]
    # # Function definition
    #
    # - Keyword `def`
    # - Function name

    # %% tags=["subslide"]
    def pythagoras(a, b):
        c = (a**2 + b**2) ** 0.5
        return c

    # %%
    pythagoras(3, 4)

    # %% [markdown] lang="de" tags=["notes"]
    #
    # - Jetzt haben wir die Funktion definiert.
    # - Hier ist eine Zusammenfassung.

    # %% [markdown] lang="en" tags=["notes"]
    #
    # - Now we have defined the function.
    # - Here is a summary.

    # %% [markdown] lang="de" tags=["subslide"]
    # ## Funktionsaufruf
    #
    # - Name der Funktion
    # - Argumente in Klammern

    # %% [markdown] lang="en" tags=["subslide"]
    # ## Function call
    #
    # - Function name
    # - Arguments in brackets

    # %%
    pythagoras(1, 1)
""")


class TestGroupSlides:
    def test_groups_german_slides(self):
        cells = parse_cells(SAMPLE_SLIDES)
        groups = group_slides(cells, "de", include_header=True)

        # Header + 3 slide groups:
        # [0] header, [1] Funktionsdefinition (slide),
        # [2] pythagoras code (subslide, lang-neutral), [3] Funktionsaufruf (subslide)
        assert len(groups) == 4
        assert groups[0].slide_type == "header"
        assert groups[0].title == "Funktionen"
        assert groups[1].title == "Funktionsdefinition"
        assert groups[2].slide_type == "subslide"  # code subslide
        assert groups[3].title == "Funktionsaufruf"

    def test_groups_english_slides(self):
        cells = parse_cells(SAMPLE_SLIDES)
        groups = group_slides(cells, "en", include_header=True)

        assert len(groups) == 4
        assert groups[0].title == "Functions"
        assert groups[1].title == "Function definition"
        assert groups[3].title == "Function call"

    def test_header_excluded(self):
        cells = parse_cells(SAMPLE_SLIDES)
        groups = group_slides(cells, "de", include_header=False)

        assert len(groups) == 3
        assert groups[0].title == "Funktionsdefinition"

    def test_notes_attached_to_preceding_subslide(self):
        cells = parse_cells(SAMPLE_SLIDES)
        groups = group_slides(cells, "de", include_header=True)

        # Notes come after the code subslide [2], so they attach there
        assert groups[1].has_notes is False  # Funktionsdefinition: no notes
        assert groups[2].has_notes is True  # code subslide: has notes
        assert "Funktion definiert" in groups[2].notes_text
        assert groups[3].has_notes is False  # Funktionsaufruf: no notes

    def test_notes_language_filtering(self):
        cells = parse_cells(SAMPLE_SLIDES)
        de_groups = group_slides(cells, "de", include_header=False)
        en_groups = group_slides(cells, "en", include_header=False)

        # Notes attach to the code subslide (index 1 without header)
        assert "Funktion definiert" in de_groups[1].notes_text
        assert "defined the function" in en_groups[1].notes_text

    def test_language_neutral_cells_included(self):
        cells = parse_cells(SAMPLE_SLIDES)
        groups = group_slides(cells, "de", include_header=False)

        # The code subslide (index 1) contains the pythagoras code
        text = groups[1].text_content
        assert "pythagoras" in text

    def test_text_content_strips_formatting(self):
        cells = parse_cells(SAMPLE_SLIDES)
        groups = group_slides(cells, "de", include_header=False)

        text = groups[0].text_content
        # Should not contain markdown formatting characters
        assert "`" not in text
        assert "def" in text  # But keywords should survive

    def test_slide_indices_are_sequential(self):
        cells = parse_cells(SAMPLE_SLIDES)
        groups = group_slides(cells, "de", include_header=True)

        indices = [g.index for g in groups]
        assert indices == [0, 1, 2, 3]

    def test_slide_indices_without_header(self):
        cells = parse_cells(SAMPLE_SLIDES)
        groups = group_slides(cells, "de", include_header=False)

        indices = [g.index for g in groups]
        assert indices == [0, 1, 2]


class TestSlideGroupTextContent:
    """Test that text_content works well for OCR matching purposes."""

    def test_code_only_slide(self):
        text = textwrap.dedent("""\
            # %% [markdown] lang="de" tags=["subslide"]
            # ## Transformation

            # %%
            result = []
            for item in [1, 2, 3, 4]:
                result.append(item + 1)
            result
        """)
        cells = parse_cells(text)
        groups = group_slides(cells, "de", include_header=False)

        assert len(groups) == 1
        content = groups[0].text_content
        assert "Transformation" in content
        assert "result" in content
        assert "append" in content
