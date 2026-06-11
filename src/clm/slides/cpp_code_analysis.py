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
    """

    category: str
    name: str | None = None
    signature: str | None = None
    text: str = ""


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


def split_top_level(src: str) -> list[str]:
    """Split comment/string-stripped C++ into top-level items.

    Splits on ``;`` at depth 0 and on a ``}`` that closes back to depth 0 —
    except when the closing brace is followed by ``;`` (the ``;`` ends the
    item) or by ``else``/``while``/``catch`` (an ``if {} else {}``,
    ``do {} while``, or ``try {} catch`` continuation of the same item).
    """
    items: list[str] = []
    brace = paren = bracket = 0
    start = 0
    i, n = 0, len(src)

    def emit(end: int) -> None:
        nonlocal start
        item = src[start:end].strip()
        if item:
            items.append(item)
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
                    if not word:
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
    return items


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
    fm = _FN_RE.match(text) or _CTOR_RE.match(text)
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
                return CppItem("fn_decl", name, sig, text=text)
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
