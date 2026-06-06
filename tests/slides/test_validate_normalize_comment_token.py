"""Comment-token (``//``-family) coverage for validate / normalize / headingless.

Problem A Phase 3: the lint/gate/fix tools must accept ``// %%`` headers (no false
format errors), insert the right blank-comment token when normalizing, and extract
headings/prose from ``//``-prefixed markdown. Python (``#``) behaviour is unchanged
(exercised by the existing suites); here we prove the ``//`` family works.
"""

from __future__ import annotations

from pathlib import Path

from clm.slides.headingless import Category, classify, extract_heading
from clm.slides.normalizer import normalize_file
from clm.slides.validator import validate_file

DECK_CS = "\n".join(
    [
        "// -*- coding: utf-8 -*-",
        "// j2 from 'macros.j2' import header",
        '// {{ header("Titel", "Title") }}',
        "",
        '// %% [markdown] lang="de" tags=["slide"] slide_id="intro"',
        "//",
        "// ## Überschrift",
        "",
        '// %% [markdown] lang="en" tags=["slide"] slide_id="intro"',
        "//",
        "// ## Heading",
        "",
    ]
)


def test_validate_accepts_clike_headers(tmp_path: Path) -> None:
    f = tmp_path / "slides_demo.cs"
    f.write_text(DECK_CS, encoding="utf-8")
    result = validate_file(f)
    # The "// %%" headers must NOT raise the "does not start with ..." format error,
    # and the "//" blank comments must NOT raise blank-comment warnings (N-2).
    fmt = [x for x in result.findings if x.category == "format"]
    assert fmt == [], [f"{x.message}" for x in fmt]


def test_normalize_cell_spacing_inserts_comment_token(tmp_path: Path) -> None:
    deck = "\n".join(
        [
            "// j2 from 'macros.j2' import header",
            '// {{ header("T", "T") }}',
            "",
            '// %% [markdown] lang="de" tags=["slide"] slide_id="x"',
            "// ## Heading",  # missing the leading blank comment
        ]
    )
    f = tmp_path / "slides_x.cs"
    f.write_text(deck, encoding="utf-8")
    normalize_file(f, operations=["cell_spacing"])
    out = f.read_text(encoding="utf-8")
    # the inserted blank comment is "//" (not a corrupting "#")
    assert "//\n// ## Heading" in out
    assert "\n#\n" not in out


def test_headingless_extracts_clike() -> None:
    assert extract_heading("// ## My Title") == "My Title"
    bullet = classify("// - First bullet")
    assert bullet.category is Category.EXTRACTABLE
    assert bullet.text == "First bullet"
    prose = classify("// Just some prose line")
    assert prose.text == "Just some prose line"
    # Python regression
    assert extract_heading("# ## My Title") == "My Title"
    assert classify("# - First bullet").text == "First bullet"
