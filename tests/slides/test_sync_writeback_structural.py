"""Unit tests for the P2 structural primitives on :class:`FileState`.

Covers ``move_cell`` (including the terminal-newline normalization when a cell
moves into/out of the last position) and the ``build_cell`` factory — the
pieces behind the Mobile Deck Studio reorder/insert ops. The Studio service
tests cover the integration; these pin the byte-exact serializer contract that
makes structural edits safe (untouched cells never shift).
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from clm.core.slide_text.raw_cells import split_cells
from clm.slides.sync_writeback import FileState, build_cell, role_of

DECK = dedent(
    """\
    # %% [markdown] lang="de" tags=["slide"] slide_id="a"
    # A

    # %% [markdown] lang="de" tags=["slide"] slide_id="b"
    # B

    # %% [markdown] lang="de" tags=["slide"] slide_id="c"
    # C
    """
)


def _write(tmp_path: Path) -> Path:
    p = tmp_path / "deck.de.py"
    p.write_text(DECK, encoding="utf-8")
    return p


def test_move_into_last_position_keeps_single_terminal_newline(tmp_path: Path):
    path = _write(tmp_path)
    state = FileState.load(path)
    assert state.move_cell("b", "slide", "down") is True
    state.flush()

    text = path.read_text(encoding="utf-8")
    # Order is now a, c, b; the file ends with exactly one trailing newline.
    _, cells = split_cells(text)
    assert [c.metadata.slide_id for c in cells] == ["a", "c", "b"]
    assert text.endswith("# B\n")
    assert not text.endswith("# B\n\n")


def test_move_leaves_untouched_cell_byte_exact(tmp_path: Path):
    path = _write(tmp_path)
    _, before = split_cells(path.read_text(encoding="utf-8"))
    a_before = "\n".join(next(c for c in before if c.metadata.slide_id == "a").lines)

    state = FileState.load(path)
    state.move_cell("b", "slide", "down")
    state.flush()

    _, after = split_cells(path.read_text(encoding="utf-8"))
    a_after = "\n".join(next(c for c in after if c.metadata.slide_id == "a").lines)
    assert a_after == a_before


def test_move_at_boundary_returns_false(tmp_path: Path):
    state = FileState.load(_write(tmp_path))
    assert state.move_cell("a", "slide", "up") is False
    assert state.dirty is False


def test_move_missing_cell_returns_false(tmp_path: Path):
    state = FileState.load(_write(tmp_path))
    assert state.move_cell("nope", "slide", "down") is False


def test_build_cell_markdown_header_and_role(tmp_path: Path):
    cell = build_cell(
        "#",
        cell_type="markdown",
        lang="de",
        tags=["slide"],
        slide_id="new-one",
        body="# Titel\n#\n# Text.\n\n",  # trailing blanks stripped by build_cell
    )
    assert cell.lines[0] == '# %% [markdown] lang="de" tags=["slide"] slide_id="new-one"'
    assert cell.lines[-1] == "# Text."  # no trailing blank retained
    assert cell.metadata.slide_id == "new-one"
    assert role_of(cell.metadata) == "slide"


def test_build_cell_code_header_is_addressable(tmp_path: Path):
    cell = build_cell(
        "#", cell_type="code", lang="de", tags=[], slide_id="snippet", body='print("hi")'
    )
    assert cell.lines[0] == '# %% lang="de" slide_id="snippet"'
    assert role_of(cell.metadata) == "code"


def test_build_cell_respects_comment_token(tmp_path: Path):
    cell = build_cell("//", cell_type="code", lang="en", tags=[], slide_id="x", body="int x = 1;")
    assert cell.lines[0] == '// %% lang="en" slide_id="x"'


class TestSetHeaderTagsCanonicalPosition:
    """Regression tests for #1053: an inserted ``tags=[…]`` block goes where
    the canonical header order puts it (``lang tags for_slide vo_anchor
    slide_id``), never after the identity attributes."""

    def test_inserts_before_slide_id(self):
        from clm.slides.sync_writeback import set_header_tags

        assert (
            set_header_tags('// %% slide_id="call-b"', ["subslide"])
            == '// %% tags=["subslide"] slide_id="call-b"'
        )

    def test_inserts_before_for_slide_and_vo_anchor(self):
        from clm.slides.sync_writeback import set_header_tags

        header = '# %% [markdown] lang="de" for_slide="s1" vo_anchor="a" slide_id="v1"'
        assert set_header_tags(header, ["voiceover"]) == (
            '# %% [markdown] lang="de" tags=["voiceover"] for_slide="s1" vo_anchor="a" '
            'slide_id="v1"'
        )

    def test_appends_when_no_identity_attribute(self):
        from clm.slides.sync_writeback import set_header_tags

        assert set_header_tags('# %% [markdown] lang="de"', ["notes"]) == (
            '# %% [markdown] lang="de" tags=["notes"]'
        )

    def test_existing_block_is_replaced_in_place(self):
        from clm.slides.sync_writeback import set_header_tags

        assert set_header_tags('# %% slide_id="x" tags=["a"]', ["b"]) == (
            '# %% slide_id="x" tags=["b"]'
        )
