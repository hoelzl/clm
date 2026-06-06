"""Comment-token (``#`` vs ``//``) parametrization of the slide parsers.

Problem A Phase 1: the shared percent-format parsers
(:mod:`clm.notebooks.slide_parser`, :mod:`clm.slides.raw_cells`) now take a
``comment_token`` (default ``"#"``) so they parse C#/C++/Java/TypeScript
(``//``) decks as well as Python (``#``) ones. The default must reproduce the
old Python behaviour exactly; the two languages must parse to equivalent cells.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.notebooks.slide_parser import (
    comment_token_for_path,
    parse_cell_header,
    parse_cells,
    parse_slides,
)
from clm.slides.raw_cells import is_cell_boundary, reconstruct, split_cells
from clm.workers.notebook.utils.prog_lang_utils import line_comment_for


def deck(tok: str) -> str:
    """A structurally identical header-line-less deck for comment token ``tok``."""
    code = "x = 1" if tok == "#" else "int x = 1;"
    return "\n".join(
        [
            f"{tok} j2 from 'macros.j2' import header",
            f'{tok} {{{{ header("Titel", "Title") }}}}',
            "",
            f'{tok} %% [markdown] lang="de" tags=["slide"]',
            f"{tok} ## Überschrift",
            f"{tok}",
            f"{tok} - Punkt eins",
            "",
            f'{tok} %% [markdown] lang="en" tags=["slide"]',
            f"{tok} ## Heading",
            "",
            f'{tok} %% tags=["keep"]',
            code,
            "",
        ]
    )


@pytest.mark.parametrize("tok", ["#", "//"])
def test_parse_cells_structure(tok: str) -> None:
    cells = parse_cells(deck(tok), tok)
    # j2 import, j2 header, de slide, en slide, keep-code  → 5 cells
    assert len(cells) == 5
    assert cells[0].metadata.is_j2
    assert cells[1].metadata.is_j2  # the ``{{ header(...) }}`` call
    assert cells[2].metadata.cell_type == "markdown"
    assert cells[2].metadata.lang == "de"
    assert cells[2].metadata.tags == ["slide"]
    assert cells[3].metadata.lang == "en"
    assert cells[4].metadata.cell_type == "code"
    assert "keep" in cells[4].metadata.tags
    assert all(c.comment_token == tok for c in cells)


def test_text_content_is_token_independent() -> None:
    py = parse_cells(deck("#"), "#")
    cs = parse_cells(deck("//"), "//")
    # The markdown/j2 cells (0-3) carry identical prose in both decks; only the
    # final code cell differs by design (`x = 1` vs `int x = 1;`).
    assert [c.text_content() for c in py[:4]] == [c.text_content() for c in cs[:4]]
    # the de slide reads as plain prose, comment + heading markers stripped
    assert "Überschrift" in cs[2].text_content()
    assert "//" not in cs[2].text_content()


def test_default_token_matches_explicit_hash() -> None:
    """Calling the parsers without a token must equal passing ``"#"`` (no regression)."""
    default = parse_cells(deck("#"))
    explicit = parse_cells(deck("#"), "#")
    key = lambda cs: [  # noqa: E731
        (c.metadata.cell_type, c.metadata.lang, c.metadata.tags, c.metadata.is_j2, c.text_content())
        for c in cs
    ]
    assert key(default) == key(explicit)


@pytest.mark.parametrize(
    "prog_lang,expected",
    [
        ("python", "#"),
        ("rust", "#"),
        ("csharp", "//"),
        ("cpp", "//"),
        ("java", "//"),
        ("typescript", "//"),
    ],
)
def test_line_comment_for(prog_lang: str, expected: str) -> None:
    assert line_comment_for(prog_lang) == expected


@pytest.mark.parametrize(
    "name,expected",
    [
        ("slides_x.py", "#"),
        ("slides_x.cs", "//"),
        ("slides_x.cpp", "//"),
        ("slides_x.java", "//"),
        ("slides_x.ts", "//"),
        ("slides_x.rs", "#"),
        ("slides_x.weird", "#"),  # unknown extension falls back to "#"
    ],
)
def test_comment_token_for_path(name: str, expected: str) -> None:
    assert comment_token_for_path(Path(name)) == expected


def test_parse_cell_header_clike() -> None:
    m = parse_cell_header('// %% [markdown] lang="de" tags=["slide", "keep"]', "//")
    assert m.cell_type == "markdown"
    assert m.lang == "de"
    assert m.tags == ["slide", "keep"]
    assert parse_cell_header("// {{ header('a', 'b') }}", "//").is_j2
    assert parse_cell_header("// j2 from 'macros.j2' import header", "//").is_j2
    # a "//" header is NOT a boundary/j2 when parsed as Python
    assert not parse_cell_header("// {{ header('a', 'b') }}", "#").is_j2


@pytest.mark.parametrize("tok", ["#", "//"])
def test_raw_cells_roundtrip_and_boundaries(tok: str) -> None:
    text = deck(tok)
    preamble, cells = split_cells(text, tok)
    assert reconstruct(preamble, cells) == text  # lossless for any language
    assert len(cells) == 5
    assert cells[1].metadata.is_j2
    assert is_cell_boundary(f'{tok} %% [markdown] lang="de"', tok)
    assert not is_cell_boundary(f'{tok} %% [markdown] lang="de"', "//" if tok == "#" else "#")


def test_parse_slides_resolves_token_from_extension(tmp_path: Path) -> None:
    f = tmp_path / "slides_demo.cs"
    f.write_text(deck("//"), encoding="utf-8")
    groups = parse_slides(f, "de")
    assert any(g.slide_type == "header" for g in groups)
    titles = [g.title for g in groups]
    assert "Titel" in titles  # header macro, de side
    assert "Überschrift" in titles  # the de content slide
