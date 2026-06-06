"""Comment-token (``//``-family) coverage for split / unify / companion naming.

Problem A Phase 2: ``split_text`` / ``unify_texts`` take a ``comment_token``,
``split_in_file`` / ``unify_in_file`` resolve it from the file extension, the
header-import rewrite matches either comment family, and ``companion_name``
preserves the deck's extension. The Python (``#``) behaviour is exercised
exhaustively by ``test_split.py``; here we prove the ``//`` family works and the
extension-generic file paths are correct.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.slides.split import (
    split_in_file,
    split_text,
    unify_in_file,
    unify_texts,
)
from clm.slides.voiceover_tools import companion_name

DECK_CS = "\n".join(
    [
        "// -*- coding: utf-8 -*-",
        "// j2 from 'macros.j2' import header",
        '// {{ header("Titel", "Title") }}',
        "",
        '// %% [markdown] lang="de" tags=["slide"] slide_id="intro"',
        "// ## Überschrift",
        "",
        '// %% [markdown] lang="en" tags=["slide"] slide_id="intro"',
        "// ## Heading",
        "",
        '// %% tags=["keep"]',
        "int x = 1;",
        "",
    ]
)

COMPANION_CS = "\n".join(
    [
        '// %% [markdown] lang="de" tags=["voiceover"] for_slide="intro"',
        "// Sprechertext.",
        "",
        '// %% [markdown] lang="en" tags=["voiceover"] for_slide="intro"',
        "// Speaker text.",
        "",
    ]
)


def test_split_rewrites_import_and_macro_with_slashes() -> None:
    de, en = split_text(DECK_CS, "//")
    # comment token preserved (// not #), bilingual header → sibling macros
    assert "// j2 from 'macros.j2' import header_de" in de
    assert '// {{ header_de("Titel") }}' in de
    assert "// j2 from 'macros.j2' import header_en" in en
    assert '// {{ header_en("Title") }}' in en
    # language routing: de half has no en slide and vice versa
    assert "Überschrift" in de and "Heading" not in de
    assert "Heading" in en and "Überschrift" not in en
    # shared code cell copied to both
    assert "int x = 1;" in de and "int x = 1;" in en


def test_split_unify_roundtrip_clike() -> None:
    de, en = split_text(DECK_CS, "//")
    assert unify_texts(de, en, "//") == DECK_CS
    # the canonical split/unify invariant in both directions
    assert split_text(unify_texts(de, en, "//"), "//") == (de, en)


def test_split_in_file_and_unify_in_file_clike(tmp_path: Path) -> None:
    src = tmp_path / "slides_demo.cs"
    src.write_text(DECK_CS, encoding="utf-8")
    res = split_in_file(src)
    de_path = tmp_path / "slides_demo.de.cs"
    en_path = tmp_path / "slides_demo.en.cs"
    assert Path(res.de_path) == de_path and de_path.exists()
    assert Path(res.en_path) == en_path and en_path.exists()
    assert "import header_de" in de_path.read_text(encoding="utf-8")
    # unify back to a byte-identical bilingual deck
    rebuilt = tmp_path / "rebuilt.cs"
    unify_in_file(de_path, en_path, target=rebuilt)
    assert rebuilt.read_text(encoding="utf-8") == DECK_CS


def test_split_in_file_splits_companion_clike(tmp_path: Path) -> None:
    (tmp_path / "slides_demo.cs").write_text(DECK_CS, encoding="utf-8")
    (tmp_path / "voiceover_demo.cs").write_text(COMPANION_CS, encoding="utf-8")
    res = split_in_file(tmp_path / "slides_demo.cs")
    assert (tmp_path / "voiceover_demo.de.cs").exists()
    assert (tmp_path / "voiceover_demo.en.cs").exists()
    assert res.de_companion is not None and res.de_companion.endswith("voiceover_demo.de.cs")
    assert "Sprechertext." in (tmp_path / "voiceover_demo.de.cs").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "name,expected",
    [
        ("slides_x.cs", "voiceover_x.cs"),
        ("slides_010_x.de.cpp", "voiceover_010_x.de.cpp"),
        ("topic_y.java", "voiceover_y.java"),
        ("project_z.ts", "voiceover_z.ts"),
        ("slides_x.py", "voiceover_x.py"),  # regression: Python unchanged
        ("slides_x.de.py", "voiceover_x.de.py"),  # regression: split half
    ],
)
def test_companion_name_preserves_extension(name: str, expected: str) -> None:
    assert companion_name(Path(name)) == expected
