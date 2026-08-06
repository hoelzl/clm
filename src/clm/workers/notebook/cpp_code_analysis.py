"""Heuristic top-level item analysis for C++ code cells.

Classifies the top-level items of a C++ code-cell body (includes, type /
function / variable definitions, statements, display expressions, ...) without
a real C++ parser. This drives the ``code_export`` validation rules (#331) and
will drive the per-item dispatch of the compilable project export (#333).

The heuristics were developed and validated against the full CppCourses
corpus (298 decks, 4,818 top-level items, 99.9% classified; see
``tools/scan_cell_structure.py`` and
``course-planning/REPORT_CPP_CODE_EXPORT_FEASIBILITY.md`` in that repo).
A libclang-based classifier could replace this module later without changing
the call sites.

Pipeline: :func:`strip_comments_and_strings` -> :func:`extract_preprocessor`
-> :func:`split_top_level` -> :func:`classify_item`, or all at once via
:func:`classify_source`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Item model
# ---------------------------------------------------------------------------


@dataclass
class CppItem:
    """One classified top-level item of a code cell.

    ``name`` is set for definitions/declarations that introduce an entity
    (variables, functions, types, aliases). For template specializations the
    specialization arguments are part of the name (``TypeName<int>``), since
    a specialization is a distinct entity from its primary template.

    ``signature`` is set for functions: ``name(normalized-param-types)`` with
    a trailing `` const`` marker for const member functions, so that legal
    overloads compare unequal while true redefinitions compare equal.

    ``text`` is the comment/string-stripped item text used for
    classification. ``original`` (only filled by :func:`classify_source_spans`)
    is the corresponding slice of the original source — comments and literal
    contents intact — for re-emission by the code export.
    """

    category: str
    name: str | None = None
    signature: str | None = None
    text: str = ""
    original: str = ""


# Categories that introduce a named entity at namespace scope.
DEFINITION_CATEGORIES = frozenset(
    {"type_def", "fn_def", "member_fn_def", "alias_def", "namespace_def", "fn_decl", "type_decl"}
)
# Categories that are executable statements (need wrapping into a function
# body for the code export).
STATEMENT_CATEGORIES = frozenset(
    {"control_stmt", "output_stmt", "call_stmt", "expr_stmt", "expr_display", "block_stmt"}
)
# Preprocessor categories (hoisted to the top of a translation unit).
PREPROCESSOR_CATEGORIES = frozenset({"include", "preproc_other"})


# ---------------------------------------------------------------------------
# Comment / string stripping
# ---------------------------------------------------------------------------


def strip_comments_and_strings(src: str) -> str:
    """Blank out comments and string/char literals (incl. raw strings).

    Preserves the structural characters (braces, parens, semicolons) the
    splitter and classifier rely on; replaces literals with empty
    placeholders so their contents can't confuse them.
    """
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and nxt == "*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
            continue
        if c == "R" and nxt == '"':
            m = re.match(r'R"([^(\s]*)\(', src[i:])
            if m:
                closer = ")" + m.group(1) + '"'
                j = src.find(closer, i + m.end())
                i = n if j < 0 else j + len(closer)
                out.append('""')
                continue
        if c == '"':
            j = i + 1
            while j < n and src[j] != '"':
                j += 2 if src[j] == "\\" else 1
            out.append('""')
            i = j + 1
            continue
        if c == "'":
            j = i + 1
            while j < n and src[j] != "'":
                j += 2 if src[j] == "\\" else 1
            out.append("' '")
            i = j + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def mask_comments_and_strings(src: str) -> str:
    """Length-preserving variant of :func:`strip_comments_and_strings`.

    Replaces comment text and string/char literal *contents* with spaces
    while keeping every character position — in particular every newline —
    intact, and keeping the quote/delimiter characters themselves. The result
    lines up 1:1 with the original source, so item spans computed on the
    masked text can slice the original. Raw-string delimiters (``R"d(`` /
    ``)d"``) are kept, so parens stay balanced for the splitter.
    """
    out = list(src)

    def blank(a: int, b: int) -> None:
        for k in range(a, min(b, len(out))):
            if out[k] != "\n":
                out[k] = " "

    i, n = 0, len(src)
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            j = src.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
            continue
        if c == "/" and nxt == "*":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            blank(i, j)
            i = j
            continue
        if c == "R" and nxt == '"':
            m = re.match(r'R"([^(\s]*)\(', src[i:])
            if m:
                closer = ")" + m.group(1) + '"'
                j = src.find(closer, i + m.end())
                if j < 0:
                    blank(i + m.end(), n)
                    i = n
                else:
                    blank(i + m.end(), j)
                    i = j + len(closer)
                continue
        if c == '"':
            j = i + 1
            while j < n and src[j] != '"':
                j += 2 if src[j] == "\\" else 1
            blank(i + 1, j)
            i = j + 1
            continue
        if c == "'":
            j = i + 1
            while j < n and src[j] != "'":
                j += 2 if src[j] == "\\" else 1
            blank(i + 1, j)
            i = j + 1
            continue
        i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# Preprocessor extraction / top-level splitting
# ---------------------------------------------------------------------------


def extract_preprocessor(src: str) -> tuple[list[str], str]:
    """Pull out preprocessor lines (with continuations); return (pp_lines, rest)."""
    pp: list[str] = []
    rest: list[str] = []
    lines = src.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("#"):
            full = line
            while full.rstrip().endswith("\\") and i + 1 < len(lines):
                i += 1
                full = full.rstrip()[:-1] + " " + lines[i]
            pp.append(full.strip())
        else:
            rest.append(line)
        i += 1
    return pp, "\n".join(rest)


# Characters that continue an expression after a closing ``}`` at depth 0.
# A brace-init temporary can sit mid-expression
# (``RequestBuilder{}.setTimeout(10).send();``,
# ``auto n = std::vector<int>{1, 2}.size();``) — a ``}`` followed by one of
# these must not end the item.
_EXPR_CONTINUATION_CHARS = frozenset(".,)]([<>+-*/%&|^?:!~=")


def split_top_level_spans(src: str) -> list[tuple[int, int]]:
    """Like :func:`split_top_level`, but return ``(start, end)`` char spans.

    Spans whose text is pure whitespace are dropped; the remaining spans may
    carry leading/trailing whitespace (callers strip as needed). Computed on
    masked (length-preserving) text, the spans are valid in the original
    source — this is how the code export recovers items with their comments
    and string contents intact.
    """
    spans: list[tuple[int, int]] = []
    brace = paren = bracket = 0
    start = 0
    i, n = 0, len(src)

    def emit(end: int) -> None:
        nonlocal start
        if src[start:end].strip():
            spans.append((start, end))
        start = end

    while i < n:
        c = src[i]
        if c == "{":
            brace += 1
        elif c == "}":
            brace -= 1
            if brace == 0 and paren == 0 and bracket == 0:
                j = i + 1
                while j < n and src[j] in " \t\r\n":
                    j += 1
                if j < n and src[j] == ";":
                    i = j
                    emit(i + 1)
                else:
                    word = re.match(r"(else|while|catch)\b", src[j : j + 8])
                    expr_continues = j < n and src[j] in _EXPR_CONTINUATION_CHARS
                    if not word and not expr_continues:
                        emit(i + 1)
        elif c == "(":
            paren += 1
        elif c == ")":
            paren -= 1
        elif c == "[":
            bracket += 1
        elif c == "]":
            bracket -= 1
        elif c == ";" and brace == 0 and paren == 0 and bracket == 0:
            emit(i + 1)
        i += 1
    emit(n)
    return spans


def split_top_level(src: str) -> list[str]:
    """Split comment/string-stripped C++ into top-level items.

    Splits on ``;`` at depth 0 and on a ``}`` that closes back to depth 0 —
    except when the closing brace is followed by ``;`` (the ``;`` ends the
    item) or by ``else``/``while``/``catch`` (an ``if {} else {}``,
    ``do {} while``, or ``try {} catch`` continuation of the same item).
    """
    return [src[a:b].strip() for a, b in split_top_level_spans(src)]


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

_SPECIFIERS = (
    r"(?:(?:const|constexpr|consteval|constinit|static|inline|virtual|extern"
    r"|friend|unsigned|signed|long|short|mutable)\s+)*"
)
_TYPE_TOKEN = r"[\w:]+(?:<[^;{}]*>)?"
_CONTROL_KW = (
    "if",
    "for",
    "while",
    "do",
    "switch",
    "try",
    "return",
    "throw",
    "break",
    "continue",
    "goto",
)

_QUAL = r"(?:\w+(?:<[^;{}()]*>)?\s*::\s*)*"
_FN_RE = re.compile(
    rf"^{_SPECIFIERS}{_TYPE_TOKEN}(?:\s+const\b|\s*[&*]+|\s+)+"
    rf"({_QUAL}~?\w+(?:<[^;{{}}()]*>)?|{_QUAL}operator\s*\S+)\s*\(",
)
# Out-of-class ctor/dtor definitions have no return type: MyVector<T>::MyVector(...)
_CTOR_RE = re.compile(r"^((?:\w+(?:<[^;{}()]*>)?\s*::\s*)+~?\w+)\s*\(")
_VAR_RE = re.compile(
    rf"^{_SPECIFIERS}{_TYPE_TOKEN}(?:\s+const\b|\s*[&*]+|\s+)+(\w+)\s*"
    rf"((?:\[[^\]]*\]\s*)*)(\{{|=|;|\(|,)",
)
# Function-pointer variables: bool (*pf)(double, double){&is_less};
_FNPTR_VAR_RE = re.compile(
    rf"^{_SPECIFIERS}{_TYPE_TOKEN}(?:\s+const\b|\s*[&*]+|\s+)*\(\s*\*+\s*(\w+)\s*\)\s*\(",
)
# Out-of-class static member definitions: std::allocator<T> MyVector<T>::allocator{};
_MEMBER_VAR_RE = re.compile(
    rf"^{_SPECIFIERS}{_TYPE_TOKEN}(?:\s+const\b|\s*[&*]+|\s+)+{_QUAL}(\w+)\s*(\{{|=|;)",
)
_TYPE_DEF_RE = re.compile(r"^(?:class|struct|union|enum(?:\s+(?:class|struct))?)\s+(\w+)")


def normalize_args(args: str) -> str:
    """Strip parameter names and defaults so overloads compare by type.

    ``int x, double const& y = 1.0`` -> ``int,doubleconst&``. Whitespace is
    removed entirely; the result is only used for equality comparison.
    """
    parts = []
    depth = 0
    cur = ""
    for ch in args + ",":
        if ch == "," and depth == 0:
            a = re.sub(r"=.*$", "", cur).strip()
            a = re.sub(r"\s+", " ", a)
            toks = a.split(" ")
            if len(toks) > 1 and re.fullmatch(r"\w+", toks[-1]):
                a = " ".join(toks[:-1])
            parts.append(a.replace(" ", ""))
            cur = ""
            continue
        if ch in "<([":
            depth += 1
        elif ch in ">)]":
            depth -= 1
        cur += ch
    return ",".join(p for p in parts if p)


def classify_item(item: str) -> CppItem:
    """Classify one comment/string-stripped top-level item."""
    text = re.sub(r"\s+", " ", item).strip()
    text = re.sub(r"^(\[\[[^\]]*\]\]\s*)+", "", text)  # strip [[nodiscard]] etc.
    if not text:
        return CppItem("empty", text=text)
    if text.startswith("template"):
        # Classify by the declaration after the template<...> intro; the
        # template parameters don't change the category or the entity name.
        depth = 0
        for k, ch in enumerate(text):
            if ch == "<":
                depth += 1
            elif ch == ">":
                depth -= 1
                if depth == 0:
                    inner = classify_item(text[k + 1 :])
                    inner.text = text
                    return inner
        return CppItem("unknown", text=text)
    m = _TYPE_DEF_RE.match(text)
    if m:
        cat = "type_def" if "{" in text else "type_decl"
        name = m.group(1)
        # Template specializations are distinct entities: TypeName<int>
        spec = re.match(rf"{re.escape(name)}\s*(<[^{{;]*>)", text[m.start(1) :])
        if spec:
            name += re.sub(r"\s+", "", spec.group(1))
        return CppItem(cat, name, text=text)
    if re.match(r"^(?:class|struct|union|enum(?:\s+(?:class|struct))?)\s*\{", text):
        # Anonymous type definition (e.g. `enum { RED, GREEN };`): no name to
        # track for redefinitions, but it must stay at namespace scope so its
        # members remain visible to later cells.
        return CppItem("type_def", text=text)
    if text.startswith("namespace"):
        return CppItem("namespace_def", text=text)
    if re.match(r"^using\s+namespace\b", text):
        return CppItem("using_directive", text=text)
    if re.match(r"^using\s+\w+\s*=", text) or text.startswith("typedef"):
        nm = re.match(r"^using\s+(\w+)\s*=", text)
        return CppItem("alias_def", nm.group(1) if nm else None, text=text)
    first_word = re.match(r"^\w+", text)
    if first_word and first_word.group(0) in _CONTROL_KW:
        return CppItem("control_stmt", text=text)
    if re.match(r"^std\s*::\s*(cout|cerr|wcout)\b", text):
        return CppItem("output_stmt", text=text)
    if text.startswith("delete"):
        return CppItem("expr_stmt", text=text)
    if re.match(r"^,\s*\w+\s*(\{|=|\()", text):
        # Continuation declarator from `int i{},\n j{};` style splits.
        nm = re.match(r"^,\s*(\w+)", text)
        return CppItem("var_decl", nm.group(1) if nm else None, text=text)
    fm = _FN_RE.match(text)
    is_ctor_like = False
    if not fm:
        fm = _CTOR_RE.match(text)
        is_ctor_like = fm is not None
    if fm:
        # Find the closing paren of the arg list; then `{` => def, `;` => decl.
        open_idx = text.find("(", fm.start(1))
        depth = 0
        close_idx = -1
        for k in range(open_idx, len(text)):
            if text[k] == "(":
                depth += 1
            elif text[k] == ")":
                depth -= 1
                if depth == 0:
                    close_idx = k
                    break
        if close_idx > 0:
            tail = text[close_idx + 1 :].strip()
            args = text[open_idx + 1 : close_idx]
            name = re.sub(r"\s*::\s*", "::", fm.group(1))
            const_marker = " const" if tail.startswith("const") else ""
            sig = f"{name}({normalize_args(args)}){const_marker}"
            if "{" in tail and not tail.startswith("="):
                if name == "main":
                    return CppItem("main_def", "main", sig, text=text)
                if "::" in name:
                    return CppItem("member_fn_def", name, sig, text=text)
                return CppItem("fn_def", name, sig, text=text)
            if tail.startswith(";") or tail.rstrip(";") in ("const", "noexcept", "override"):
                if is_ctor_like and tail.startswith(";"):
                    # `std::sort(xs.begin(), xs.end());` — a qualified call,
                    # not a declaration: a member-function declaration without
                    # return type is illegal at namespace scope, so a `;` tail
                    # after the ctor-like pattern always means a call.
                    return CppItem("call_stmt", text=text)
                return CppItem("fn_decl", name, sig, text=text)
    fp = _FNPTR_VAR_RE.match(text)
    if fp:
        return CppItem("var_decl", fp.group(1), text=text)
    vm = _VAR_RE.match(text)
    if vm:
        return CppItem("var_decl", vm.group(1), text=text)
    mv = _MEMBER_VAR_RE.match(text)
    if mv:
        return CppItem("member_var_def", mv.group(1), text=text)
    if re.match(r"^(::)?\w[\w:]*\s*\(", text):
        return CppItem("call_stmt", text=text)
    if text.startswith("{"):
        return CppItem("block_stmt", text=text)
    # Expression fallback: unary-op/literal/identifier-chain expressions.
    # Bare expressions (no trailing `;`) relied on the kernel's auto-display.
    expr_head = re.match(
        r"^(\+\+|--|[!*&~+\-(.]|::|\d|true\b|false\b|nullptr\b|' '|\"\"|static_cast\b|sizeof\b|\w[\w:]*)",
        text,
    )
    if expr_head:
        body = text.rstrip(";").strip()
        # A `{` is fine when it is a brace-init argument or functional cast,
        # i.e. it appears after a `(` or `=`; a leading-position `{` we failed
        # to parse stays unknown.
        brace_pos = body.find("{")
        ok = (
            brace_pos < 0
            or re.match(r"^[\w:]+(<[^{}]*>)?\s*\{", body)
            or (0 <= body.find("(") < brace_pos)
            or (0 <= body.find("=") < brace_pos)
        )
        if ok:
            cat = "expr_stmt" if text.endswith(";") else "expr_display"
            return CppItem(cat, text=text)
    return CppItem("unknown", text=text)


def classify_source(source: str) -> list[CppItem]:
    """Classify all top-level items of one C++ code-cell body.

    Returns preprocessor items (``include`` / ``preproc_other``) followed by
    the classified code items, in source order within each group. ``empty``
    items are dropped.
    """
    cleaned = strip_comments_and_strings(source)
    pp_lines, rest = extract_preprocessor(cleaned)
    items: list[CppItem] = [
        CppItem("include" if pp.startswith("#include") else "preproc_other", text=pp)
        for pp in pp_lines
    ]
    for raw in split_top_level(rest):
        item = classify_item(raw)
        if item.category != "empty":
            items.append(item)
    return items


def classify_source_spans(source: str) -> list[CppItem]:
    """Like :func:`classify_source`, but with :attr:`CppItem.original` filled.

    Classification still happens on comment/string-stripped text (the form
    the heuristics were validated against); ``original`` carries the
    corresponding slice of the original source — comments and literal
    contents intact — for re-emission by the code export. Comments between
    items attach to the *following* item; comments after the last item are
    dropped. Preprocessor lines are extracted line-wise (mirroring
    :func:`extract_preprocessor`) and removed from the code items' originals.
    """
    masked = mask_comments_and_strings(source)
    masked_lines = masked.split("\n")
    source_lines = source.split("\n")
    items: list[CppItem] = []
    i = 0
    while i < len(masked_lines):
        if masked_lines[i].lstrip().startswith("#"):
            start_line = i
            while masked_lines[i].rstrip().endswith("\\") and i + 1 < len(masked_lines):
                i += 1
            original = "\n".join(source_lines[start_line : i + 1])
            merged = masked_lines[start_line]
            for k in range(start_line + 1, i + 1):
                merged = merged.rstrip()[:-1] + " " + masked_lines[k]
            cat = "include" if merged.strip().startswith("#include") else "preproc_other"
            items.append(CppItem(cat, text=merged.strip(), original=original))
            # Blank the pp lines in both copies so char offsets stay aligned
            # for the top-level split below.
            for k in range(start_line, i + 1):
                masked_lines[k] = ""
                source_lines[k] = ""
        i += 1
    split_masked = "\n".join(masked_lines)
    split_source = "\n".join(source_lines)
    for a, b in split_top_level_spans(split_masked):
        original = split_source[a:b].strip()
        item = classify_item(strip_comments_and_strings(original))
        if item.category != "empty":
            item.original = original
            items.append(item)
    return items
