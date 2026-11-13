"""Rewrite bare display expressions of C++ decks to ``SHOW(expr);`` (#928).

The xeus-cpp kernel prints nothing for a code cell that is not terminated
by ``;`` — xeus-cling used to display the value of a bare expression, the
new kernel silently drops it, ``std::cout`` output included. Decks therefore
always terminate cells with ``;`` and show values through the ``SHOW``
macro of ``clm/display.hpp`` (the header the code export vendors and the
notebook worker image installs), which prints ``expr = value`` in the
notebook and in the exported program alike.

This module is the mechanical part of that migration: every top-level item
the C++ classifier reports as a bare display — an expression without ``;``,
or a call without ``;`` — becomes ``SHOW(<expr>);``, keeping the item's
leading comment lines and trailing line comment where they were, and the
deck gains ``#include <clm/display.hpp>`` (appended to its first
include-only cell, else a new ``keep`` cell before the first code cell).
Cells tagged ``global`` are skipped and reported: a ``SHOW`` at namespace
scope is ill-formed (it expands to a statement), so such a
cell needs the author's attention. Displays holding an inline lambda are
skipped and reported too: ``SHOW`` around one kills the kernel (#1044), while
the bare form merely prints nothing. Files are rewritten losslessly
(:mod:`clm.core.slide_text.raw_cells`); a second run changes nothing.

:func:`find_show_calls` is the read-only side of the same knowledge, used by
``clm validate`` (#1119, #1044).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from pathlib import Path

from attrs import Factory, define

from clm.core.slide_text.raw_cells import RawCell, reconstruct, split_cells
from clm.core.slide_text.slide_parser import parse_cell_header
from clm.workers.notebook.cpp_code_analysis import (
    CppItem,
    classify_source_spans,
    mask_comments_and_strings,
)

COMMENT_TOKEN = "//"
INCLUDE_LINE = "#include <clm/display.hpp>"
INCLUDE_CELL_HEADER = '// %% tags=["keep"]'
_INCLUDE_RE = re.compile(r'^\s*#include\s*[<"]clm/display\.hpp[>"]')
_ANY_INCLUDE_RE = re.compile(r"^\s*#include\b")

_SHOW_CALL_RE = re.compile(r"\b(SHOW|CLM_DISPLAY)\s*\(")
# A name right after one of these is a member, a qualified name or a declarator
# (``obj.SHOW(``, ``ns::SHOW(``, ``int SHOW(``), unless the word is one of the
# statement keywords below, which can precede a real macro use.
_LAST_TOKEN_RE = re.compile(r"(::|\.|->|\w+)\s*$")
_STATEMENT_KEYWORDS = frozenset({"else", "do", "return"})
# A namespace-like block does not change the scope a call is at. The linkage
# string must sit right before the brace: ``extern "C" int f() {`` is a function.
_NAMESPACE_BLOCK_RE = re.compile(r'\bnamespace\b|\bextern\s*"[^"]*"\s*$')
# ``[`` after one of these subscripts or declares an array (``v[0]``,
# ``f()[0]``, ``"abc"[0]``, ``T{}[0]``), as it does after a name (``operator[]``,
# ``new int[2]``), except for the operator-like keywords below.
_SUBSCRIPT_PRECEDERS = frozenset(")]}\"'")
_LAST_WORD_RE = re.compile(r"\w+$")
_EXPRESSION_KEYWORDS = frozenset(
    {
        "and",
        "bitand",
        "bitor",
        "co_await",
        "co_return",
        "co_yield",
        "compl",
        "not",
        "not_eq",
        "or",
        "return",
        "throw",
        "xor",
    }
)
_AFTER_LAMBDA_INTRODUCER_RE = re.compile(
    r"\s*(?:[({<]|->|\[\[|(?:mutable|constexpr|consteval|static|noexcept|requires)\b)"
)
# Measured in the 1.34.3 lite image (xcpp20), see the tests of the validator
# rule: a ``SHOW`` holding a lambda kills the kernel as a top-level statement
# and anywhere below these statements, even in a body nested there, and
# survives in the body of a top-level definition and below ``if`` / ``else`` /
# ``while`` / ``do`` / ``try``. (A braceless ``else`` is an item of its own.)
_FATAL_STATEMENT_RE = re.compile(r"\s*(?:(?:for|switch)\b|\{)")
_SURVIVING_STATEMENT_RE = re.compile(r"\s*(?:if|else|while|do|try)\b")
_MAX_CALL_TEXT = 60


@define
class ShowRewrite:
    """One display expression turned into a ``SHOW`` call."""

    line_number: int
    """1-based line of the cell header in the original file."""
    before: str
    after: str


@define(frozen=True)
class ShowCall:
    """One ``SHOW(...)`` / ``CLM_DISPLAY(...)`` call in a code cell's source."""

    text: str
    """The call, on one line and shortened to a readable length."""
    at_namespace_scope: bool
    """Outside all braces, not counting those of a namespace or linkage block."""
    kills_kernel: bool
    """Holds an inline lambda at a place where that kills the xeus-cpp kernel (#1044)."""


