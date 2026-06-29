"""Issue #429: reflow-insensitive markdown content hashing.

A pure soft re-wrap of a markdown prose paragraph must hash identically, while
whitespace-significant blocks (fenced code, ``<pre>``, indented code, list/heading
structure) must hash byte-for-byte.
"""

from __future__ import annotations

from clm.slides.sync_writeback import cell_content_hash, normalize_for_hash


def _md(text: str) -> str:
    return cell_content_hash(text, markdown=True)


def _md_slash(text: str) -> str:
    """Markdown hash for a ``//``-comment deck (C++/C#/Java/TS), Issue #458."""
    return cell_content_hash(text, markdown=True, comment_token="//")


class TestProseReflow:
    def test_softwrap_hashes_equal(self) -> None:
        a = "# This is a long paragraph that\n# wraps across two lines."
        b = "# This is a long paragraph that wraps\n# across two lines."
        assert _md(a) == _md(b)

    def test_one_long_line_equals_wrapped(self) -> None:
        # The reported incident: one long line vs the same words wrapped at 80 cols.
        words = " ".join(f"word{i}" for i in range(40))
        one_line = f"# {words}"
        wrapped = "\n".join(f"# {chunk}" for chunk in _wrap(words, 8))
        assert _md(one_line) == _md(wrapped)

    def test_genuinely_different_prose_differs(self) -> None:
        a = "# This is a long paragraph that\n# wraps across two lines."
        b = "# This is a DIFFERENT paragraph that\n# wraps across two lines."
        assert _md(a) != _md(b)

    def test_blank_run_collapse(self) -> None:
        a = "# para one\n#\n#\n# para two"
        b = "# para one\n#\n# para two"
        assert _md(a) == _md(b)

    def test_markdown_emphasis_change_is_detected(self) -> None:
        # Normalization is whitespace-only; a real formatting change must differ.
        assert _md("# this is **bold**") != _md("# this is *italic*")


class TestPreservedBlocks:
    def test_fenced_code_blank_line_is_significant(self) -> None:
        a = "# ```python\n# x = 1\n# y = 2\n# ```"
        b = "# ```python\n# x = 1\n#\n# y = 2\n# ```"
        assert _md(a) != _md(b)

    def test_fenced_code_not_folded(self) -> None:
        # Two short code lines must NOT be joined like prose.
        norm = normalize_for_hash("# ```\n# a = 1\n# b = 2\n# ```")
        assert "a = 1\nb = 2" in norm

    def test_pre_block_preserved(self) -> None:
        a = "# <pre>\n# A --> B\n# B --> C\n# </pre>"
        b = "# <pre>\n# A --> B\n# B  --> C\n# </pre>"
        assert _md(a) != _md(b)

    def test_pre_lines_not_folded(self) -> None:
        norm = normalize_for_hash("# <pre>\n# A --> B\n# B --> C\n# </pre>")
        assert "A --> B\nB --> C" in norm

    def test_indented_code_preserved_and_not_folded(self) -> None:
        text = "# Look:\n#\n#     code_one()\n#     code_two()"
        norm = normalize_for_hash(text)
        assert "    code_one()\n    code_two()" in norm
        # changing the indentation is a real change
        other = "# Look:\n#\n#       code_one()\n#     code_two()"
        assert _md(text) != _md(other)

    def test_heading_kept_on_own_line(self) -> None:
        a = "# # Heading\n# Some prose here that is\n# wrapped."
        b = "# # Heading\n# Some prose here that is wrapped."
        assert _md(a) == _md(b)  # prose under the heading reflows
        norm = normalize_for_hash(a)
        assert norm.startswith("# Heading\n")

    def test_list_items_not_merged(self) -> None:
        norm = normalize_for_hash("# - item one\n# - item two")
        assert "- item one\n- item two" in norm

    def test_table_rows_preserved(self) -> None:
        a = "# | a | b |\n# | - | - |\n# | 1 | 2 |"
        b = "# | a | b |\n# | - | - |\n# | 1 | 3 |"
        assert _md(a) != _md(b)


class TestCodeCellUnchanged:
    def test_code_hash_is_strip_only(self) -> None:
        import hashlib

        text = "x = 1\ny = 2"
        expected = hashlib.sha256(text.strip().encode("utf-8")).hexdigest()
        assert cell_content_hash(text) == expected
        assert cell_content_hash(text, markdown=False) == expected

    def test_code_lines_not_folded(self) -> None:
        # markdown=False must NOT join code lines (regression guard).
        a = cell_content_hash("x = 1\ny = 2", markdown=False)
        b = cell_content_hash("x = 1 y = 2", markdown=False)
        assert a != b


class TestSlashCommentReflow:
    """Issue #458: ``//``-comment decks get the same reflow-insensitivity as ``#``."""

    def test_softwrap_hashes_equal(self) -> None:
        a = "// This is a long paragraph that\n// wraps across two lines."
        b = "// This is a long paragraph that wraps\n// across two lines."
        assert _md_slash(a) == _md_slash(b)

    def test_one_long_line_equals_wrapped(self) -> None:
        words = " ".join(f"word{i}" for i in range(40))
        one_line = f"// {words}"
        wrapped = "\n".join(f"// {chunk}" for chunk in _wrap(words, 8))
        assert _md_slash(one_line) == _md_slash(wrapped)

    def test_genuinely_different_prose_differs(self) -> None:
        a = "// This is a long paragraph that\n// wraps across two lines."
        b = "// This is a DIFFERENT paragraph that\n// wraps across two lines."
        assert _md_slash(a) != _md_slash(b)

    def test_heading_kept_on_own_line(self) -> None:
        a = "// # Heading\n// Some prose here that is\n// wrapped."
        b = "// # Heading\n// Some prose here that is wrapped."
        assert _md_slash(a) == _md_slash(b)
        assert normalize_for_hash(a, "//").startswith("# Heading\n")

    def test_fenced_code_blank_line_is_significant(self) -> None:
        a = "// ```cpp\n// int x = 1;\n// int y = 2;\n// ```"
        b = "// ```cpp\n// int x = 1;\n//\n// int y = 2;\n// ```"
        assert _md_slash(a) != _md_slash(b)

    def test_without_token_the_slash_prefix_blocks_reflow(self) -> None:
        # The #458 motivation: hashing a //-cell with the "#" default leaves the "// "
        # prefix on each line, so a re-wrap moves the embedded // tokens → different hash.
        a = "// This is a long paragraph that\n// wraps across two lines."
        b = "// This is a long paragraph that wraps\n// across two lines."
        assert cell_content_hash(a, markdown=True) != cell_content_hash(b, markdown=True)


class TestHashCommentUnchangedByVersionBump:
    """#458 must not change any ``#``-deck hash (``"#"`` was the prior default)."""

    def test_default_equals_explicit_hash_token(self) -> None:
        text = "# A paragraph of prose\n# wrapped across lines."
        assert cell_content_hash(text, markdown=True) == cell_content_hash(
            text, markdown=True, comment_token="#"
        )

    def test_hash_reflow_still_works(self) -> None:
        a = "# A paragraph of prose that\n# wraps across two lines."
        b = "# A paragraph of prose\n# that wraps across two lines."
        assert _md(a) == _md(b)


def _wrap(words: str, n: int) -> list[str]:
    parts = words.split(" ")
    return [" ".join(parts[i : i + n]) for i in range(0, len(parts), n)]
