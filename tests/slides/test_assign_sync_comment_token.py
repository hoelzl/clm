"""Comment-token (``//``-family) coverage for assign-ids + sync writeback.

Problem A Phase 3c: assign-ids mints slide_ids on ``//``-family decks (parse +
headingless are token-aware), and the sync writeback's ``swap_lang`` inserts a
``lang=`` attribute on either comment family's bare code-cell header.
"""

from __future__ import annotations

from pathlib import Path

from clm.slides.assign_ids import AssignOptions, assign_ids_in_file
from clm.slides.sync_writeback import swap_lang


def test_assign_ids_mints_on_clike_deck(tmp_path: Path) -> None:
    deck = "\n".join(
        [
            '// %% [markdown] lang="de" tags=["slide"]',
            "//",
            "// ## Erste Folie",
            "",
        ]
    )
    f = tmp_path / "slides_demo.de.cs"
    f.write_text(deck, encoding="utf-8")
    result = assign_ids_in_file(f, AssignOptions())
    out = f.read_text(encoding="utf-8")
    # an id was minted from the "## Erste Folie" heading (headingless saw the //)
    assert 'slide_id="' in out
    assert result.assignments
    # the cell header keeps its // comment token (no corruption)
    assert out.splitlines()[0].startswith('// %% [markdown] lang="de"')


def test_swap_lang_inserts_on_either_family() -> None:
    assert swap_lang('// %% tags=["keep"]', "de") == '// %% lang="de" tags=["keep"]'
    # python regression
    assert swap_lang('# %% tags=["keep"]', "de") == '# %% lang="de" tags=["keep"]'
    # already-tagged headers just have their lang swapped, both families
    assert swap_lang('// %% [markdown] lang="en" tags=["slide"]', "de") == (
        '// %% [markdown] lang="de" tags=["slide"]'
    )