@define
class DeckRewrite:
    """Outcome of :func:`rewrite_deck_text` for one deck."""

    text: str
    rewrites: list[ShowRewrite] = Factory(list)
    skipped_global: list[tuple[int, str]] = Factory(list)
    """``(cell header line, expression)`` of displays in ``global`` cells, untouched."""
    skipped_lambda: list[tuple[int, str]] = Factory(list)
    """``(cell header line, expression)`` of displays holding an inline lambda, untouched."""
    unmatched: list[tuple[int, str]] = Factory(list)
    """Displays whose text could not be located verbatim in the cell (left as is)."""
    include_added: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.rewrites) or self.include_added


def _closing(masked: str, open_at: int, opener: str, closer: str) -> int:
    """Position of the bracket matching the one at ``open_at``, or -1."""
    depth = 0
    for i in range(open_at, len(masked)):
        if masked[i] == opener:
            depth += 1
        elif masked[i] == closer:
            depth -= 1
            if depth == 0:
                return i
    return -1


def _has_lambda_introducer(masked: str) -> bool:
    """Whether masked C++ source holds a lambda expression.

    A ``[`` opens a lambda where an expression can start. An attribute
    (``[[...]]``) is the only other thing that can start there, and only a
    lambda continues with a parameter list, a body or a specifier after the
    ``]``, which also rules out a designator (``{[0] = 1}``).
    """
    at = masked.find("[")
    while at >= 0:
        before = masked[:at].rstrip()
        previous = before[-1:]
        close = _closing(masked, at, "[", "]")
        word = _LAST_WORD_RE.search(before)
        is_subscript = previous in _SUBSCRIPT_PRECEDERS or (
            word is not None and word.group() not in _EXPRESSION_KEYWORDS
        )
        if (
            previous
            and not is_subscript
            and close > 0
            and masked[at + 1] != "["
            and _AFTER_LAMBDA_INTRODUCER_RE.match(masked, close + 1)
        ):
            return True
        # Also look inside an outer ``[...]``: ``v[[] { return 0; }()]``.
        at = masked.find("[", at + 1)
    return False


def contains_inline_lambda(expr: str) -> bool:
    """Whether a C++ expression holds a lambda expression, comments and strings aside."""
    # The leading ``(`` puts the start of ``expr`` where an expression can start.
    return _has_lambda_introducer("(" + mask_comments_and_strings(expr))


def _is_macro_use(masked: str, at: int) -> bool:
    token = _LAST_TOKEN_RE.search(masked[:at])
    return token is None or token.group(1) in _STATEMENT_KEYWORDS


def show_macro_positions(masked: str) -> list[tuple[int, str]]:
    """``(position, macro name)`` of every display-macro use in *masked* source.

    *masked* is source with comments and string contents blanked
    (:func:`mask_comments_and_strings`). A member, a qualified name or a
    declarator called ``SHOW`` is not a use of the macro.
    """
    return [
        (match.start(), match.group(1))
        for match in _SHOW_CALL_RE.finditer(masked)
        if _is_macro_use(masked, match.start())
    ]


def _in_definition_body(masked: str, at: int) -> bool:
    """Whether ``at`` is inside braces other than a namespace-like block's."""
    stack: list[bool] = []
    boundary = 0
    for i in range(at):
        c = masked[i]
        if c == "{":
            stack.append(_NAMESPACE_BLOCK_RE.search(masked[boundary:i]) is None)
            boundary = i + 1
        elif c == "}":
            if stack:
                stack.pop()
            boundary = i + 1
        elif c == ";":
            boundary = i + 1
    return any(stack)


