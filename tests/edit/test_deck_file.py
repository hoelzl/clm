"""Tests for :mod:`clm.edit.deck_file` — the pure library core.

These exercise the lossless round-trip and the index-keyed edit
operations directly, with no web layer. The two percent-format token
families (``#`` python, ``//`` c-family) are both covered.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.edit.deck_file import CellInfo, DeckFile, DeckFileError

# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------

PY_DECK = """\
# j2 from 'macros.j2' import header
# {{ header("Einführung", "Introduction") }}

# %% [markdown] lang="de" tags=["slide"] slide_id="intro-de"
# # Einführung
# - Begrüßung
# - Überblick

# %% [markdown] lang="de" tags=["voiceover"] slide_id="intro-de" for_slide="intro-de"
# - Sag Hallo zur Klasse.

# %% lang="de"
print("Hallo Welt!")

# %% [markdown] lang="en" tags=["slide"] slide_id="intro-en"
# # Introduction
# - Welcome
# - Overview
"""

CPP_DECK = """\
// j2 from 'macros.j2' import header
// {{ header("Einführung", "Introduction") }}

// %% [markdown] lang="de" tags=["slide"]
// # Einführung

// %% lang="de"
std::cout << "Hallo";
"""


@pytest.fixture()
def py_deck(tmp_path: Path) -> Path:
    p = tmp_path / "slides_intro.py"
    p.write_text(PY_DECK, encoding="utf-8")
    return p


@pytest.fixture()
def cpp_deck(tmp_path: Path) -> Path:
    p = tmp_path / "slides_intro.cpp"
    p.write_text(CPP_DECK, encoding="utf-8")
    return p


# ----------------------------------------------------------------------
# Round-trip invariance
# ----------------------------------------------------------------------


class TestRoundTrip:
    def test_load_then_render_is_byte_identical(self, py_deck: Path):
        original = py_deck.read_text(encoding="utf-8")
        deck = DeckFile.load(py_deck)
        assert deck.render() == original

    def test_flush_without_edits_is_noop(self, py_deck: Path):
        original = py_deck.read_text(encoding="utf-8")
        DeckFile.load(py_deck).flush()
        assert py_deck.read_text(encoding="utf-8") == original

    def test_cpp_deck_round_trips(self, cpp_deck: Path):
        original = cpp_deck.read_text(encoding="utf-8")
        deck = DeckFile.load(cpp_deck)
        assert deck.render() == original

    def test_file_without_trailing_newline_round_trips(self, tmp_path: Path):
        p = tmp_path / "no_newline.py"
        p.write_text("# %%\nprint('x')", encoding="utf-8")  # no trailing \n
        deck = DeckFile.load(p)
        assert not deck.ends_with_newline
        assert deck.render() == "# %%\nprint('x')"


# ----------------------------------------------------------------------
# Read views
# ----------------------------------------------------------------------


class TestReadViews:
    def test_cell_infos_project_metadata(self, py_deck: Path):
        infos = DeckFile.load(py_deck).cell_infos()
        # j2 import, j2 header call, then 4 content cells (de slide,
        # voiceover, code, en slide).
        assert len(infos) == 6
        assert infos[0].is_j2
        assert infos[0].kind == "j2"
        assert infos[2].kind == "markdown"
        assert infos[2].lang == "de"
        assert "slide" in infos[2].tags
        assert infos[2].is_slide_start
        assert infos[3].is_narrative
        assert infos[4].kind == "code"

    def test_cell_count(self, py_deck: Path):
        assert DeckFile.load(py_deck).cell_count() == 6


# ----------------------------------------------------------------------
# replace_cell_body
# ----------------------------------------------------------------------


class TestReplaceCellBody:
    def test_rewrites_body_preserves_header(self, py_deck: Path):
        deck = DeckFile.load(py_deck)
        deck.replace_cell_body(4, 'print("Hallo!")')
        cell = deck.cells[4]
        assert cell.header == '# %% lang="de"'
        # body carries the preserved trailing blank (separator), so compare stripped.
        assert cell.body.strip() == 'print("Hallo!")'

    def test_preserves_trailing_blanks(self, py_deck: Path):
        deck = DeckFile.load(py_deck)
        original_trailing = _count_trailing(deck.cells[2])
        deck.replace_cell_body(2, "# new body")
        assert _count_trailing(deck.cells[2]) == original_trailing

    def test_untouched_cells_stay_byte_identical(self, py_deck: Path):
        original = py_deck.read_text(encoding="utf-8")
        deck = DeckFile.load(py_deck)
        before = [list(c.lines) for c in deck.cells]
        deck.replace_cell_body(4, "changed")
        # Only cell 4's lines changed.
        for i, lines in enumerate(before):
            if i == 4:
                continue
            assert deck.cells[i].lines == lines, f"cell {i} was disturbed"
        # And the full file differs only in cell 4's body region.
        deck.flush()
        new = py_deck.read_text(encoding="utf-8")
        assert new != original
        assert "changed" in new

    def test_out_of_range_raises(self, py_deck: Path):
        deck = DeckFile.load(py_deck)
        with pytest.raises(DeckFileError):
            deck.replace_cell_body(99, "x")

    def test_cpp_deck_replace(self, cpp_deck: Path):
        deck = DeckFile.load(cpp_deck)
        deck.replace_cell_body(2, "// # Neue Überschrift")
        assert deck.cells[2].body.strip() == "// # Neue Überschrift"


# ----------------------------------------------------------------------
# update_cell_header
# ----------------------------------------------------------------------


class TestUpdateHeader:
    def test_rewrites_header_and_metadata(self, py_deck: Path):
        deck = DeckFile.load(py_deck)
        deck.update_cell_header(4, '# %% [markdown] lang="en" tags=["notes"]')
        assert deck.cells[4].header == '# %% [markdown] lang="en" tags=["notes"]'
        assert deck.cells[4].metadata.lang == "en"
        assert deck.cells[4].metadata.cell_type == "markdown"
        assert "notes" in deck.cells[4].metadata.tags

    def test_header_body_preserved(self, py_deck: Path):
        deck = DeckFile.load(py_deck)
        original_body = deck.cells[4].body
        deck.update_cell_header(4, '# %% lang="en"')
        assert deck.cells[4].body == original_body

    def test_out_of_range_raises(self, py_deck: Path):
        with pytest.raises(DeckFileError):
            DeckFile.load(py_deck).update_cell_header(-1, "# %%")


# ----------------------------------------------------------------------
# delete_cell
# ----------------------------------------------------------------------


class TestDeleteCell:
    def test_removes_cell(self, py_deck: Path):
        deck = DeckFile.load(py_deck)
        n = deck.cell_count()
        deck.delete_cell(3)  # delete the voiceover cell
        assert deck.cell_count() == n - 1
        # The cell that was at index 4 (code) shifts into index 3.
        assert deck.cells[3].header == '# %% lang="de"'

    def test_delete_preserves_terminal_newline(self, py_deck: Path):
        deck = DeckFile.load(py_deck)
        last = deck.cell_count() - 1
        deck.delete_cell(last)
        assert deck.render().endswith("\n")

    def test_delete_last_cell_no_newline_file(self, tmp_path: Path):
        p = tmp_path / "x.py"
        p.write_text("# %%\nprint('a')\n\n# %%\nprint('b')", encoding="utf-8")
        deck = DeckFile.load(p)
        deck.delete_cell(1)
        # File didn't end with newline originally; ends_with_newline stays False
        # so render() doesn't *add* a newline. Cell 0 retains its separator
        # blank (a `''` body line), which renders as a trailing `\n` — the
        # same leave-the-now-last-cell-alone behaviour as FileState.delete.
        assert not deck.ends_with_newline
        assert deck.render() == "# %%\nprint('a')\n"

    def test_out_of_range_raises(self, py_deck: Path):
        with pytest.raises(DeckFileError):
            DeckFile.load(py_deck).delete_cell(50)


# ----------------------------------------------------------------------
# insert_cell
# ----------------------------------------------------------------------


class TestInsertCell:
    def test_insert_in_middle(self, py_deck: Path):
        deck = DeckFile.load(py_deck)
        n = deck.cell_count()
        deck.insert_cell(4, '# %% [markdown] lang="de" tags=["slide"]', "# Neu")
        assert deck.cell_count() == n + 1
        assert deck.cells[4].body.strip() == "# Neu"
        assert deck.cells[4].metadata.lang == "de"

    def test_insert_at_end_appends(self, py_deck: Path):
        deck = DeckFile.load(py_deck)
        n = deck.cell_count()
        idx = deck.insert_cell(n, "# %%", "tail")
        assert idx == n
        assert deck.cells[n].body.strip() == "tail"

    def test_insert_at_zero(self, py_deck: Path):
        deck = DeckFile.load(py_deck)
        deck.insert_cell(0, "# %%", "head")
        assert deck.cells[0].body.strip() == "head"

    def test_insert_gets_separator_padding(self, py_deck: Path):
        deck = DeckFile.load(py_deck)
        deck.insert_cell(2, "# %%", "new")
        # The deck is blank-separated (gap 1), so the inserted middle cell
        # gets one trailing blank.
        assert _count_trailing(deck.cells[2]) == 1

    def test_insert_last_has_no_trailing_blank(self, py_deck: Path):
        deck = DeckFile.load(py_deck)
        n = deck.cell_count()
        deck.insert_cell(n, "# %%", "tail")
        # Last cell carries no trailing blank; render restores newline.
        assert _count_trailing(deck.cells[n]) == 0
        assert deck.render().endswith("tail\n")

    def test_out_of_range_raises(self, py_deck: Path):
        deck = DeckFile.load(py_deck)
        with pytest.raises(DeckFileError):
            deck.insert_cell(deck.cell_count() + 1, "# %%", "x")


# ----------------------------------------------------------------------
# move_cell
# ----------------------------------------------------------------------


class TestMoveCell:
    def test_move_down(self, py_deck: Path):
        deck = DeckFile.load(py_deck)
        header_before = deck.cells[2].header
        new_idx = deck.move_cell(2, +1)
        assert new_idx == 3
        assert deck.cells[3].header == header_before

    def test_move_up(self, py_deck: Path):
        deck = DeckFile.load(py_deck)
        header_before = deck.cells[4].header
        new_idx = deck.move_cell(4, -1)
        assert new_idx == 3
        assert deck.cells[3].header == header_before

    def test_move_at_boundary_is_noop(self, py_deck: Path):
        deck = DeckFile.load(py_deck)
        last = deck.cell_count() - 1
        header = deck.cells[last].header
        idx = deck.move_cell(last, +1)
        assert idx == last
        assert deck.cells[last].header == header
        # first cell up-move
        idx0 = deck.move_cell(0, -1)
        assert idx0 == 0

    def test_count_unchanged_after_move(self, py_deck: Path):
        deck = DeckFile.load(py_deck)
        n = deck.cell_count()
        deck.move_cell(2, +1)
        assert deck.cell_count() == n


# ----------------------------------------------------------------------
# Round-trip after edits (the real safety net)
# ----------------------------------------------------------------------


class TestEditRoundTrip:
    def test_replace_then_flush_then_reload(self, py_deck: Path):
        deck = DeckFile.load(py_deck)
        deck.replace_cell_body(2, "# completely new slide")
        deck.flush()
        reloaded = DeckFile.load(py_deck)
        assert reloaded.cells[2].body.strip() == "# completely new slide"
        # other cells intact (body carries its separator blank)
        assert reloaded.cells[4].body.strip() == 'print("Hallo Welt!")'

    def test_insert_then_flush_then_reload(self, py_deck: Path):
        deck = DeckFile.load(py_deck)
        deck.insert_cell(2, '# %% [markdown] lang="en" tags=["slide"]', "# Inserted")
        deck.flush()
        reloaded = DeckFile.load(py_deck)
        assert any("Inserted" in c.body for c in reloaded.cells)
        assert reloaded.cell_count() == 7

    def test_delete_then_flush_then_reload(self, py_deck: Path):
        deck = DeckFile.load(py_deck)
        original_header = deck.cells[3].header
        deck.delete_cell(3)
        deck.flush()
        reloaded = DeckFile.load(py_deck)
        assert all(c.header != original_header or i != 3 for i, c in enumerate(reloaded.cells))


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _count_trailing(cell) -> int:
    n = 0
    for line in reversed(cell.lines[1:]):
        if line == "":
            n += 1
        else:
            break
    return n
