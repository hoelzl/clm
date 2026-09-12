"""Emit per-deck C++ study material from notebook cells.

Implements the section-function export of issue #928 on top of the per-item
dispatch that #333 introduced. The input is the deck's cell sequence as one
(language × kind) view — the notebook pipeline filters and blanks cells
before this is called — carried as :class:`CppCell` records that keep the
cell type, tags, ``slide_id`` and the *pre-blank* source, none of which the
plain cell text preserves.

Output shape of the lecture file ``<stem>.cpp``::

    #include <...>                 hoisted, deduplicated
    #include <clm/display.hpp>     only when a bare expression is displayed

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

A deck with workshop ranges or ``global`` cells is a *file set* instead
(:class:`CppDeckExport`; names from :mod:`clm.core.cpp_export_files`)::

    <stem>.hpp             #pragma once, the hoisted includes, and the
                           ``global`` cells of the lecture part
    <stem>.cpp             #include "<stem>.hpp" + the lecture sections
    <stem>_workshop_N.cpp  #include "<stem>.hpp" + the sections of workshop
                           block N (one file per run of back-to-back
                           workshop ranges, its own main)

Rules (the design record is ``docs/claude/handovers/cpp-ide-export-handover.md``):

- A section opens at every cell tagged ``slide`` or ``subslide``, code cells
  included, and at every workshop-range boundary; cells before the first
  opener form a leading section.
- The function name derives from the opener's ``slide_id`` (stable and
  language-invariant), the banner from the section's first markdown heading.
- Definitions, aliases, namespaces and preprocessor lines go to namespace
  scope in source order; statements and variable definitions stay local to
  the section function — unless a variable is referenced by a later section
  of the same file or by a namespace-scope item of the same file, in which
  case it is *promoted* to namespace scope (D2 in the handover: the corpus
  needs this ~240 times, a mandatory tag would not scale). ``global``-tagged
  cells go to the header wholesale — that is how a workshop file sees a
  lecture definition — except inside a workshop range, where they stay at
  namespace scope of the workshop file (D6: workshop definitions never
  reach the header).
- Bare display expressions are wrapped in ``CLM_DISPLAY``, which prints the
  expression text as a label, so the transcript reads ``i1 = 10``.
- Blanked (code-along) cells leave one ``// TODO: define <names>`` (or
  ``// TODO: <section heading>`` when the cell defined nothing) where the
  code would have gone — namespace scope for definitions and promoted
  variables, the header for a ``global`` cell, the section body for
  statements — and a kept cell that uses a name whose latest definition is
  missing from the view (a blanked cell, a ``completed``/``alt`` solution
  cell the view drops, or another commented-out cell) is emitted commented
  out, so the skeleton compiles as shipped. That scan is deck-global even
  when the output is split into files.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

from attrs import Factory, define

from clm.core.cpp_export_files import header_file_name, workshop_file_name
from clm.core.workshop_scope import find_workshop_ranges
from clm.workers.notebook.cpp_code_analysis import (
    STATEMENT_CATEGORIES,
    CppItem,
    classify_source,
    classify_source_spans,
    strip_comments_and_strings,
)

# The labeled-display helper (``CLM_DISPLAY``) lives in a vendored support
# header — ``clm/data/cpp_export/include/clm/display.hpp`` — that the CMake
# export copies next to the decks and puts on the include path, so students
# see one ``#include`` instead of a template block at the top of every file.
DISPLAY_INCLUDE = "#include <clm/display.hpp>"
# The section banner needs std::cout.
_BANNER_INCLUDES = ("#include <iostream>",)

# Note above a kept cell that cannot compile until the student has typed
# the blanked code it depends on (D5 in the handover).
DANGLING_NOTE = "// depends on code you'll type above \u2014 uncomment after"

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

    An ``excluded`` cell is not part of this view at all — the solution side
    of a ``start``/``completed`` pair (or an ``alt`` variant) that the
    code-along views drop. It is never emitted and opens no section; it is
    carried only so the names it defines count as *missing* for the
    dangling-cell scan (D5), and so promotion decisions match the Completed
    view, which does contain it.
    """

    cell_type: str
    source: str
    original_source: str | None = None
    tags: tuple[str, ...] = ()
    slide_id: str | None = None
    excluded: bool = False

    @property
    def is_code(self) -> bool:
        return self.cell_type == "code"

    @property
    def is_markdown(self) -> bool:
        return self.cell_type == "markdown"

    @property
    def opens_section(self) -> bool:
        return not self.excluded and ("slide" in self.tags or "subslide" in self.tags)

    @property
    def pre_blank_source(self) -> str:
        return self.source if self.original_source is None else self.original_source


