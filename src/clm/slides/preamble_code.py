"""Detect (and re-home) "preamble code" folded into a j2 header cell.

**The problem (issue #253).** jupytext's ``py:percent`` reader folds any code
that has no preceding ``# %%`` marker into the *preceding* cell. When such code
sits between the ``# {{ header(...) }}`` macro call and the first real ``# %%``
cell, it becomes the **body** of the j2 header cell::

    # j2 from 'macros.j2' import header
    # {{ header("Regeln für Typen", "Rules for Types") }}
    from typing import Iterable          # <- folded into the header cell's body
    # %% [markdown] lang="de" ...

Because ``# {{ `` and ``# j2 `` are themselves cell boundaries, this code is
**never** part of the :func:`clm.slides.raw_cells.split_cells` preamble string —
it lives in ``RawCell.lines[1:]`` of the trailing j2 cell.

At build time the header macro expands to the title slide(s), and the trailing
code is folded into the *title markdown*. In the **bilingual** ``header(de, en)``
form the macro's last cell is the EN title, so the code attaches to EN and is
silently dropped from a DE build. In the **split** form ``header_de`` emits only
a DE cell, so the same code attaches to DE and survives. The two builds therefore
diverge on the DE side — the conversion is not render-neutral.

**The fix.** Move the code into its own ``# %%`` code cell. A code cell carries
no ``lang``, so it is included verbatim in every build and ``split`` copies it
identically to both halves — bilingual and split builds become byte-identical
(and the code is finally executed as code rather than rendered as markdown text).

This module is the single source of truth used by the validator (warning),
``clm slides split`` (warning), and ``clm slides normalize`` (auto-fix).
"""

from __future__ import annotations

from dataclasses import dataclass

from clm.notebooks.slide_parser import parse_cell_header
from clm.slides.raw_cells import RawCell


@dataclass(frozen=True)
class PreambleCodeFinding:
    """One j2 cell whose body carries executable preamble code."""

    cell_index: int
    """Index of the offending j2 cell in the ``cells`` list."""
    header_line: int
    """1-based line number of that j2 cell's header (the macro/import line)."""
    first_code_line: int
    """1-based absolute line number of the first offending code line."""
    code_lines: list[str]
    """The non-blank, non-comment body lines, verbatim and in order."""


def _is_code_line(line: str, comment_token: str) -> bool:
    """True iff ``line`` is executable code (non-blank and not a comment)."""
    return bool(line.strip()) and not line.lstrip().startswith(comment_token)


def _code_body_indices(body: list[str], comment_token: str) -> list[int]:
    return [i for i, line in enumerate(body) if _is_code_line(line, comment_token)]


def find_preamble_code(cells: list[RawCell], comment_token: str = "#") -> list[PreambleCodeFinding]:
    """Return findings for code folded into a *leading* j2 cell body.

    Only the run of j2 cells at the top of the file (the ``import`` directive
    and the ``# {{ header(...) }}`` macro call) is inspected; iteration stops at
    the first non-j2 cell. A body line counts as code iff it is non-blank and
    does not start with ``comment_token`` (``"#"`` for python/rust, ``"//"`` for
    cpp/csharp/java/typescript). Blank and comment lines are ignored, so a
    normal markdown body — whose ``#``-prefixed lines look like comments — never
    matches (and markdown cells are not j2 anyway).
    """
    findings: list[PreambleCodeFinding] = []
    for idx, cell in enumerate(cells):
        if not cell.metadata.is_j2:
            break
        body = cell.lines[1:]
        code_idx = _code_body_indices(body, comment_token)
        if code_idx:
            findings.append(
                PreambleCodeFinding(
                    cell_index=idx,
                    header_line=cell.line_number,
                    first_code_line=cell.line_number + 1 + code_idx[0],
                    code_lines=[body[i] for i in code_idx],
                )
            )
    return findings


def wrap_preamble_code(cells: list[RawCell], comment_token: str = "#") -> list[PreambleCodeFinding]:
    """Re-home preamble code into ``# %%`` code cells, mutating ``cells`` in place.

    For each leading j2 cell whose body carries code, the body from the first
    code line onward (preserving any interleaved comments) is moved into a new
    shared ``# %%`` code cell inserted directly after that j2 cell. Lines that
    preceded the code — comments and blank lines alike — stay on the j2 cell
    verbatim, so no authored content is lost. Only the *trailing* blank lines of
    the moved run are dropped (they are pure separators, re-added by the
    ``cell_spacing`` normalizer pass that runs afterwards).

    The new cell carries no ``lang`` (it is a shared cell), so the bilingual
    ``split`` copies it verbatim to both halves and the round trip stays
    byte-identical.

    Returns the findings (one per rewritten j2 cell), with line numbers relative
    to the pre-mutation file. Idempotent: a conforming deck is a no-op.
    """
    marker = f"{comment_token} %%"
    wrapped: list[PreambleCodeFinding] = []
    i = 0
    while i < len(cells) and cells[i].metadata.is_j2:
        cell = cells[i]
        body = cell.lines[1:]
        code_idx = _code_body_indices(body, comment_token)
        if code_idx:
            first = code_idx[0]
            head = body[:first]  # kept verbatim on the j2 cell — no content loss
            moved = body[first:]
            while moved and not moved[-1].strip():
                moved.pop()
            wrapped.append(
                PreambleCodeFinding(
                    cell_index=i,
                    header_line=cell.line_number,
                    first_code_line=cell.line_number + 1 + first,
                    code_lines=[line for line in moved if _is_code_line(line, comment_token)],
                )
            )
            cell.lines = [cell.lines[0], *head]
            cells.insert(
                i + 1,
                RawCell(
                    lines=[marker, *moved],
                    line_number=cell.line_number + 1 + first,
                    metadata=parse_cell_header(marker, comment_token),
                ),
            )
            i += 1  # skip the cell we just inserted (it is non-j2)
        i += 1
    return wrapped
