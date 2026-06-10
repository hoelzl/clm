"""Slug extraction for code-typed slide cells.

Used by ``clm slides assign-ids`` as a Phase-2 fallback when
:func:`clm.slides.headingless.classify` returns ``NON_EXTRACTABLE`` on a
cell whose ``cell_type == "code"``. Walks the top-level statements with
:mod:`ast` and picks the most slug-worthy node by precedence:

    1. class definition       -> ``class <Name>``
    2. function / async def   -> ``function <name>``
    3. top-level assignment   -> ``<target>``
    4. import / from-import   -> ``import <name> [<name>...]``
    5. expression-stmt call   -> ``<obj> <method>`` (or ``<func>``)
    6. for / async for loop   -> ``for <target> in <iterable>``
    7. expression-stmt value  -> ``<name> [<key>...]`` (subscript /
                                 attribute / bare-name display, #233)
    8. first code line        -> the raw first non-comment/non-magic line
                                 (only when ``accept_code_derived`` is set)

Extractors 1-7 are intent-based: they name a salient construct.
Extractors 6-7 (#233) cover the display-style cells common on subslides:
``data[:5]`` names ``data``, ``response.headers["Content-Type"]`` names
``response headers Content-Type``, ``for student in classroom: …`` names
``for student in classroom``. They run only when ``display_exprs`` is
set: ``assign-ids`` enables it, but the sync content-anchor caller
(:func:`clm.slides.sync_writeback.construct_of`) must NOT — anchors are
re-derived against persisted watermark baselines, so widening the
extractor set there would make unchanged cells' anchors drift. A bare
expression with no salient name (``(1 + 1j) * (1 + 1j)``, ``a == b``)
still has no construct, so by default it returns ``None`` and the caller
hard-refuses — the only non-manual escape historically being the LLM
(#251). Extractor 8 is the **opt-in deterministic fallback** for exactly
those cells: it slugs the first real code line. It is
comment-token-aware (:func:`_first_code_line`) so it works across every
supported prog_lang, not just Python; ``ast`` is Python-only, so for a
``.cs`` / ``.cpp`` / ``.java`` / ``.ts`` cell the AST extractors never
fire and extractor 6 is the path that completes the deck.

Returns ``None`` when the source can't be parsed *and* no first-code-line
fallback applies (shell escapes, magic commands, blank/comment-only
cells), or no slug-worthy node is found. The caller treats ``None`` as
``NON_EXTRACTABLE`` and falls through to the existing refusal /
LLM-fallback path.

Extractor labels (``Extraction.source``) match the precedence order:
``code:class``, ``code:def``, ``code:assign``, ``code:import``,
``code:call``, ``code:for``, ``code:expr``, ``code:line``. Labels 1-7
surface in the ``assign-ids`` report as ``content:code:<kind>`` (gated by
``--accept-content-derived``); ``code:line`` keeps its bare label and is
gated by ``--accept-code-derived`` so it never silently activates inside
the content-derived funnels.
"""

from __future__ import annotations

import ast
import re

from clm.slides.headingless import Category, Extraction

# Jupyter line magics / shell escapes / help operators — never a slide's
# identity. Skipping them keeps a magic-only cell (``!pip install …``) a
# refusal, preserving the pre-1.8 behavior for the ``#``-comment-token
# languages where these tokens are meaningful (Python/Rust).
_MAGIC_PREFIXES = ("!", "%", "?")

# A code line must carry at least one ASCII alphanumeric to slugify to
# anything; a pure-punctuation line (``...``, ``()``, ``_``) slugifies to
# the empty string and must stay a refusal rather than mint a bogus id.
_HAS_ALNUM_RE = re.compile(r"[A-Za-z0-9]")

# Cap the number of import names baked into the composite title so a
# 30-line `from foo import (a, b, c, ...)` block doesn't produce an
# absurdly long proposed title. ``slug.py`` enforces a character cap
# afterwards too; this is purely for readability of the report.
_MAX_IMPORT_NAMES = 4