@define(frozen=True)
class CppDeckExport:
    """The file set the export writes for one deck.

    ``main`` is ``<stem>.cpp``. ``header`` is ``<stem>.hpp`` — present only
    when the deck has workshop ranges or ``global`` cells (then ``main``
    includes it). ``workshops`` holds ``(ordinal, text)`` for every workshop
    block of the deck (a run of back-to-back workshop ranges), 1-based in
    deck order; each becomes ``<stem>_workshop_<ordinal>.cpp`` and includes
    the header.
    """

    main: str
    header: str | None = None
    workshops: tuple[tuple[int, str], ...] = ()
    # Per workshop ordinal, the lecture-defined names (functions, types,
    # variables of untagged lecture cells) its code uses. Those live in
    # ``<stem>.cpp`` and are invisible to the workshop file, so the deck
    # author has to tag their cells ``global``; the processor reports them.
    workshop_lecture_uses: dict[int, tuple[str, ...]] = Factory(dict)

    def companion_files(self, stem: str) -> dict[str, str]:
        """The files to write next to ``<stem>.cpp``, by file name."""
        files: dict[str, str] = {}
        if self.header is not None:
            files[header_file_name(stem)] = self.header
        for ordinal, text in self.workshops:
            files[workshop_file_name(stem, ordinal)] = text
        return files


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
    """Whether identifier ``name`` occurs in (comment/string-stripped) ``text``.

    A member access (``p.x``, ``p->x``) or a qualified name (``ns::x``)
    never refers to the top-level entity ``x``, so those are skipped.
    """
    return re.search(rf"(?<![\w:.])(?<!->){re.escape(name)}(?!\w)", text) is not None


def _comment_out(text: str) -> str:
    """Prefix every line of ``text`` with ``// ``."""
    return "\n".join(f"// {line}" if line.strip() else "//" for line in text.split("\n"))


def _uses(name: str, text: str) -> bool:
    """Like :func:`_references`, but a qualified ``ns::name`` counts too.

    The dangling scan tracks entities by their unqualified name, so a
    reference through a namespace (``frac::Fraction``) must match.
    """
    return re.search(rf"(?<![\w.])(?<!->){re.escape(name)}(?!\w)", text) is not None


def _uses_member(name: str, text: str) -> bool:
    """Whether ``text`` accesses a member called ``name`` (``p.f``, ``p->f``, ``T::f``)."""
    return re.search(rf"(?:\.|->|::)\s*{re.escape(name)}(?!\w)", text) is not None


# Words that precede an identifier without declaring it (``return x;``).
_NOT_A_TYPE = frozenset(
    "return delete throw new case goto co_return co_yield else using namespace "
    "struct class enum typename template operator sizeof".split()
)


def _declared_locally(name: str, text: str) -> bool:
    """Whether ``text`` declares its own ``name`` (``for (int i{1}; …``).

    A type-like token, optional ``*``/``&``, the name and an initializer or
    terminator, on one line (a bare display ``x`` above ``i = 10;`` must not
    read as ``x i = 10``). Function-local declarations are not top-level
    items, so the lecture-use report needs this to tell a workshop's own
    loop variable from a lecture variable of the same name.
    """
    pattern = (
        rf"(?<![\w:.])([A-Za-z_][\w:<>]*)[ \t]*[*&]*[ \t]+(?:const[ \t]+)?{re.escape(name)}\b"
        rf"\s*(?=[{{=;,)\[])"
    )
    return any(m.group(1) not in _NOT_A_TYPE for m in re.finditer(pattern, text))


def _short_name(name: str) -> str:
    """The unqualified identifier of a classifier name (``A::f<int>`` → ``f``)."""
    return name.split("<")[0].split("::")[-1]


_MEMBER_CATEGORIES = frozenset({"member_fn_def", "member_var_def"})


