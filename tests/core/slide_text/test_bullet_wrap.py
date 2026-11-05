"""Tests for :func:`clm.core.slide_text.bullet_wrap.wrap_bullet` (#1101)."""

from __future__ import annotations

import pytest

from clm.core.slide_text.bullet_wrap import NARRATIVE_WIDTH, wrap_bullet

TOP = ("// - ", "//   ")
CHILD = ("//   - ", "//     ")


def _wrap(text: str, prefixes: tuple[str, str] = TOP, width: int = NARRATIVE_WIDTH) -> list[str]:
    return wrap_bullet(text, *prefixes, width=width)


def _rejoin(lines: list[str], prefixes: tuple[str, str] = TOP) -> str:
    first, cont = prefixes
    parts = [lines[0].removeprefix(first)] + [line.removeprefix(cont) for line in lines[1:]]
    return " ".join(parts)


def test_width_is_80() -> None:
    assert NARRATIVE_WIDTH == 80


def test_fitting_bullet_is_unchanged_byte_for_byte() -> None:
    text = "Ein  Satz mit  doppelten Leerzeichen."
    assert _wrap(text) == [f"// - {text}"]


def test_exactly_80_columns_does_not_wrap() -> None:
    text = "x" * (80 - len("// - "))
    assert _wrap(text) == [f"// - {text}"]


def test_long_bullet_wraps_at_80_with_hanging_indent() -> None:
    text = " ".join(f"word{i}" for i in range(40))
    lines = _wrap(text)
    assert len(lines) > 1
    assert lines[0].startswith("// - ")
    assert all(line.startswith("//   ") and not line.startswith("//    ") for line in lines[1:])
    assert all(len(line) <= 80 for line in lines)
    assert _rejoin(lines) == text


def test_python_token_continuation_is_hash_plus_three_spaces() -> None:
    text = " ".join(["Python"] * 30)
    lines = wrap_bullet(text, "# - ", "#   ")
    assert lines[0].startswith("# - ")
    assert all(line.startswith("#   ") for line in lines[1:])
    assert all(len(line) <= 80 for line in lines)


def test_child_bullet_continuation_aligns_under_its_text() -> None:
    text = " ".join(["child"] * 30)
    lines = _wrap(text, CHILD)
    assert lines[0].startswith("//   - ")
    assert all(line.startswith("//     ") and line[7] != " " for line in lines[1:])
    assert all(len(line) <= 80 for line in lines)
    assert _rejoin(lines, CHILD) == text


def test_never_breaks_inside_an_inline_code_span() -> None:
    code = "`std::vector<int> values{1, 2, 3};`"
    text = "Hier sehen wir " + "viele Wörter " * 4 + code + " und danach noch mehr Text."
    lines = _wrap(text)
    assert len(lines) > 1
    assert any(code in line for line in lines), lines
    assert _rejoin(lines) == text


def test_double_backtick_span_with_inner_backtick_stays_whole() -> None:
    code = "``a ` b c d e``"
    text = "w " * 35 + code + " tail"
    lines = _wrap(text)
    assert any(code in line for line in lines), lines


def test_unmatched_backtick_is_literal() -> None:
    text = "an ` unmatched backtick " + "filler " * 12
    lines = _wrap(text.strip())
    assert all(len(line) <= 80 for line in lines)


def test_url_is_never_broken() -> None:
    url = "https://en.cppreference.com/w/cpp/container/vector/emplace_back#Example"
    text = "Details stehen in der Referenz unter " + url + " — dort gibt es Beispiele."
    lines = _wrap(text)
    assert any(url in line for line in lines), lines


def test_overlong_word_stays_on_its_own_line() -> None:
    word = "x" * 100
    lines = _wrap(f"kurz {word} danach")
    assert lines == ["// - kurz", f"//   {word}", "//   danach"]


@pytest.mark.parametrize("marker", ["-", "+", "*", "1.", "2)", "#", "##", ">", "---", "==="])
def test_continuation_never_starts_with_a_block_marker(marker: str) -> None:
    # Place the marker exactly where a naive wrap would break before it:
    # "// - " + head is 79 columns, so " <marker>" cannot fit on line one.
    head = "word " * 14 + "abcd"
    assert len("// - " + head) == 79
    text = f"{head} {marker} rest of the bullet"
    lines = _wrap(text)
    assert len(lines) == 2
    for line in lines[1:]:
        assert not line.removeprefix("//   ").startswith(f"{marker} "), lines
    assert all(len(line) <= 80 for line in lines)
    assert _rejoin(lines) == text


def test_escaped_backtick_does_not_open_a_code_span() -> None:
    text = "Escaped \\` tick " + "w " * 40 + "and a real `x`"
    lines = _wrap(text)
    assert all(len(line) <= 80 for line in lines), lines
    assert _rejoin(lines) == text


def test_continuation_never_starts_with_a_fence_or_html_tag() -> None:
    head = "word " * 14 + "abcd"
    for atom in ("```cpp", "<details>", ">5", ">Zitat"):
        lines = _wrap(f"{head} {atom} c")
        assert len(lines) == 2
        assert all(not line.removeprefix("//   ").startswith(atom) for line in lines[1:]), lines
