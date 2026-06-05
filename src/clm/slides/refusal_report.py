"""Build an authoring worklist from ``assign-ids`` refusals (gap #5).

``clm slides assign-ids`` already reports each refused ``file:line`` and,
for soft refusals, a proposed slug. But to actually *author* a good
``slide_id`` by hand — which is the only way to clear a **hard** refusal —
you need each cell's body and the surrounding slide context (the nearest
preceding ``slide_id`` and heading so you know *where* in the deck you
are). Course conversions extracted that with throwaway scripts; this
module makes it a first-class report.

The worklist is a post-processing layer over :class:`AssignResult`: the
engine stays unchanged and emits cheap ``Refusal`` records (``file:line``
plus reason/proposal). Only when context is requested do we re-read the
affected files — a small subset — and pull each refused cell's marker,
body, and preceding anchors back out. This mirrors how
:mod:`clm.slides.validation_summary` layers on top of the validator.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from clm.slides.assign_ids import Refusal
from clm.slides.headingless import extract_heading
from clm.slides.raw_cells import RawCell, split_cells
from clm.slides.slug import strip_preserve_marker

__all__ = [
    "RefusalContext",
    "RefusalEntry",
    "RefusalWorklist",
    "build_refusal_worklist",
    "render_worklist",
    "worklist_to_dict",
]


@dataclass
class RefusalContext:
    """Authoring context for one refused cell, recovered from its file."""

    marker: str  # the cell header line, verbatim
    cell_type: str  # "markdown" / "code" / "j2"
    lang: str | None
    body: str  # the cell body, verbatim (trailing blank lines stripped)
    preceding_slide_id: str | None  # nearest preceding bare slide_id
    preceding_heading: str | None  # nearest preceding markdown heading text


@dataclass
class RefusalEntry:
    """One refusal, optionally enriched with :class:`RefusalContext`."""

    file: str
    line: int
    severity: str  # "soft" / "hard"
    reason: str
    proposed_slug: str | None = None
    proposed_title: str | None = None
    context: RefusalContext | None = None


@dataclass
class RefusalWorklist:
    """The hand-authoring worklist: every refusal, hard ones first."""

    entries: list[RefusalEntry] = field(default_factory=list)

    @property
    def hard(self) -> list[RefusalEntry]:
        return [e for e in self.entries if e.severity == "hard"]

    @property
    def soft(self) -> list[RefusalEntry]:
        return [e for e in self.entries if e.severity == "soft"]


def _strip_trailing_blank(body: str) -> str:
    lines = body.split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _preceding_anchors(cells: list[RawCell], idx: int) -> tuple[str | None, str | None]:
    """Nearest preceding ``slide_id`` and heading for the cell at ``idx``.

    The two anchors are found independently — the closest preceding cell
    that carries a ``slide_id`` and the closest preceding cell whose body
    has a markdown heading need not be the same cell. We stop as soon as
    both are known.
    """
    found_id: str | None = None
    found_heading: str | None = None
    for j in range(idx - 1, -1, -1):
        cell = cells[j]
        if found_id is None and cell.metadata.slide_id:
            found_id = strip_preserve_marker(cell.metadata.slide_id)
        if found_heading is None:
            heading = extract_heading(cell.body)
            if heading:
                found_heading = heading
        if found_id is not None and found_heading is not None:
            break
    return found_id, found_heading


def _context_for_file(refusals: list[Refusal], text: str) -> dict[int, RefusalContext]:
    """Map each refused line in one file to its recovered context."""
    _, cells = split_cells(text)
    by_line: dict[int, int] = {cell.line_number: i for i, cell in enumerate(cells)}
    out: dict[int, RefusalContext] = {}
    for refusal in refusals:
        idx = by_line.get(refusal.line)
        if idx is None:
            continue
        cell = cells[idx]
        slide_id, heading = _preceding_anchors(cells, idx)
        out[refusal.line] = RefusalContext(
            marker=cell.header,
            cell_type=cell.metadata.cell_type,
            lang=cell.metadata.lang,
            body=_strip_trailing_blank(cell.body),
            preceding_slide_id=slide_id,
            preceding_heading=heading,
        )
    return out


def build_refusal_worklist(
    refusals: list[Refusal], *, with_context: bool = False
) -> RefusalWorklist:
    """Turn engine ``Refusal`` records into an authoring worklist.

    With ``with_context`` the affected files are re-read once each and every
    refused cell is enriched with its marker, body, and preceding
    ``slide_id``/heading anchors. Without it the worklist carries only the
    cheap ``file:line``/reason/proposal data already in ``refusals`` (no
    file reads). Hard refusals sort before soft ones; within a severity the
    original ``(file, line)`` order is preserved.
    """
    context_by_file: dict[str, dict[int, RefusalContext]] = {}
    if with_context:
        grouped: dict[str, list[Refusal]] = defaultdict(list)
        for refusal in refusals:
            grouped[refusal.file].append(refusal)
        for file_str, file_refusals in grouped.items():
            try:
                text = Path(file_str).read_text(encoding="utf-8")
            except OSError:
                continue
            context_by_file[file_str] = _context_for_file(file_refusals, text)

    entries = [
        RefusalEntry(
            file=refusal.file,
            line=refusal.line,
            severity=refusal.severity,
            reason=refusal.reason,
            proposed_slug=refusal.proposed_slug,
            proposed_title=refusal.proposed_title,
            context=context_by_file.get(refusal.file, {}).get(refusal.line),
        )
        for refusal in refusals
    ]
    # Hard first, then soft; stable within each severity.
    entries.sort(key=lambda e: 0 if e.severity == "hard" else 1)
    return RefusalWorklist(entries=entries)


def _render_entry(entry: RefusalEntry) -> list[str]:
    out = [f"─── {entry.file}:{entry.line}  [{entry.severity}]"]
    out.append(f"    reason: {entry.reason}")
    if entry.proposed_slug or entry.proposed_title:
        bits = []
        if entry.proposed_title:
            bits.append(f'title="{entry.proposed_title}"')
        if entry.proposed_slug:
            bits.append(f'proposed slide_id="{entry.proposed_slug}"')
        out.append("    " + ", ".join(bits))
    ctx = entry.context
    if ctx is not None:
        anchor_bits = []
        if ctx.preceding_slide_id:
            anchor_bits.append(f'after slide_id="{ctx.preceding_slide_id}"')
        if ctx.preceding_heading:
            anchor_bits.append(f'heading "{ctx.preceding_heading}"')
        if anchor_bits:
            out.append("    " + " / ".join(anchor_bits))
        else:
            out.append("    (no preceding slide_id/heading — start of deck)")
        out.append(f"    | {ctx.marker}")
        for line in ctx.body.split("\n"):
            out.append(f"    | {line}")
    return out


def render_worklist(worklist: RefusalWorklist) -> str:
    """Human-readable worklist; hard refusals first, then soft."""
    if not worklist.entries:
        return "No refusals — every slide either has or was assigned an id."
    lines: list[str] = []
    for entry in worklist.entries:
        lines.extend(_render_entry(entry))
        lines.append("")
    lines.append(
        f"{len(worklist.hard)} hard refusal(s) (need a hand-authored slide_id), "
        f"{len(worklist.soft)} soft refusal(s)."
    )
    return "\n".join(lines)


def worklist_to_dict(worklist: RefusalWorklist) -> dict:
    """JSON-serializable worklist."""

    def ctx_dict(ctx: RefusalContext | None) -> dict | None:
        if ctx is None:
            return None
        return {
            "marker": ctx.marker,
            "cell_type": ctx.cell_type,
            "lang": ctx.lang,
            "body": ctx.body,
            "preceding_slide_id": ctx.preceding_slide_id,
            "preceding_heading": ctx.preceding_heading,
        }

    return {
        "hard_refusals": len(worklist.hard),
        "soft_refusals": len(worklist.soft),
        "refusals": [
            {
                "file": e.file,
                "line": e.line,
                "severity": e.severity,
                "reason": e.reason,
                "proposed_slug": e.proposed_slug,
                "proposed_title": e.proposed_title,
                "context": ctx_dict(e.context),
            }
            for e in worklist.entries
        ],
    }
