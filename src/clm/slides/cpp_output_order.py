"""Find C++ cells whose notebook output comes out in the wrong order (#1228).

The xeus-cpp kernel captures a cell's output on two channels and replays
them one after the other: first everything written through ``std::cout`` —
the ``SHOW`` / ``CLM_DISPLAY`` macros of ``clm/display.hpp`` included — then
everything written through C stdio, which is where ``std::print`` and
``std::println`` (C++23) write, like ``printf`` and ``puts``. Flushing does
not help. So a cell that writes

.. code-block:: cpp

    std::println("before");
    std::cout << "after\\n";

shows ``after`` above ``before`` in the notebook, while the exported program
prints the two in program order. Measured in the notebook image built for
#1228 (xeus-cpp 0.10.0, ``xcpp20`` and ``xcpp23``); the cases are the
parameters of ``tests/slides/test_cpp_output_order.py``.

Only orders the notebook provably gets wrong are reported:

- a C-stdio write that is textually **before** a ``std::cout`` write, in
  straight-line code;
- both kinds of write inside one **loop** — the second iteration's
  ``std::cout`` output is displayed before the first iteration's C-stdio
  output;
- both kinds of write inside one **function or lambda body**, which is
  reordered as soon as it is called twice (and at once if the C-stdio write
  comes first).

``std::cout`` output followed by C-stdio output in straight-line code is
displayed correctly and is not reported. Neither is
``std::println(std::cout, ...)``, which writes through ``std::cout``.

This is a text-level check, not a C++ parser: it reads the cell with
comments, string contents and preprocessor lines blanked. Where it cannot
tell what a brace block is, it treats the block as straight-line code and
stays silent rather than guess. It does not follow calls: a function that
prints, called next to a ``std::cout`` write, is not seen.
"""

from __future__ import annotations

import re

from attrs import define

from clm.slides.cpp_show import show_macro_positions
from clm.workers.notebook.cpp_code_analysis import mask_comments_and_strings

# ``std::cout``, or a bare ``cout`` that is not a member or a name of another
# namespace (``using std::cout;`` decks).
_COUT_RE = re.compile(r"\bstd\s*::\s*cout\b|(?<![\w.>:])cout\b")
# Qualified only: an unqualified ``print(...)`` is far more often a deck's own
# function than ``std::print`` behind a using-declaration.
_STD_PRINT_RE = re.compile(r"\bstd\s*::\s*(print|println)\s*\(")
_C_STDIO_RE = re.compile(r"(?<![\w.>:])(?:std\s*::\s*)?(printf|puts)\s*\(")
# First argument of ``std::print``: a format string literal means stdout.
# Anything else is a stream or ``FILE*`` (``std::cout``, ``stderr``, a file) —
# or a format held in a variable, which this check cannot tell from a stream.
_STRING_LITERAL_START_RE = re.compile(r'\s*(?:(?:u8|[uUL])?R?")')
_NO_ARGUMENT_RE = re.compile(r"\s*\)")

_LOOP_KEYWORD_RE = re.compile(r"\b(?:for|while)\s*\(")
_LOOP_HEAD_RE = re.compile(r"\s*(?:else\s+)?(?:(?:for|while)\b|do\s*$)")
_CONTROL_HEAD_RE = re.compile(r"\s*(?:if|else|switch|catch|try|return|case|default)\b")
_TRAILING_SPECIFIERS = (
    r"(?:\s*(?:const|volatile|mutable|constexpr|consteval|static|override|final|&&?|"
    r"noexcept(?:\s*\([^(){};]*\))?))*"
    r"(?:\s*->\s*[^{};]+?)?"
)
# ``...) [specifiers] [-> type]`` right before the brace: a function body.
_FUNCTION_HEAD_RE = re.compile(r"\)" + _TRAILING_SPECIFIERS + r"\s*$")
# ``[captures] [specifiers] [-> type]`` right before the brace: a lambda
# without a parameter list. (With one, the head ends like a function's.)
_LAMBDA_HEAD_RE = re.compile(r"\]" + _TRAILING_SPECIFIERS + r"\s*$")


@define(frozen=True)
class OutputOrderProblem:
    """A cell whose notebook output is displayed in another order than written."""

    stdio_writer: str
    """The C-stdio writer involved, as written: ``std::println``, ``printf``, …"""
    stream_writer: str
    """The ``std::cout`` writer involved: ``std::cout``, ``SHOW`` or ``CLM_DISPLAY``."""
    reason: str
    """``"order"`` (C stdio written first), ``"loop"`` or ``"function"``."""


@define(frozen=True)
class _Write:
    position: int
    name: str


@define(frozen=True)
class _Region:
    start: int
    end: int
    kind: str  # "loop" or "function"

    def holds(self, position: int) -> bool:
        return self.start < position < self.end


def _blank_preprocessor_lines(masked: str) -> str:
    """Blank ``#`` directives, continuation lines included, keeping positions."""
    lines = masked.split("\n")
    in_directive = False
    for index, line in enumerate(lines):
        if in_directive or line.lstrip().startswith("#"):
            in_directive = line.rstrip().endswith("\\")
            lines[index] = " " * len(line)
        else:
            in_directive = False
    return "\n".join(lines)


