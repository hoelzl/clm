"""Comment-token (``//``-family) coverage for voiceover write/merge + suppression.

Problem A Phase 4: the build-time voiceover merge, the companion/narrative writers,
and the output-suppression patterns all handle ``//``-family (C#/C++/Java/TS) decks.
"""

from __future__ import annotations

from pathlib import Path

from clm.infrastructure.utils.path_utils import is_ignored_file_for_output
from clm.notebooks.slide_writer import format_narrative_cell
from clm.slides.voiceover_tools import merge_voiceover_text, render_companion_update

_SLIDE_CS = "\n".join(
    [
        '// %% [markdown] lang="de" tags=["slide"] slide_id="intro"',
        "// ## Hallo",
        "",
    ]
)
_COMPANION_CS = "\n".join(
    [
        '// %% [markdown] lang="de" tags=["voiceover"] for_slide="intro"',
        "// Sprechertext.",
        "",
    ]
)


def test_merge_voiceover_text_clike() -> None:
    merged, unmatched = merge_voiceover_text(_SLIDE_CS, _COMPANION_CS, "//")
    assert unmatched == []
    assert 'tags=["voiceover"]' in merged
    assert "Sprechertext." in merged
    # V-1b: with the wrong token the //-deck parses to zero cells and the
    # voiceover is silently dropped — proves the token is load-bearing.
    merged_wrong, _ = merge_voiceover_text(_SLIDE_CS, _COMPANION_CS, "#")
    assert "Sprechertext." not in merged_wrong


def test_format_narrative_cell_clike() -> None:
    out = format_narrative_cell("Hallo\nWelt", "de", comment_token="//")
    assert out.startswith('// %% [markdown] lang="de" tags=["voiceover"]')
    assert "\n// - Hallo" in out
    # python regression
    assert format_narrative_cell("Hallo", "de").startswith("# %% [markdown]")


def test_render_companion_update_clike() -> None:
    out = render_companion_update("", {"intro": "Sprechertext"}, "de", comment_token="//")
    assert '// %% [markdown] lang="de" tags=["voiceover"] for_slide="intro"' in out
    assert "// - Sprechertext" in out


def test_voiceover_companion_output_suppressed() -> None:
    # D-1: a companion of ANY slide extension is kept out of output, not just .py
    assert is_ignored_file_for_output(Path("voiceover_intro.cs"))
    assert is_ignored_file_for_output(Path("voiceover_intro.cpp"))
    assert is_ignored_file_for_output(Path("voiceover_intro.de.cs"))
    assert is_ignored_file_for_output(Path("voiceover_intro.py"))  # regression
    # a normal slide deck must NOT be suppressed
    assert not is_ignored_file_for_output(Path("slides_intro.cs"))