def _one_line(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= _MAX_CALL_TEXT else flat[: _MAX_CALL_TEXT - 3] + "..."


def find_show_calls(source: str) -> list[ShowCall]:
    """The display-macro calls of a code cell, comments, strings and ``#`` lines aside.

    A call inside another call's argument is part of the outer one. A call
    with no closing parenthesis is not reported: the cell is mid-edit or
    templated, and any verdict on it would be a guess.
    """
    calls: list[ShowCall] = []
    if not _SHOW_CALL_RE.search(source):
        return calls
    for item in classify_source_spans(source):
        if item.category in ("include", "preproc_other"):
            continue
        masked = mask_comments_and_strings(item.original)
        fatal_statement = _FATAL_STATEMENT_RE.match(masked) is not None
        surviving_statement = _SURVIVING_STATEMENT_RE.match(masked) is not None
        pos = 0
        while match := _SHOW_CALL_RE.search(masked, pos):
            pos = match.end()
            if not _is_macro_use(masked, match.start()):
                continue
            close = _closing(masked, match.end() - 1, "(", ")")
            if close < 0:
                continue
            pos = close + 1
            in_body = _in_definition_body(masked, match.start())
            has_lambda = _has_lambda_introducer(masked[match.end() - 1 : close])
            kills_kernel = has_lambda and (fatal_statement or not (in_body or surviving_statement))
            calls.append(
                ShowCall(
                    text=_one_line(item.original[match.start() : close + 1]),
                    at_namespace_scope=not in_body,
                    kills_kernel=kills_kernel,
                )
            )
    return calls


def is_display_item(item: CppItem) -> bool:
    """Whether the classifier saw a value the kernel used to display.

    A call followed by a brace block (``TEST_P(Suite, Name) { … }``, a
    macro-defined function) is a definition, not a display.
    """
    if item.category == "expr_display":
        return True
    return item.category == "call_stmt" and not item.text.endswith((";", "}"))


def _split_leading_comments(original: str) -> tuple[str, str]:
    """``(comment/blank lines before the code, the rest)``."""
    lines = original.split("\n")
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("//"):
            i += 1
        elif stripped.startswith("/*"):
            while i < len(lines) and "*/" not in lines[i]:
                i += 1
            i += 1
        else:
            break
    return "\n".join(lines[:i]), "\n".join(lines[i:])


def wrap_in_show(original: str) -> str:
    """``x  // note`` → ``SHOW(x);  // note``; leading comment lines stay."""
    prefix, rest = _split_leading_comments(original)
    masked = mask_comments_and_strings(rest)
    end = len(masked.rstrip())
    expr, tail = rest[:end], rest[end:]
    wrapped = f"SHOW({expr});{tail}"
    return f"{prefix}\n{wrapped}" if prefix else wrapped


def _find_item(body: str, original: str, pos: int) -> int:
    """Position of ``original`` in ``body`` at or after ``pos``, or -1.

    The slice must start a line (only whitespace before it on that line)
    and end one (only whitespace or a comment after it): a bare ``arg``
    display must not be found inside the ``int arg{1};`` above it.
    """
    while True:
        at = body.find(original, pos)
        if at < 0:
            return -1
        line_start = body.rfind("\n", 0, at) + 1
        end = at + len(original)
        line_end = body.find("\n", end)
        line_end = len(body) if line_end < 0 else line_end
        after = body[end:line_end].strip()
        if not body[line_start:at].strip() and (not after or after.startswith(("//", "/*"))):
            return at
        pos = at + 1


def looks_like_prose(expr: str) -> bool:
    """A backtick never occurs in C++: the cell holds prose, not code."""
    return "`" in expr


def _rewrite_cell_body(body: str) -> tuple[str, list[tuple[str, str]], list[str], list[str]]:
    """Rewrite one code cell's body.

    Returns ``(body, [(before, after)], unmatched, skipped_lambda)``.
    """
    items = classify_source_spans(body)
    pos = 0
    out: list[str] = []
    done: list[tuple[str, str]] = []
    unmatched: list[str] = []
    skipped_lambda: list[str] = []
    for item in items:
        if not is_display_item(item) or not item.original:
            continue
        if contains_inline_lambda(item.original):
            skipped_lambda.append(item.original.strip())
            continue
        at = _find_item(body, item.original, pos)
        if at < 0 or looks_like_prose(item.original):
            unmatched.append(item.original.strip())
            continue
        replacement = wrap_in_show(item.original)
        out.append(body[pos:at])
        out.append(replacement)
        pos = at + len(item.original)
        done.append((item.original.strip(), replacement.strip()))
    out.append(body[pos:])
    return "".join(out), done, unmatched, skipped_lambda


def _display_items(body: str) -> list[CppItem]:
    return [item for item in classify_source_spans(body) if is_display_item(item)]


def _is_include_only(cell: RawCell) -> bool:
    lines = [line for line in cell.lines[1:] if line.strip()]
    return bool(lines) and all(_ANY_INCLUDE_RE.match(line) for line in lines)


def _has_display_include(cells: list[RawCell]) -> bool:
    return any(
        _INCLUDE_RE.match(line)
        for cell in cells
        if cell.metadata.cell_type == "code"
        for line in cell.lines[1:]
    )


def _add_include(cells: list[RawCell]) -> bool:
    """Make ``clm/display.hpp`` available; returns whether the deck changed."""
    if _has_display_include(cells):
        return False
    for index, cell in enumerate(cells):
        if cell.metadata.cell_type != "code" or cell.metadata.is_j2:
            continue
        if _is_include_only(cell):
            last = max(i for i, line in enumerate(cell.lines) if _ANY_INCLUDE_RE.match(line))
            cell.lines.insert(last + 1, INCLUDE_LINE)
        else:
            cells.insert(
                index,
                RawCell(
                    lines=[INCLUDE_CELL_HEADER, INCLUDE_LINE, ""],
                    line_number=cell.line_number,
                    metadata=parse_cell_header(INCLUDE_CELL_HEADER, COMMENT_TOKEN),
                ),
            )
        return True
    return False


def rewrite_deck_text(text: str) -> DeckRewrite:
    """Rewrite one deck's percent-format text; the input is never modified."""
    preamble, cells = split_cells(text, COMMENT_TOKEN)
    result = DeckRewrite(text=text)
    for cell in cells:
        meta = cell.metadata
        if meta.cell_type != "code" or meta.is_j2:
            continue
        body = cell.body
        if "global" in meta.tags:
            result.skipped_global.extend(
                (cell.line_number, item.original.strip())
                for item in _display_items(body)
                if item.original
            )
            continue
        new_body, done, unmatched, skipped_lambda = _rewrite_cell_body(body)
        result.unmatched.extend((cell.line_number, expr) for expr in unmatched)
        result.skipped_lambda.extend((cell.line_number, expr) for expr in skipped_lambda)
        if done:
            cell.lines = [cell.header, *new_body.split("\n")]
            result.rewrites.extend(ShowRewrite(cell.line_number, b, a) for b, a in done)
    if result.rewrites:
        result.include_added = _add_include(cells)
    if result.changed:
        result.text = reconstruct(preamble, cells)
    return result


def iter_cpp_slide_files(paths: Iterable[Path]) -> Iterator[Path]:
    """The ``*.cpp`` slide files under ``paths`` (files or directories), sorted."""
    seen: set[Path] = set()
    for path in paths:
        candidates = [path] if path.is_file() else sorted(path.rglob("*.cpp"))
        for candidate in candidates:
            if ".ipynb_checkpoints" in candidate.parts or candidate in seen:
                continue
            seen.add(candidate)
            yield candidate


def rewrite_deck_file(path: Path, *, dry_run: bool = False) -> DeckRewrite:
    """Rewrite one file in place (unless ``dry_run``); line endings are kept."""
    raw = path.read_bytes()
    crlf = b"\r\n" in raw
    text = raw.decode("utf-8").replace("\r\n", "\n")
    result = rewrite_deck_text(text)
    if result.changed and not dry_run:
        out = result.text.replace("\n", "\r\n") if crlf else result.text
        path.write_bytes(out.encode("utf-8"))
    return result