def _stream_writes(masked: str) -> list[_Write]:
    writes = [_Write(match.start(), "std::cout") for match in _COUT_RE.finditer(masked)]
    writes += [_Write(position, name) for position, name in show_macro_positions(masked)]
    return sorted(writes, key=lambda write: write.position)


def _stdio_writes(masked: str) -> list[_Write]:
    writes: list[_Write] = []
    for match in _STD_PRINT_RE.finditer(masked):
        if _STRING_LITERAL_START_RE.match(masked, match.end()) or _NO_ARGUMENT_RE.match(
            masked, match.end()
        ):
            writes.append(_Write(match.start(), f"std::{match.group(1)}"))
    writes += [_Write(match.start(), match.group(1)) for match in _C_STDIO_RE.finditer(masked)]
    return sorted(writes, key=lambda write: write.position)


def _block_kind(head: str) -> str | None:
    """What the brace block after ``head`` is: a loop, a function-like body, or neither."""
    if _LOOP_HEAD_RE.match(head):
        return "loop"
    if _CONTROL_HEAD_RE.match(head):
        return None
    if _FUNCTION_HEAD_RE.search(head) or _LAMBDA_HEAD_RE.search(head):
        return "function"
    return None


def _statement_end(masked: str, start: int) -> int:
    """Position of the ``;`` that ends the statement starting at ``start``."""
    depth = 0
    for i in range(start, len(masked)):
        c = masked[i]
        if c in "({[":
            depth += 1
        elif c in ")}]":
            if depth == 0:
                return i
            depth -= 1
        elif c == ";" and depth == 0:
            return i
    return len(masked)


def _closing_paren(masked: str, open_at: int) -> int:
    depth = 0
    for i in range(open_at, len(masked)):
        if masked[i] == "(":
            depth += 1
        elif masked[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _regions(masked: str) -> list[_Region]:
    """The loop bodies and function-like bodies of masked source."""
    regions: list[_Region] = []
    # Brace blocks. ``head`` is the text since the last statement boundary;
    # a ``;`` inside parentheses (a ``for`` header) is not one.
    stack: list[tuple[int, str | None, int]] = []
    head_start = 0
    parens = 0
    for i, c in enumerate(masked):
        if c == "(":
            parens += 1
        elif c == ")":
            parens = max(0, parens - 1)
        elif c == "{":
            stack.append((i, _block_kind(masked[head_start:i]), parens))
            head_start = i + 1
            parens = 0
        elif c == "}":
            if stack:
                open_at, kind, parens = stack.pop()
                if kind is not None:
                    regions.append(_Region(open_at, i, kind))
            head_start = i + 1
        elif c == ";" and parens == 0:
            head_start = i + 1
    # Loops whose body is a single statement: ``for (...) statement;``.
    for match in _LOOP_KEYWORD_RE.finditer(masked):
        close = _closing_paren(masked, match.end() - 1)
        if close < 0:
            continue
        body = close + 1
        while body < len(masked) and masked[body].isspace():
            body += 1
        if body >= len(masked) or masked[body] in "{;":
            continue  # a brace block (handled above), or the tail of ``do … while (…);``
        regions.append(_Region(close, _statement_end(masked, body) + 1, "loop"))
    return regions


def _scope_of(position: int, functions: list[_Region]) -> _Region | None:
    """The innermost function-like body holding ``position`` (``None``: cell level)."""
    holding = [region for region in functions if region.holds(position)]
    return max(holding, key=lambda region: region.start) if holding else None


def find_output_order_problem(source: str) -> OutputOrderProblem | None:
    """The first way a code cell's notebook output is reordered, if any.

    See the module docstring for what counts. One problem per cell is
    enough: the fix — one output channel per cell — is the same for all.
    """
    masked = _blank_preprocessor_lines(mask_comments_and_strings(source))
    stream_writes = _stream_writes(masked)
    if not stream_writes:
        return None
    stdio_writes = _stdio_writes(masked)
    if not stdio_writes:
        return None

    regions = _regions(masked)
    functions = [region for region in regions if region.kind == "function"]
    loops = [region for region in regions if region.kind == "loop"]

    # Writes are compared within one scope only: the cell's own statements, or
    # one function-like body. Two functions that each use one channel mix
    # nothing by being defined in the same cell.
    scopes: dict[_Region | None, tuple[list[_Write], list[_Write]]] = {}
    for write in stdio_writes:
        scopes.setdefault(_scope_of(write.position, functions), ([], []))[0].append(write)
    for write in stream_writes:
        scopes.setdefault(_scope_of(write.position, functions), ([], []))[1].append(write)

    for scope, (stdio, stream) in sorted(
        scopes.items(), key=lambda item: -1 if item[0] is None else item[0].start
    ):
        if not stdio or not stream:
            continue
        if scope is not None:
            return OutputOrderProblem(stdio[0].name, stream[0].name, "function")
        later_stream = [write for write in stream if write.position > stdio[0].position]
        if later_stream:
            return OutputOrderProblem(stdio[0].name, later_stream[0].name, "order")
        for loop in loops:
            in_loop_stdio = [write for write in stdio if loop.holds(write.position)]
            in_loop_stream = [write for write in stream if loop.holds(write.position)]
            if in_loop_stdio and in_loop_stream:
                return OutputOrderProblem(in_loop_stdio[0].name, in_loop_stream[0].name, "loop")
    return None
