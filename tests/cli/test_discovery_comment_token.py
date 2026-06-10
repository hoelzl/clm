"""Comment-token (``//``-family) coverage for CLI discovery + dispatch.

Problem A Phase 5: recursive slide-file discovery finds .cs/.cpp/… decks (not just
.py), ``clm validate`` infers "slides" for any slide extension, and ``clm export summary``
extracts content from //-family decks. (Voiceover/MCP already work via parse_slides,
which resolves the token from the path.)
"""

from __future__ import annotations

from pathlib import Path

from clm.cli.commands.export.summary import _extract_from_py
from clm.cli.commands.validate import _infer_kind
from clm.core.topic_resolver import find_slide_files_recursive


def test_find_slide_files_recursive_finds_clike(tmp_path: Path) -> None:
    topic = tmp_path / "module_010_intros" / "topic_100_welcome"
    topic.mkdir(parents=True)
    cs = topic / "slides_welcome.cs"
    cs.write_text('// %% [markdown] tags=["slide"]\n// ## Hi\n', encoding="utf-8")
    cpp = topic / "slides_other.cpp"
    cpp.write_text('// %% [markdown] tags=["slide"]\n// ## Yo\n', encoding="utf-8")
    not_a_deck = topic / "helper.cs"  # no slides_/topic_/project_ prefix
    not_a_deck.write_text("int x = 1;\n", encoding="utf-8")

    found = find_slide_files_recursive(tmp_path)
    assert cs.resolve() in found
    assert cpp.resolve() in found
    assert not_a_deck.resolve() not in found


def test_infer_kind_accepts_clike(tmp_path: Path) -> None:
    # _infer_kind inspects a real path (is_file / suffix), so create the files.
    for name in ("slides_x.cs", "slides_x.cpp", "slides_x.java", "slides_x.py"):
        f = tmp_path / name
        f.write_text("// %%\n", encoding="utf-8")
        assert _infer_kind(f) == "slides", name
    (tmp_path / "course.xml").write_text("<course/>", encoding="utf-8")
    assert _infer_kind(tmp_path / "course.xml") == "spec"
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    assert _infer_kind(tmp_path / "notes.txt") is None


def test_summarize_extract_from_clike() -> None:
    deck = "\n".join(
        [
            '// %% [markdown] tags=["slide"]',
            "// Hello world",
            "// %%",
            "int x = 1;",
        ]
    )
    out = _extract_from_py(deck, "student", "//")
    assert "Hello world" in out
    assert "//" not in out  # comment prefix stripped
    # python regression
    py = "\n".join(["# %% [markdown]", "# Hi there", "# %%", "x = 1"])
    assert "Hi there" in _extract_from_py(py, "student", "#")
