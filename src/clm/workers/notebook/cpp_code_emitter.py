"""Emit per-deck C++ study material from notebook cells.

Implements the section-function export of issue #928 on top of the per-item
dispatch that #333 introduced. The input is the deck's cell sequence as one
(language × kind) view — the notebook pipeline filters and blanks cells
before this is called — carried as :class:`CppCell` records that keep the
cell type, tags, ``slide_id`` and the *pre-blank* source, none of which the
plain cell text preserves.

Output shape (one translation unit)::

    #include <...>                 hoisted, deduplicated
    <display helper>               only when a bare expression is displayed

    // ## Brace initialization     markdown preceding the section's first
    int helper(int x) { ... }      statement, and namespace-scope items
                                   (definitions, promoted variables)
    void brace_initialization() {  one function per slide/subslide section
        std::cout << "== Brace initialization ==\\n";
        int i1{10};                statements and locals, in cell order
        // markdown inside the section becomes body comments
        CLM_DISPLAY(i1);           prints ``i1 = 10``
    }

    int main() { brace_initialization(); ... }   unless the deck defines main

Rules (the design record is ``docs/claude/handovers/cpp-ide-export-handover.md``):

- A section opens at every cell tagged ``slide`` or ``subslide``, code cells
  included; cells before the first opener form a leading section.
- The function name derives from the opener's ``slide_id`` (stable and
  language-invariant), the banner from the section's first markdown heading.
- Definitions, aliases, namespaces and preprocessor lines go to namespace
  scope in source order; statements and variable definitions stay local to
  the section function — unless a variable is referenced by a later section
  or by any namespace-scope item, in which case it is *promoted* to
  namespace scope (D2 in the handover: the corpus needs this ~240 times,
  a mandatory tag would not scale). ``global``-tagged cells go to namespace
  scope wholesale.
- Bare display expressions are wrapped in ``CLM_DISPLAY``, which prints the
  expression text as a label, so the transcript reads ``i1 = 10``.
- Blanked (code-along) cells leave a ``// TODO`` in the section body.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

from attrs import define

from clm.workers.notebook.cpp_code_analysis import (
    STATEMENT_CATEGORIES,
    CppItem,
    classify_source_spans,
    strip_comments_and_strings,
)

# C++20 (the course standard): concepts and if-constexpr drive the
# operator<<-availability fallback at compile time.
_DISPLAY_HELPER = """\
// Replicates the notebook's automatic display of bare expressions, labeled
// with the expression text: prints `expr = value` if the value has an
// operator<<, a placeholder otherwise; void expressions are just evaluated.
namespace clm {

template <typename T>
concept Streamable = requires(std::ostream& os, const T& value) { os << value; };

template <typename ExprThunk>
void display(const char* label, ExprThunk&& expr_thunk) {
    if constexpr (std::is_void_v<std::invoke_result_t<ExprThunk>>) {
        std::forward<ExprThunk>(expr_thunk)();
    } else {
        decltype(auto) value = std::forward<ExprThunk>(expr_thunk)();
        std::cout << label << " = ";
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

#define CLM_DISPLAY(...) \\
    ::clm::display(#__VA_ARGS__, [&]() -> decltype(auto) { return (__VA_ARGS__); })"""

# Includes the display helper itself needs.
_DISPLAY_INCLUDES = ("#include <iostream>", "#include <type_traits>", "#include <utility>")
# The section banner needs std::cout.
_BANNER_INCLUDES = ("#include <iostream>",)

_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")

# Categories whose items always live at namespace scope.
_HOISTED_CATEGORIES = frozenset(
    {
        "type_def",
        "type_decl",
        "fn_def",
        "fn_decl",
        "member_fn_def",
        "member_var_def",
        "alias_def",
        "namespace_def",
        "using_directive",
        "preproc_other",
        "main_def",
    }
)

_CPP_KEYWORDS = frozenset(
    """
    alignas alignof and and_eq asm auto bitand bitor bool break case catch char
    char8_t char16_t char32_t class compl concept const consteval constexpr
    constinit const_cast continue co_await co_return co_yield decltype default
    delete do double dynamic_cast else enum explicit export extern false float
    for friend goto if inline int long mutable namespace new noexcept not not_eq
    nullptr operator or or_eq private protected public register reinterpret_cast
    requires return short signed sizeof static static_assert static_cast struct
    switch template this thread_local throw true try typedef typeid typename
    union unsigned using virtual void volatile wchar_t while xor xor_eq main
    """.split()
)


# ---------------------------------------------------------------------------
# Input model
# ---------------------------------------------------------------------------


@define(frozen=True)
class CppCell:
    """One notebook cell as the C++ export sees it.

    ``source`` is the cell text of the current output view (blank for a
    code-along cell that was cleared); ``original_source`` is the text before
    blanking, when the caller can supply it — it drives the reference scan
    so a variable a *blanked* later cell uses is still promoted. ``tags`` and
    ``slide_id`` come from the cell metadata before the pipeline strips it.
    """

    cell_type: str
    source: str
    original_source: str | None = None
    tags: tuple[str, ...] = ()
    slide_id: str | None = None

    @property
    def is_code(self) -> bool:
        return self.cell_type == "code"

    @property
    def is_markdown(self) -> bool:
        return self.cell_type == "markdown"

    @property
    def opens_section(self) -> bool:
        return "slide" in self.tags or "subslide" in self.tags

    @property
    def pre_blank_source(self) -> str:
        return self.source if self.original_source is None else self.original_source


# ---------------------------------------------------------------------------
# Small text helpers
# ---------------------------------------------------------------------------


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


def _comment_block(markdown: str) -> str:
    """Render a markdown cell as a ``//`` comment block."""
    lines = [line.rstrip() for line in markdown.strip("\n").split("\n")]
    return "\n".join(f"// {line}" if line else "//" for line in lines)


def _references(name: str, text: str) -> bool:
    """Whether identifier ``name`` occurs in (comment/string-stripped) ``text``."""
    return re.search(rf"(?<![\w:]){re.escape(name)}(?!\w)", text) is not None


def _cpp_string(text: str) -> str:
    """Escape ``text`` for use inside a C++ string literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def identifier_from_slide_id(slide_id: str | None) -> str | None:
    """Fold a ``slide_id`` to a C++ identifier, or ``None`` if nothing is left.

    ``brace-initialization`` → ``brace_initialization``; ``Einführung`` is
    ASCII-folded; a leading digit gets an ``s_`` prefix; keywords (and
    ``main``) get a ``_section`` suffix.
    """
    if not slide_id:
        return None
    folded = unicodedata.normalize("NFKD", slide_id).encode("ascii", "ignore").decode("ascii")
    ident = re.sub(r"[^A-Za-z0-9_]+", "_", folded).strip("_")
    ident = re.sub(r"_+", "_", ident)
    if not ident:
        return None
    if ident[0].isdigit():
        ident = f"s_{ident}"
    if ident in _CPP_KEYWORDS:
        ident = f"{ident}_section"
    return ident


def _humanize(slide_id: str) -> str:
    return re.sub(r"[-_]+", " ", slide_id).strip().capitalize()


def _first_heading(cells: Sequence[CppCell]) -> str | None:
    for cell in cells:
        if not cell.is_markdown:
            continue
        for line in cell.source.split("\n"):
            m = _HEADING_RE.match(line)
            if m:
                return m.group(1).strip()
    return None


# ---------------------------------------------------------------------------
# Sections and classified cells
# ---------------------------------------------------------------------------


@define
class _Section:
    index: int
    opener: CppCell | None
    cells: list[CppCell]
    name: str = ""
    heading: str = ""


@define
class _VarSlot:
    """A top-level variable definition and where it sits."""

    section: int
    order: int
    item: CppItem
    promoted: bool = False


def _split_sections(cells: Sequence[CppCell]) -> list[_Section]:
    sections: list[_Section] = []
    current = _Section(index=0, opener=None, cells=[])
    for cell in cells:
        if cell.opens_section:
            if current.cells or current.opener is not None:
                sections.append(current)
            current = _Section(index=len(sections), opener=cell, cells=[])
        current.cells.append(cell)
    if current.cells:
        sections.append(current)
    for i, section in enumerate(sections):
        section.index = i
    return sections


def _name_sections(sections: Sequence[_Section], reserved: frozenset[str] = frozenset()) -> None:
    """Assign function names and banner headings.

    ``reserved`` holds the names the deck itself defines (functions, types,
    variables): a section whose slide_id folds to one of them — code-derived
    ids make this likely — gets a ``_section`` suffix rather than colliding.
    """
    used: dict[str, int] = {}
    for section in sections:
        slide_id = section.opener.slide_id if section.opener is not None else None
        base = identifier_from_slide_id(slide_id) or f"section_{section.index + 1:02d}"
        if base in reserved:
            base = f"{base}_section"
        count = used.get(base, 0)
        used[base] = count + 1
        section.name = base if count == 0 else f"{base}_{count + 1}"
        heading = _first_heading(section.cells)
        if heading is None:
            heading = _humanize(slide_id) if slide_id else section.name
        section.heading = heading


# ---------------------------------------------------------------------------
# Emission
# ---------------------------------------------------------------------------


@define
class _Stream:
    """An ordered list of chunks that renders with notebook-like spacing.

    Items of one cell stay together (single newlines), cells are separated
    by a blank line, and a comment attaches to the code that follows it.
    """

    chunks: list[tuple[str, int, str]]  # (kind, cell index, text); kind: comment | code

    def add(self, kind: str, cell: int, text: str) -> None:
        self.chunks.append((kind, cell, text))

    def __bool__(self) -> bool:
        return bool(self.chunks)

    def has_code(self) -> bool:
        return any(kind == "code" for kind, _, _ in self.chunks)

    def render(self) -> str:
        out: list[str] = []
        prev: tuple[str, int] | None = None
        for kind, cell, text in self.chunks:
            if prev is not None:
                prev_kind, prev_cell = prev
                tight = prev_cell == cell or (prev_kind == "comment" and kind == "code")
                out.append("\n" if tight else "\n\n")
            out.append(text)
            prev = (kind, cell)
        return "".join(out)


class _DeckEmitter:
    def __init__(self, cells: Sequence[CppCell], *, blanks_code_cells: bool) -> None:
        self.blanks_code_cells = blanks_code_cells
        self.sections = _split_sections(cells)
        self.includes: list[str] = []
        self.include_keys: set[str] = set()
        self.deck_defines_main = False
        self.uses_display = False
        self.uses_banner = False
        # Per section, per cell: classified items (None for non-code cells).
        self.items: dict[tuple[int, int], list[CppItem]] = {}
        self.vars: list[_VarSlot] = []

    # -- pass 1: classify and decide promotions -----------------------------

    def _is_blanked(self, cell: CppCell) -> bool:
        if not cell.is_code or cell.source.strip():
            return False
        return bool(cell.pre_blank_source.strip()) or self.blanks_code_cells

    def _classify(self) -> None:
        order = 0
        for section in self.sections:
            for ci, cell in enumerate(section.cells):
                if not cell.is_code or not cell.source.strip():
                    continue
                items = classify_source_spans(cell.source)
                self.items[(section.index, ci)] = items
                if "global" in cell.tags:
                    continue
                for item in items:
                    order += 1
                    if item.category == "var_decl" and item.name:
                        self.vars.append(_VarSlot(section.index, order, item))

    def _namespace_texts(self) -> list[str]:
        """Comment-stripped text of every item that lands at namespace scope."""
        texts: list[str] = []
        for section in self.sections:
            for ci, cell in enumerate(section.cells):
                items = self.items.get((section.index, ci))
                if items is None:
                    continue
                if "global" in cell.tags:
                    texts.extend(item.text for item in items)
                    continue
                texts.extend(item.text for item in items if item.category in _HOISTED_CATEGORIES)
        return texts

    def _later_code(self, section_index: int) -> str:
        """Comment-stripped pre-blank code of every section after ``section_index``."""
        parts = [
            strip_comments_and_strings(cell.pre_blank_source)
            for section in self.sections[section_index + 1 :]
            for cell in section.cells
            if cell.is_code
        ]
        return "\n".join(parts)

    def _decide_promotions(self) -> None:
        namespace_texts = self._namespace_texts()
        later_code = {s.index: self._later_code(s.index) for s in self.sections}
        for slot in self.vars:
            name = slot.item.name
            assert name is not None
            if _references(name, later_code[slot.section]):
                slot.promoted = True
                continue
            if any(
                text is not slot.item.text and _references(name, text) for text in namespace_texts
            ):
                slot.promoted = True
        # A promoted variable's initializer may use an earlier local of the
        # same section, which the function scope would otherwise hide from it.
        changed = True
        while changed:
            changed = False
            for slot in self.vars:
                if not slot.promoted:
                    continue
                for other in self.vars:
                    if (
                        not other.promoted
                        and other.section == slot.section
                        and other.order < slot.order
                        and other.item.name
                        and _references(other.item.name, slot.item.text)
                    ):
                        other.promoted = True
                        changed = True

    # -- pass 2: emit ---------------------------------------------------------

    def _add_include(self, line: str) -> None:
        key = _include_key(line)
        if key not in self.include_keys:
            self.include_keys.add(key)
            self.includes.append(line)

    def _emit_section(self, section: _Section, promoted: set[int]) -> tuple[str, str | None]:
        """Return ``(namespace_scope_text, function_name_or_None)``."""
        namespace = _Stream([])
        body = _Stream([])
        for ci, cell in enumerate(section.cells):
            if cell.is_markdown:
                if cell.source.strip():
                    target = body if body.has_code() else namespace
                    target.add("comment", ci, _comment_block(cell.source))
                continue
            if not cell.is_code:
                continue
            if self._is_blanked(cell):
                body.add("code", ci, "// TODO")
                continue
            items = self.items.get((section.index, ci))
            if items is None:
                continue
            is_global = "global" in cell.tags
            for item in items:
                cat = item.category
                text = item.original.strip()
                if cat == "include":
                    self._add_include(text)
                elif cat == "main_def":
                    self.deck_defines_main = True
                    namespace.add("code", ci, text)
                elif is_global:
                    namespace.add("code", ci, _terminate(text))
                elif cat == "expr_display" or (cat == "call_stmt" and not text.endswith(";")):
                    # A bare call without `;` also relied on the kernel's
                    # auto-display; the helper's void branch makes the wrap
                    # safe for calls that don't return a value.
                    self.uses_display = True
                    body.add("code", ci, _wrap_display(text))
                elif cat in STATEMENT_CATEGORIES or cat == "unknown":
                    # ``unknown`` is rare; treat it as a statement and let the
                    # compile check flag it if that guess is wrong.
                    body.add("code", ci, _terminate(text))
                elif cat == "var_decl":
                    target = namespace if id(item) in promoted else body
                    target.add("code", ci, _terminate(text))
                else:
                    namespace.add("code", ci, _terminate(text))
        function_name: str | None = None
        if body:
            self.uses_banner = True
            function_name = section.name
            banner = f'std::cout << "== {_cpp_string(section.heading)} ==\\n";'
            fn = f"void {function_name}() {{\n{_indent(banner)}\n{_indent(body.render())}\n}}"
            # The function is its own block: never glued to the item before it.
            namespace.add("code", -1, fn)
        return namespace.render(), function_name

    def _defined_names(self) -> frozenset[str]:
        names = {
            item.name.split("<")[0].split("::")[-1]
            for items in self.items.values()
            for item in items
            if item.name
        }
        return frozenset(names)

    def emit(self) -> str:
        self._classify()
        _name_sections(self.sections, reserved=self._defined_names())
        self._decide_promotions()
        promoted = {id(slot.item) for slot in self.vars if slot.promoted}
        chunks: list[str] = []
        calls: list[str] = []
        for section in self.sections:
            text, function_name = self._emit_section(section, promoted)
            if text:
                chunks.append(text)
            if function_name:
                calls.append(f"{function_name}();")

        if self.uses_display:
            for forced in _DISPLAY_INCLUDES:
                self._add_include(forced)
        if self.uses_banner:
            for forced in _BANNER_INCLUDES:
                self._add_include(forced)

        if not self.deck_defines_main:
            if calls:
                body = "\n".join(f"    {call}" for call in calls)
                chunks.append(f"int main() {{\n{body}\n}}")
            else:
                chunks.append("int main() {}")

        parts: list[str] = []
        if self.includes:
            parts.append("\n".join(self.includes))
        if self.uses_display:
            parts.append(_DISPLAY_HELPER)
        parts.extend(chunks)
        return "\n\n".join(parts) + "\n"


def emit_cpp_deck(cells: Sequence[CppCell], *, blanks_code_cells: bool = False) -> str:
    """Emit one translation unit of study material from a deck's cells.

    ``cells`` must already reflect the desired (language × kind) view. With
    ``blanks_code_cells`` (code-along-style variants) an empty code cell
    counts as blanked even when no ``original_source`` is available, and
    leaves a ``// TODO`` in its section body. Returns the text, ending in a
    newline.
    """
    return _DeckEmitter(cells, blanks_code_cells=blanks_code_cells).emit()