def extract_from_code(
    source: str,
    comment_token: str = "#",
    *,
    accept_code_derived: bool = False,
    display_exprs: bool = False,
) -> Extraction | None:
    """Return an ``EXTRACTABLE`` proposal derived from code, or ``None``.

    ``source`` is the raw body of a percent-format code cell (no comment
    prefix). ``comment_token`` is the deck's line-comment token (``"#"``
    python/rust, ``"//"`` c/c++/c#/java/ts) used only by the
    ``accept_code_derived`` fallback to recognize comment lines.

    The AST extractors (class/def/assign/import/call) run first when the
    source parses as Python. A :class:`SyntaxError` (non-Python cell, shell
    escape, magic, half-finished stub) skips them but is **not** fatal: with
    ``accept_code_derived`` the comment-token-aware first-code-line fallback
    still gets a turn, which is what lets a ``.cs`` / ``.ts`` cell — never
    parseable by :mod:`ast` — be completed. ``None`` is returned only when
    nothing matched, so the caller hard-refuses as before.

    ``accept_code_derived`` and ``display_exprs`` both default to ``False``
    so every existing caller (the content-anchor in
    :mod:`clm.slides.sync_writeback`, the direct unit tests, the four
    content-derived funnels) is byte-for-byte unchanged. ``display_exprs``
    (#233) additionally enables the for-loop / display-expression
    extractors — ``assign-ids`` sets it; the sync content-anchor must not
    (see the module docstring).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        tree = None

    if tree is not None:
        extractors = [
            _from_class_def,
            _from_function_def,
            _from_assignment,
            _from_import,
            _from_call,
        ]
        if display_exprs:
            extractors += [_from_for_loop, _from_expr_value]
        for extractor in extractors:
            result = extractor(tree)
            if result is not None:
                return result

    if accept_code_derived:
        line = _first_code_line(source, comment_token)
        if line is not None and _HAS_ALNUM_RE.search(line):
            return Extraction(Category.EXTRACTABLE, line, "code:line")

    return None


def _first_code_line(source: str, comment_token: str) -> str | None:
    """First non-blank, non-comment, non-magic source line, or ``None``.

    Comment-token-aware so it works across every supported prog_lang rather
    than only Python:

    - ``#``-token languages (Python, Rust): skip ``#`` *and* ``//`` line
      comments — so a Python ``# comment`` and a Rust ``// comment`` both
      drop — plus Jupyter line magics / shell escapes (``!pip …``,
      ``%timeit``). A cell that is only a magic therefore yields ``None``
      and stays a refusal.
    - ``//``-token languages (C, C++, C#, Java, TypeScript): skip ``//``
      line comments.

    ``/* … */`` block comments (including multi-line) are consumed for every
    language; a line can never validly *start* with ``/*`` in Python, so
    enabling it there is a harmless no-op while it correctly skips C-family
    and Rust block comments.

    Returns the first surviving line, or ``None`` when the cell is all
    blanks / comments / magics. Slugging (and the empty-slug rejection) is
    the caller's job.
    """
    if comment_token == "//":
        line_comment_prefixes: tuple[str, ...] = ("//",)
        skip_magics = False
    else:  # "#" family (Python, Rust)
        line_comment_prefixes = ("#", "//")
        skip_magics = True

    in_block = False
    for raw in source.splitlines():
        line = raw.strip()
        if not line:
            continue

        if in_block:
            close = line.find("*/")
            if close == -1:
                continue
            line = line[close + 2 :].strip()
            in_block = False
            if not line:
                continue

        # Consume any leading inline ``/* … */`` blocks; an unterminated one
        # opens a multi-line block consumed on subsequent iterations.
        while line.startswith("/*"):
            close = line.find("*/", 2)
            if close == -1:
                in_block = True
                line = ""
                break
            line = line[close + 2 :].strip()
        if in_block or not line:
            continue

        if any(line.startswith(prefix) for prefix in line_comment_prefixes):
            continue
        if skip_magics and line[:1] in _MAGIC_PREFIXES:
            continue
        return line

    return None


def _from_class_def(tree: ast.Module) -> Extraction | None:
    for stmt in tree.body:
        if isinstance(stmt, ast.ClassDef):
            return Extraction(Category.EXTRACTABLE, f"class {stmt.name}", "code:class")
    return None


def _from_function_def(tree: ast.Module) -> Extraction | None:
    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            return Extraction(Category.EXTRACTABLE, f"function {stmt.name}", "code:def")
    return None


