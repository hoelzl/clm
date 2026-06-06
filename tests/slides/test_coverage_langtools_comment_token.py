"""Comment-token (``//``-family) coverage for coverage / lang_coverage / language_tools.

Problem A Phase 3b: the analysis tools (bullet/narrative coverage, DE/EN slide
counting, single-language view) handle ``//``-family decks. Python (``#``) behaviour
is unchanged.
"""

from __future__ import annotations

from pathlib import Path

from clm.slides.coverage import extract_bullets
from clm.slides.lang_coverage import count_languages
from clm.slides.language_tools import get_language_view


def test_extract_bullets_either_family() -> None:
    assert extract_bullets("// - First\n// - Second") == ["First", "Second"]
    assert extract_bullets("// 1. One\n// 2. Two") == ["One", "Two"]
    assert extract_bullets("# - First") == ["First"]  # python regression


def test_count_languages_clike() -> None:
    deck = "\n".join(
        [
            '// %% [markdown] lang="de" tags=["slide"]',
            "// ## A",
            '// %% [markdown] lang="en" tags=["slide"]',
            "// ## A",
        ]
    )
    assert count_languages(deck, "//") == (1, 1)
    # the wrong token sees zero cells — proves the token is load-bearing
    assert count_languages(deck, "#") == (0, 0)


def test_get_language_view_uses_comment_token(tmp_path: Path) -> None:
    deck = "\n".join(
        [
            '// %% [markdown] lang="de" tags=["slide"]',
            "// ## Hallo",
            '// %% [markdown] lang="en" tags=["slide"]',
            "// ## Hello",
        ]
    )
    f = tmp_path / "slides_x.cs"
    f.write_text(deck, encoding="utf-8")
    view = get_language_view(f, "de")
    # the back-reference annotation must be a // comment (valid in a C# file)
    assert "// [original line" in view
    assert "# [original line" not in view
    # only the de content is kept
    assert "Hallo" in view
    assert "Hello" not in view
