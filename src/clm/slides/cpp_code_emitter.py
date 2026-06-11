"""Emit a compilable C++ translation unit from notebook code cells.

Implements the per-item dispatch of the C++ code export (#333). The previous
``format="code"`` output was a jupytext concatenation of the notebook — for
C++ that yields top-level statements, mid-file ``#include``\\ s, and bare
auto-display expressions, which is not valid C++. Instead, each top-level
item of each (already variant-filtered) code cell is routed to its proper
place in a translation unit:

- ``#include`` lines are hoisted to the top and deduplicated,
- other preprocessor lines, definitions, and global variables stay at
  namespace scope in cell order (within-TU dynamic initialization order is
  declaration order, so cling-style top-level state carries over 1:1),
- statements are wrapped in a ``void slide_NN()`` function per cell, called
  in order from a generated ``main()``,
- bare display expressions (which relied on the kernel's auto-display) are
  wrapped in a ``CLM_DISPLAY`` helper that prints the value when an
  ``operator<<`` exists and a placeholder otherwise.

Decks that define their own ``main()`` suppress the generated one.

The heuristics live in :mod:`clm.slides.cpp_code_analysis`; this module only
routes and formats. See ``REPORT_CPP_CODE_EXPORT_FEASIBILITY.md`` in the
CppCourses repo for the corpus numbers behind the design.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from clm.slides.cpp_code_analysis import (
    STATEMENT_CATEGORIES,
    classify_source_spans,
)

# C++20 (the course standard): concepts and if-constexpr drive the
# operator<<-availability fallback at compile time.
_DISPLAY_HELPER = """\
// Replicates the notebook's automatic display of bare expressions: prints
// the value if it has an operator<<, a placeholder otherwise.
namespace clm {

template <typename T>
concept Streamable = requires(std::ostream& os, const T& value) { os << value; };

template <typename ExprThunk>
void display(ExprThunk&& expr_thunk) {
    if constexpr (std::is_void_v<std::invoke_result_t<ExprThunk>>) {
        std::forward<ExprThunk>(expr_thunk)();
    } else {
        decltype(auto) value = std::forward<ExprThunk>(expr_thunk)();
        if constexpr (Streamable<std::remove_cvref_t<decltype(value)>>) {
            const auto flags = std::cout.flags();
            std::cout << std::boolalpha << value << "\\n";
            std::cout.flags(flags);
        } else {
            std::cout << "<unprintable value>\\n";
        }
    }
}

}  // namespace clm

#define CLM_DISPLAY(...) ::clm::display([&]() -> decltype(auto) { return (__VA_ARGS__); })"""

# Includes the display helper itself needs.
_DISPLAY_INCLUDES = ("#include <iostream>", "#include <type_traits>", "#include <utility>")

_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _include_key(line: str) -> str:
    """Dedupe key for an ``#include`` line: code only, whitespace-free."""
    code = _BLOCK_COMMENT_RE.sub(" ", _LINE_COMMENT_RE.sub(" ", line))
    return re.sub(r"\s+", "", code)


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line if line.strip() else line for line in text.split("\n"))


def _terminate(text: str) -> str:
    """Append a missing ``;``, dodging a trailing line comment if present."""
    if text.endswith((";", "}")):
        return text
    last_line = text.rsplit("\n", 1)[-1]
    if "//" in last_line:
        return text + "\n;"
    return text + ";"


def _wrap_display(expr: str) -> str:
    """Wrap a bare display expression in the ``CLM_DISPLAY`` helper.

    A line comment inside the expression would swallow an inline ``);``, so
    multi-line or commented expressions get the closing paren on its own line.
    """
    if "//" in expr or "/*" in expr or "\n" in expr:
        return f"CLM_DISPLAY(\n{_indent(expr)}\n);"
    return f"CLM_DISPLAY({expr});"


def emit_cpp_translation_unit(
    cell_sources: Sequence[str], *, empty_cells_as_todo: bool = False
) -> str:
    """Emit one compilable translation unit from C++ code-cell sources.

    ``cell_sources`` must already reflect the desired (language × kind) view —
    the notebook pipeline filters cells before this is called. Returns the TU
    text, ending in a newline.

    With ``empty_cells_as_todo`` (code-along-style variants), empty cells —
    which the pipeline blanked for live coding — become ``slide_NN()`` stubs
    with a ``// TODO`` body, still called from the generated ``main()``, so
    students have a compilable place to write each cell's code.
    """
    includes: list[str] = []
    include_keys: set[str] = set()
    chunks: list[str] = []
    slide_calls: list[str] = []
    deck_defines_main = False
    uses_display = False
    slide_number = 0

    for source in cell_sources:
        if not source.strip():
            if empty_cells_as_todo:
                slide_number += 1
                name = f"slide_{slide_number:02d}"
                chunks.append(f"void {name}() {{\n    // TODO\n}}")
                slide_calls.append(f"{name}();")
            continue
        defs: list[str] = []
        stmts: list[str] = []
        for item in classify_source_spans(source):
            cat = item.category
            text = item.original.strip()
            if cat == "include":
                key = _include_key(text)
                if key not in include_keys:
                    include_keys.add(key)
                    includes.append(text)
            elif cat == "preproc_other":
                defs.append(text)
            elif cat == "main_def":
                deck_defines_main = True
                defs.append(text)
            elif cat == "expr_display" or (cat == "call_stmt" and not text.endswith(";")):
                # A bare call without `;` also relied on the kernel's
                # auto-display; the helper's void branch makes the wrap safe
                # for calls that don't return a value.
                uses_display = True
                stmts.append(_wrap_display(text))
            elif cat in STATEMENT_CATEGORIES or cat == "unknown":
                # ``unknown`` is rare (3 items in the whole corpus, all in a
                # disabled deck); treat it as a statement and let the compile
                # check flag it if that guess is wrong.
                stmts.append(_terminate(text))
            else:
                # Definitions, declarations, globals, aliases, using
                # directives, namespaces: namespace scope, in cell order.
                defs.append(_terminate(text))
        if defs:
            chunks.append("\n\n".join(defs))
        if stmts:
            slide_number += 1
            name = f"slide_{slide_number:02d}"
            body = "\n".join(_indent(s) for s in stmts)
            chunks.append(f"void {name}() {{\n{body}\n}}")
            slide_calls.append(f"{name}();")

    if uses_display:
        for forced in _DISPLAY_INCLUDES:
            key = _include_key(forced)
            if key not in include_keys:
                include_keys.add(key)
                includes.append(forced)

    if not deck_defines_main:
        if slide_calls:
            body = "\n".join(f"    {call}" for call in slide_calls)
            chunks.append(f"int main() {{\n{body}\n}}")
        else:
            chunks.append("int main() {}")

    parts: list[str] = []
    if includes:
        parts.append("\n".join(includes))
    if uses_display:
        parts.append(_DISPLAY_HELPER)
    parts.extend(chunks)
    return "\n\n".join(parts) + "\n"
