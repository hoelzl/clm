"""AST-based slug extraction for code-typed slide cells.

Used by ``clm slides assign-ids`` as a Phase-2 fallback when
:func:`clm.slides.headingless.classify` returns ``NON_EXTRACTABLE`` on a
cell whose ``cell_type == "code"``. Walks the top-level statements with
:mod:`ast` and picks the most slug-worthy node by precedence:

    1. class definition       -> ``class <Name>``
    2. function / async def   -> ``function <name>``
    3. top-level assignment   -> ``<target>``
    4. import / from-import   -> ``import <name> [<name>...]``
    5. expression-stmt call   -> ``<obj> <method>`` (or ``<func>``)

Returns ``None`` when the source can't be parsed (shell escapes, magic
commands, half-finished stubs) or no slug-worthy node is found. The
caller treats ``None`` as ``NON_EXTRACTABLE`` and falls through to the
existing refusal / LLM-fallback path.

Extractor labels (``Extraction.source``) match the precedence order:
``code:class``, ``code:def``, ``code:assign``, ``code:import``,
``code:call``. They surface in the ``assign-ids`` report as
``content:code:<kind>`` so authors can tell which strategy produced a
given slug.
"""

from __future__ import annotations

import ast

from clm.slides.headingless import Category, Extraction

# Cap the number of import names baked into the composite title so a
# 30-line `from foo import (a, b, c, ...)` block doesn't produce an
# absurdly long proposed title. ``slug.py`` enforces a character cap
# afterwards too; this is purely for readability of the report.
_MAX_IMPORT_NAMES = 4


def extract_from_code(source: str) -> Extraction | None:
    """Return an ``EXTRACTABLE`` proposal derived from code, or ``None``.

    ``source`` should be the raw Python body of a percent-format code
    cell (no ``# `` prefix). A :class:`SyntaxError` produces ``None`` so
    one unparsable cell does not abort the larger ``assign-ids`` run.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    for extractor in (
        _from_class_def,
        _from_function_def,
        _from_assignment,
        _from_import,
        _from_call,
    ):
        result = extractor(tree)
        if result is not None:
            return result
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