def _namespace_body(text: str) -> str:
    """The text between the outer braces of a ``namespace X { ... }`` item."""
    start = text.find("{")
    end = text.rfind("}")
    return text[start + 1 : end] if 0 <= start < end else ""


def _entity_items(items: Sequence[CppItem]) -> list[CppItem]:
    """``items`` with every ``namespace_def`` replaced by its (nested) members.

    A class defined inside ``namespace poly { ... }`` is an entity of the
    deck like any other; the classifier only sees the namespace block.
    """
    out: list[CppItem] = []
    for item in items:
        if item.category == "namespace_def":
            out.extend(_entity_items(classify_source(_namespace_body(item.text))))
        else:
            out.append(item)
    return out


def _defined_names(items: Sequence[CppItem]) -> set[str]:
    """Short names of the entities ``items`` define (members excluded)."""
    return {
        _short_name(item.name)
        for item in _entity_items(items)
        if item.name and item.category not in _MEMBER_CATEGORIES
    }


def _defined_members(items: Sequence[CppItem]) -> set[str]:
    """Short names of the out-of-class members ``items`` define."""
    return {
        _short_name(item.name)
        for item in _entity_items(items)
        if item.name and item.category in _MEMBER_CATEGORIES
    }


def _operator_operands(items: Sequence[CppItem], deck_names: frozenset[str]) -> set[str]:
    """Deck-defined types an operator overload in ``items`` is declared for.

    An operator cannot be tracked by name (``a * b`` never spells
    ``operator*``), so a missing overload makes its operand types missing
    instead — every later cell that touches the type is then commented
    out, which is broader than necessary but compiles.
    """
    types: set[str] = set()
    for item in _entity_items(items):
        if not item.name or "operator" not in item.name:
            continue
        signature = item.text.split("{", 1)[0]
        types.update(name for name in deck_names if _uses(name, signature))
    return types


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


#: File id of the lecture file ``<stem>.cpp``; workshop files use their ordinal.
_MAIN_FILE = 0


@define
class _Section:
    index: int
    opener: CppCell | None
    cells: list[CppCell]
    file: int = _MAIN_FILE
    name: str = ""
    heading: str = ""


@define
class _VarSlot:
    """A top-level variable definition and where it sits."""

    section: int
    file: int
    order: int
    item: CppItem
    promoted: bool = False


