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
scope is ill-formed (the wrapper is a capture-default lambda), so such a
cell needs the author's attention. Files are rewritten losslessly
(:mod:`clm.core.slide_text.raw_cells`); a second run changes nothing.
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


@define
class ShowRewrite:
    """One display expression turned into a ``SHOW`` call."""

    line_number: int
    """1-based line of the cell header in the original file."""
    before: str
    after: str


@define
class DeckRewrite:
    """Outcome of :func:`rewrite_deck_text` for one deck."""

    text: str
    rewrites: list[ShowRewrite] = Factory(list)
    skipped_global: list[tuple[int, str]] = Factory(list)
    """``(cell header line, expression)`` of displays in ``global`` cells, untouched."""
    unmatched: list[tuple[int, str]] = Factory(list)
    """Displays whose text could not be located verbatim in the cell (left as is)."""
    include_added: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.rewrites) or self.include_added


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
        elif stripped.startswith("/*") and "*/" in stripped:
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


def _rewrite_cell_body(body: str) -> tuple[str, list[tuple[str, str]], list[str]]:
    """Rewrite one code cell's body; returns ``(body, [(before, after)], unmatched)``."""
    items = classify_source_spans(body)
    pos = 0
    out: list[str] = []
    done: list[tuple[str, str]] = []
    unmatched: list[str] = []
    for item in items:
        if not is_display_item(item) or not item.original:
            continue
        at = body.find(item.original, pos)
        if at < 0:
            unmatched.append(item.original.strip())
            continue
        replacement = wrap_in_show(item.original)
        out.append(body[pos:at])
        out.append(replacement)
        pos = at + len(item.original)
        done.append((item.original.strip(), replacement.strip()))
    out.append(body[pos:])
    return "".join(out), done, unmatched


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
        new_body, done, unmatched = _rewrite_cell_body(body)
        result.unmatched.extend((cell.line_number, expr) for expr in unmatched)
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