def _from_assignment(tree: ast.Module) -> Extraction | None:
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                name = _assignment_target_name(target)
                if name:
                    return Extraction(Category.EXTRACTABLE, name, "code:assign")
        elif isinstance(stmt, ast.AnnAssign):
            name = _assignment_target_name(stmt.target)
            if name:
                return Extraction(Category.EXTRACTABLE, name, "code:assign")
    return None


def _assignment_target_name(node: ast.AST) -> str | None:
    """Return a readable name for an assignment target, or None.

    Handles ``x = ...`` and ``x, y = ...``. Attribute / subscript /
    starred targets fall through to None — they're rare in slide demo
    code and don't produce clean slugs.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Tuple | ast.List):
        names: list[str] = [n for n in (_assignment_target_name(elt) for elt in node.elts) if n]
        if names:
            return " ".join(names[:_MAX_IMPORT_NAMES])
    return None


def _from_import(tree: ast.Module) -> Extraction | None:
    names: list[str] = []
    for stmt in tree.body:
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                names.append(alias.asname or alias.name.split(".")[0])
        elif isinstance(stmt, ast.ImportFrom):
            for alias in stmt.names:
                names.append(alias.asname or alias.name)
    if not names:
        return None
    title = "import " + " ".join(names[:_MAX_IMPORT_NAMES])
    return Extraction(Category.EXTRACTABLE, title, "code:import")


def _from_call(tree: ast.Module) -> Extraction | None:
    for stmt in tree.body:
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            name = _call_name(stmt.value.func)
            if name:
                return Extraction(Category.EXTRACTABLE, name, "code:call")
    return None


def _from_for_loop(tree: ast.Module) -> Extraction | None:
    """``for student in classroom: …`` -> ``for student in classroom`` (#233)."""
    for stmt in tree.body:
        if isinstance(stmt, ast.For | ast.AsyncFor):
            target = _assignment_target_name(stmt.target)
            iterable = _value_name(stmt.iter)
            if target and iterable:
                return Extraction(Category.EXTRACTABLE, f"for {target} in {iterable}", "code:for")
            if target:
                return Extraction(Category.EXTRACTABLE, f"for {target}", "code:for")
    return None


def _from_expr_value(tree: ast.Module) -> Extraction | None:
    """Bare display expressions: subscript / attribute / name (#233).

    ``data[:5]``                          -> ``data``
    ``result["choices"]``                 -> ``result choices``
    ``response.headers["Content-Type"]``  -> ``response headers Content-Type``

    Runs after :func:`_from_call`, so a call expression never lands here.
    Expressions with no salient head name (arithmetic, comparisons,
    literals) return ``None`` and keep the hard-refusal behavior.
    """
    for stmt in tree.body:
        if isinstance(stmt, ast.Expr):
            name = _value_name(stmt.value)
            if name:
                return Extraction(Category.EXTRACTABLE, name, "code:expr")
    return None


def _value_name(node: ast.AST) -> str | None:
    """Render a value expression's salient name as readable text.

    Subscript keys that are string constants are appended
    (``post["title"]`` -> ``post title``); slices, numeric indexes, and
    computed keys contribute nothing beyond the base name
    (``data[:5]`` -> ``data``, ``items[0]`` -> ``items``).
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _value_name(node.value)
        if prefix:
            return f"{prefix} {node.attr}"
        return node.attr
    if isinstance(node, ast.Subscript):
        prefix = _value_name(node.value)
        if prefix is None:
            return None
        key = node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            return f"{prefix} {key.value}"
        return prefix
    if isinstance(node, ast.Call):
        return _call_name(node.func)
    return None


def _call_name(node: ast.AST) -> str | None:
    """Render the callee of a Call expression as readable text.

    ``foo()``               -> ``"foo"``
    ``obj.method()``        -> ``"obj method"``
    ``a.b.c()``             -> ``"a b c"``
    ``factory()()``         -> ``"factory"``
    ``items[0].method()``   -> ``"items method"``
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        if prefix:
            return f"{prefix} {node.attr}"
        return node.attr
    if isinstance(node, ast.Call):
        return _call_name(node.func)
    if isinstance(node, ast.Subscript):
        return _call_name(node.value)
    return None