def merge_adjacent_workshop_ranges(ranges: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """Fold back-to-back workshop ranges into one *workshop block*.

    The canonical detector opens a new range at every ``workshop`` tag or
    ``workshop-…`` slide_id, so a workshop whose tasks are ``workshop-task-1``,
    ``workshop-task-2``… sub-slides is several ranges — for blanking that
    makes no difference, but the tasks build on each other and belong in one
    file. In the corpus every multi-range deck is one such run. Ranges that
    are separated — by an ``end-workshop`` closer or lecture cells — stay
    separate blocks (and files).
    """
    merged: list[tuple[int, int]] = []
    for start, end in ranges:
        if merged and merged[-1][1] == start:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def _split_sections(
    cells: Sequence[CppCell], workshop_ranges: Sequence[tuple[int, int]]
) -> list[_Section]:
    """Group ``cells`` into sections, each assigned to one output file.

    A section opens at every section-opening cell and at every workshop
    boundary (the range's first cell and the first cell after it), so no
    section straddles two files; the cells of workshop range ``k`` (1-based)
    belong to file ``k``, every other cell to the lecture file.
    """
    boundaries = {start for start, _ in workshop_ranges} | {
        end for _, end in workshop_ranges if end < len(cells)
    }

    def file_of(index: int) -> int:
        for ordinal, (start, end) in enumerate(workshop_ranges, start=1):
            if start <= index < end:
                return ordinal
        return _MAIN_FILE

    sections: list[_Section] = []
    current = _Section(index=0, opener=None, cells=[])
    for i, cell in enumerate(cells):
        if cell.opens_section or i in boundaries:
            if current.cells or current.opener is not None:
                sections.append(current)
            current = _Section(index=len(sections), opener=cell, cells=[], file=file_of(i))
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


@define
class _CellWriter:
    """Adds one cell's rendered items to a stream, commented out if dangling.

    The dangling note is written once per stream the cell touches, ahead of
    the first commented-out item.
    """

    cell: int
    dangling: bool
    noted: set[int] = Factory(set)

    def add(self, stream: _Stream, text: str) -> None:
        if self.dangling:
            if id(stream) not in self.noted:
                self.noted.add(id(stream))
                stream.add("comment", self.cell, DANGLING_NOTE)
            text = _comment_out(text)
        stream.add("code", self.cell, text)


class _DeckEmitter:
    def __init__(
        self,
        cells: Sequence[CppCell],
        *,
        stem: str,
        blanks_code_cells: bool,
        workshop_ranges: Sequence[tuple[int, int]] | None,
    ) -> None:
        self.stem = stem
        self.blanks_code_cells = blanks_code_cells
        if workshop_ranges is None:
            workshop_ranges = find_workshop_ranges(cells)
        self.workshop_ranges = merge_adjacent_workshop_ranges(workshop_ranges)
        self.sections = _split_sections(cells, self.workshop_ranges)
        self.includes: list[str] = []
        self.include_keys: set[str] = set()
        # Files whose kept cells define ``main`` (no ``main`` is generated there).
        self.defines_main: set[int] = set()
        self.uses_display = False
        self.uses_banner = False
        # ``global`` cells of the lecture part; rendered into the header.
        self.header_stream = _Stream([])
        self.has_header_cells = False
        # Per section, per cell: classified items (None for non-code cells).
        self.items: dict[tuple[int, int], list[CppItem]] = {}
        self.vars: list[_VarSlot] = []
        # Cells whose source is blank in this view (code-along); their
        # items are classified from the pre-blank source and only decide
        # where the TODO goes and what it names.
        self.blanked: set[tuple[int, int]] = set()
        # Cells not in this view at all (see ``CppCell.excluded``).
        self.excluded: set[tuple[int, int]] = set()
        # Kept cells that reference a name whose latest definition is
        # missing from the view; emitted commented out.
        self.dangling: set[tuple[int, int]] = set()

    # -- pass 1: classify and decide promotions -----------------------------

    def _is_blanked(self, cell: CppCell) -> bool:
        """Whether ``cell`` is an empty code cell that had content before.

        With an ``original_source`` snapshot the answer is exact — an empty
        cell that was empty in the source is not a TODO even for a spec that
        blanks (Partial blanks only its workshop range). Without a snapshot
        the spec's ``blanks_code_cells`` flag decides.
        """
        if not cell.is_code or cell.source.strip():
            return False
        if cell.original_source is not None:
            return bool(cell.original_source.strip())
        return self.blanks_code_cells

    def _classify(self) -> None:
        order = 0
        for section in self.sections:
            for ci, cell in enumerate(section.cells):
                if not cell.is_code:
                    continue
                key = (section.index, ci)
                if cell.excluded:
                    if not cell.pre_blank_source.strip():
                        continue
                    self.excluded.add(key)
                    self.items[key] = classify_source_spans(cell.pre_blank_source)
                    continue
                if self._is_blanked(cell):
                    self.blanked.add(key)
                    source = cell.pre_blank_source
                elif cell.source.strip():
                    source = cell.source
                else:
                    continue
                items = classify_source_spans(source)
                self.items[key] = items
                if "global" in cell.tags:
                    if self._in_header(cell, section):
                        self.has_header_cells = True
                    continue
                for item in items:
                    order += 1
                    if item.category == "var_decl" and item.name:
                        self.vars.append(_VarSlot(section.index, section.file, order, item))

    @staticmethod
    def _in_header(cell: CppCell, section: _Section) -> bool:
        """Whether a ``global`` cell lands in the header.

        Only lecture-part ``global`` cells do; inside a workshop range they
        stay at namespace scope of the workshop file (D6).
        """
        return "global" in cell.tags and section.file == _MAIN_FILE

    @property
    def has_header(self) -> bool:
        return bool(self.workshop_ranges) or self.has_header_cells

    _GLOBAL = -1
    _MISSING = -2

    def _find_dangling(self, promoted: set[int]) -> None:
        """Mark kept cells that depend on code the student has yet to type.

        Deck-global (D5), in cell order, latest definition wins: a name
        defined by a blanked cell, an excluded solution cell, or a cell that
        is itself dangling (so the closure holds) is *missing*; a name
        defined by an emitted cell is available — deck-wide when it lands
        at namespace scope, within its section when it stays local. A kept
        cell that references a missing name (outside its own definitions),
        or accesses a missing member, is dangling. Only earlier cells
        count: a kept section may reuse a local name a later workshop cell
        blanks. Operator overloads are tracked through their operand types.
        """
        deck_names = self._deck_names()
        state: dict[str, int] = {}
        missing_members: set[str] = set()
        for section in self.sections:
            for ci, cell in enumerate(section.cells):
                key = (section.index, ci)
                items = self.items.get(key)
                if items is None:
                    continue
                own = _defined_names(items)
                own_members = _defined_members(items)
                if key in self.blanked or key in self.excluded:
                    for name in own | _operator_operands(items, deck_names):
                        state[name] = self._MISSING
                    missing_members |= own_members
                    continue
                text = strip_comments_and_strings(cell.source)
                missing = {name for name, st in state.items() if st == self._MISSING}
                dangling = any(_uses(name, text) for name in missing - own) or any(
                    _uses_member(name, text) for name in missing_members - own_members
                )
                if dangling:
                    self.dangling.add(key)
                    for name in own:
                        state[name] = self._MISSING
                    missing_members |= own_members
                    continue
                is_global = "global" in cell.tags
                for item in _entity_items(items):
                    if not item.name or item.category in _MEMBER_CATEGORIES:
                        continue
                    local = (
                        item.category == "var_decl" and not is_global and id(item) not in promoted
                    )
                    state[_short_name(item.name)] = section.index if local else self._GLOBAL
                missing_members -= own_members

    def _namespace_texts(self) -> dict[int, list[str]]:
        """Per file, comment-stripped text of every item landing at namespace scope.

        Blanked cells count too: the student types their definitions back at
        namespace scope, so a variable such a definition uses must be
        promoted for the typed-in code to compile. Header cells are left
        out: the header is included ahead of every file, so no promotion
        could make a lecture variable visible to it.
        """
        texts: dict[int, list[str]] = {}
        for section in self.sections:
            file_texts = texts.setdefault(section.file, [])
            for ci, cell in enumerate(section.cells):
                items = self.items.get((section.index, ci))
                if items is None:
                    continue
                if "global" in cell.tags:
                    if not self._in_header(cell, section):
                        file_texts.extend(item.text for item in items)
                    continue
                file_texts.extend(
                    item.text for item in items if item.category in _HOISTED_CATEGORIES
                )
        return texts

    def _later_code(self, section: _Section) -> str:
        """Comment-stripped pre-blank code of the later sections of ``section``'s file.

        A workshop file cannot see the lecture file's namespace scope, so a
        reference from another file never promotes (the author tags the
        definition ``global`` instead, D8).
        """
        parts = [
            strip_comments_and_strings(cell.pre_blank_source)
            for later in self.sections[section.index + 1 :]
            if later.file == section.file
            for cell in later.cells
            if cell.is_code
        ]
        return "\n".join(parts)

    def _decide_promotions(self) -> None:
        namespace_texts = self._namespace_texts()
        later_code = {s.index: self._later_code(s) for s in self.sections}
        for slot in self.vars:
            name = slot.item.name
            assert name is not None
            if _references(name, later_code[slot.section]):
                slot.promoted = True
                continue
            if any(
                text is not slot.item.text and _references(name, text)
                for text in namespace_texts.get(slot.file, ())
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
        """Return ``(namespace_scope_text, function_name_or_None)``.

        Lecture-part ``global`` cells are written to :attr:`header_stream`
        instead of the section's namespace scope.
        """
        namespace = _Stream([])
        body = _Stream([])
        for ci, cell in enumerate(section.cells):
            # Chunk ids must be unique across sections: the header stream
            # collects cells of many sections.
            cell_id = (section.index << 16) | ci
            if cell.is_markdown:
                if cell.source.strip():
                    target = body if body.has_code() else namespace
                    target.add("comment", cell_id, _comment_block(cell.source))
                continue
            if not cell.is_code:
                continue
            key = (section.index, ci)
            if key in self.excluded:
                continue
            items = self.items.get(key)
            in_header = self._in_header(cell, section)
            scope = self.header_stream if in_header else namespace
            if key in self.blanked:
                if items is None:
                    # No snapshot; the spec says the cell was blanked.
                    body.add("code", cell_id, f"// TODO: {section.heading}")
                    continue
                target = self._todo_target(cell, items, promoted, scope, body)
                target.add("code", cell_id, self._todo_text(section, items))
                continue
            if items is None:
                continue
            is_global = "global" in cell.tags
            dangling = key in self.dangling
            add = _CellWriter(cell_id, dangling=dangling).add
            for item in items:
                cat = item.category
                text = item.original.strip()
                if cat == "include":
                    self._add_include(text)
                elif cat == "main_def":
                    # A dangling main is commented out like the rest of its
                    # cell, so the file still gets a generated main.
                    if not dangling:
                        self.defines_main.add(section.file)
                    add(scope, text)
                elif is_global:
                    add(scope, _terminate(text))
                elif cat == "using_directive" and self.has_header and section.file == _MAIN_FILE:
                    # A ``using namespace`` directive is environment, like an
                    # include: in the notebook it applied to every later
                    # cell, workshop included, so it is shared via the header.
                    add(self.header_stream, _terminate(text))
                elif cat == "expr_display" or (cat == "call_stmt" and not text.endswith(";")):
                    # A bare call without `;` also relied on the kernel's
                    # auto-display; the helper's void branch makes the wrap
                    # safe for calls that don't return a value.
                    self.uses_display = True
                    add(body, _wrap_display(text))
                elif cat in STATEMENT_CATEGORIES or cat == "unknown":
                    # ``unknown`` is rare; treat it as a statement and let the
                    # compile check flag it if that guess is wrong.
                    add(body, _terminate(text))
                elif cat == "var_decl":
                    add(namespace if id(item) in promoted else body, _terminate(text))
                else:
                    add(namespace, _terminate(text))
        function_name: str | None = None
        if body:
            self.uses_banner = True
            function_name = section.name
            banner = f'std::cout << "== {_cpp_string(section.heading)} ==\\n";'
            fn = f"void {function_name}() {{\n{_indent(banner)}\n{_indent(body.render())}\n}}"
            # The function is its own block: never glued to the item before it.
            namespace.add("code", -1, fn)
        return namespace.render(), function_name

    @staticmethod
    def _todo_target(
        cell: CppCell,
        items: Sequence[CppItem],
        promoted: set[int],
        scope: _Stream,
        body: _Stream,
    ) -> _Stream:
        """Where a blanked cell's TODO goes: where its code would have gone.

        The cell's namespace scope (``scope``: the header for a lecture
        ``global`` cell, else the section's namespace scope) if the cell is
        ``global`` or any of its items would land there (a definition, an
        include, a promoted variable) — a student cannot type a function
        definition inside the section function; otherwise the section body.
        """
        if "global" in cell.tags:
            return scope
        for item in items:
            if item.category in _HOISTED_CATEGORIES or item.category == "include":
                return scope
            if item.category == "var_decl" and id(item) in promoted:
                return scope
        return body

    @staticmethod
    def _todo_text(section: _Section, items: Sequence[CppItem]) -> str:
        names: list[str] = []
        for item in items:
            if item.name and item.name not in names:
                names.append(item.name)
        if names:
            return f"// TODO: define {', '.join(names)}"
        return f"// TODO: {section.heading}"

    def _deck_names(self) -> frozenset[str]:
        """Short names of everything any cell of the deck defines."""
        names: set[str] = set()
        for items in self.items.values():
            names |= _defined_names(items) | _defined_members(items)
        return frozenset(names)

    def emit(self) -> CppDeckExport:
        self._classify()
        _name_sections(self.sections, reserved=self._deck_names())
        self._decide_promotions()
        promoted = {id(slot.item) for slot in self.vars if slot.promoted}
        self._find_dangling(promoted)
        chunks: dict[int, list[str]] = {}
        calls: dict[int, list[str]] = {}
        for section in self.sections:
            text, function_name = self._emit_section(section, promoted)
            if text:
                chunks.setdefault(section.file, []).append(text)
            if function_name:
                calls.setdefault(section.file, []).append(f"{function_name}();")

        if self.uses_banner:
            for forced in _BANNER_INCLUDES:
                self._add_include(forced)
        if self.uses_display:
            self._add_include(DISPLAY_INCLUDE)

        if not self.has_header:
            return CppDeckExport(main=self._assemble(self.includes, _MAIN_FILE, chunks, calls))

        header_parts = ["#pragma once"]
        if self.includes:
            header_parts.append("\n".join(self.includes))
        if self.header_stream:
            header_parts.append(self.header_stream.render())
        header_include = [f'#include "{header_file_name(self.stem)}"']
        return CppDeckExport(
            main=self._assemble(header_include, _MAIN_FILE, chunks, calls),
            header="\n\n".join(header_parts) + "\n",
            workshops=tuple(
                (ordinal, self._assemble(header_include, ordinal, chunks, calls))
                for ordinal in range(1, len(self.workshop_ranges) + 1)
            ),
            workshop_lecture_uses=self._workshop_lecture_uses(),
        )

    def _workshop_lecture_uses(self) -> dict[int, tuple[str, ...]]:
        """Per workshop, the lecture-file names its (pre-blank) code uses.

        Lecture definitions stay in ``<stem>.cpp`` next to their narrative
        (D8); a workshop that needs one must have its cell tagged ``global``.
        Names already in the header, names the workshop defines itself
        (top-level or as a local declaration), qualified names and member
        accesses are not reported. Scanned on the pre-blank source so the
        report is the same for every view of the deck. Advisory: the compile
        gate is the authority, this only names the likely cause.
        """
        lecture_names: set[str] = set()
        for section in self.sections:
            if section.file != _MAIN_FILE:
                continue
            for ci, cell in enumerate(section.cells):
                items = self.items.get((section.index, ci))
                if items is None or self._in_header(cell, section):
                    continue
                lecture_names |= _defined_names(items)
        uses: dict[int, tuple[str, ...]] = {}
        for ordinal in range(1, len(self.workshop_ranges) + 1):
            own: set[str] = set()
            parts: list[str] = []
            for section in self.sections:
                if section.file != ordinal:
                    continue
                for ci, cell in enumerate(section.cells):
                    items = self.items.get((section.index, ci))
                    if items is None:
                        continue
                    own |= _defined_names(items)
                    parts.append(strip_comments_and_strings(cell.pre_blank_source))
            text = "\n".join(parts)
            used = tuple(
                sorted(
                    name
                    for name in lecture_names - own
                    if _references(name, text) and not _declared_locally(name, text)
                )
            )
            if used:
                uses[ordinal] = used
        return uses

    def _assemble(
        self,
        includes: Sequence[str],
        file: int,
        chunks: dict[int, list[str]],
        calls: dict[int, list[str]],
    ) -> str:
        """Render one translation unit: includes, its sections, its ``main``."""
        parts: list[str] = []
        if includes:
            parts.append("\n".join(includes))
        parts.extend(chunks.get(file, ()))
        if file not in self.defines_main:
            file_calls = calls.get(file, [])
            if file_calls:
                body = "\n".join(f"    {call}" for call in file_calls)
                parts.append(f"int main() {{\n{body}\n}}")
            else:
                parts.append("int main() {}")
        return "\n\n".join(parts) + "\n"


def emit_cpp_deck(
    cells: Sequence[CppCell],
    *,
    stem: str = "deck",
    blanks_code_cells: bool = False,
    workshop_ranges: Sequence[tuple[int, int]] | None = None,
) -> CppDeckExport:
    """Emit the study-material file set of a deck from its cells.

    ``cells`` must already reflect the desired (language × kind) view. A
    blanked cell (empty ``source`` with a non-empty ``original_source``)
    leaves one ``// TODO`` where its code would have gone; with
    ``blanks_code_cells`` (code-along-style variants) an empty code cell
    without an ``original_source`` counts as blanked too. ``excluded``
    cells (solution cells the view drops) are never emitted but count as
    missing definitions. Kept cells that depend on missing code are emitted
    commented out.

    ``stem`` names the deck output (``<stem>.cpp``); the header and workshop
    files are named after it. ``workshop_ranges`` are half-open index
    ranges into ``cells``; when ``None`` they are detected from the cells
    themselves (the notebook pipeline passes the ranges it computed on the
    full cell list, which may contain cells the view dropped). Back-to-back
    ranges form one workshop block and one file. Every text ends in a
    newline.
    """
    return _DeckEmitter(
        cells, stem=stem, blanks_code_cells=blanks_code_cells, workshop_ranges=workshop_ranges
    ).emit()
