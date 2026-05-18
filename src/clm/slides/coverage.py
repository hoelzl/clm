"""LLM-driven voiceover-coverage check.

Phase 4 of the slide-format-redesign feature. For each (slide, voiceover)
pair in a deck, ask a local LLM whether every bullet on the slide is
covered by the voiceover. Verdicts are cached in
:class:`clm.infrastructure.llm.cache.CoverageCache` keyed by
``(slide_hash, voiceover_hash, prompt_version, lang)`` so re-runs are
free when neither the slide nor its voiceover has changed. See §2.5 of
``handover-slide-format-redesign-clm.md`` for the design rationale.

The orchestration here is deliberately offline-tolerant: a missing or
unreachable judge causes pairs to be reported as "skipped" rather than
raising. The CLI surfaces uncovered bullets at ``warning`` severity per
the Phase 3-style rollout (promote to ``error`` once the false-positive
rate against real ML AZAV decks is known).
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from clm.notebooks.slide_parser import Cell, parse_cells
from clm.slides.pairing import TITLE_SLIDE_ID, is_title_macro_cell
from clm.slides.slug import strip_preserve_marker

if TYPE_CHECKING:
    from clm.infrastructure.llm.cache import CoverageCache
    from clm.infrastructure.llm.ollama_client import (
        CoverageJudge,
        CoverageVerdict,
    )

logger = logging.getLogger(__name__)

__all__ = [
    "CoverageFinding",
    "CoveragePair",
    "CoverageResult",
    "check_coverage_for_text",
    "check_coverage_in_directory",
    "check_coverage_in_file",
    "extract_bullets",
]


# ---------------------------------------------------------------------------
# Public result types
# ---------------------------------------------------------------------------


@dataclass
class CoverageFinding:
    """One uncovered (slide, voiceover) pair surfaced to the author."""

    severity: str  # "warning" / "error" / "info"
    file: str
    line: int
    slide_id: str
    lang: str
    message: str
    suggestion: str = ""
    uncovered_bullets: tuple[str, ...] = ()


@dataclass
class CoveragePair:
    """One (slide, voiceover, lang) triple to be judged.

    ``narrative_cells`` are the voiceover/notes cells of this language
    that immediately follow ``slide_cell`` in source order, up to the
    next slide/subslide cell of the same language.
    """

    slide_cell: Cell
    narrative_cells: list[Cell]
    lang: str
    slide_id: str


@dataclass
class CoverageResult:
    """Outcome of one coverage run over one or more files."""

    files_visited: int = 0
    pairs_total: int = 0
    pairs_checked: int = 0
    cache_hits: int = 0
    llm_calls: int = 0
    pairs_skipped: int = 0
    findings: list[CoverageFinding] = field(default_factory=list)

    @property
    def has_findings(self) -> bool:
        return bool(self.findings)


# ---------------------------------------------------------------------------
# Pair construction
# ---------------------------------------------------------------------------


@dataclass
class _OpenPair:
    slide_cell: Cell
    narrative_cells: list[Cell] = field(default_factory=list)
    lang: str = ""
    slide_id: str = ""

    def finalize(self) -> CoveragePair:
        return CoveragePair(
            slide_cell=self.slide_cell,
            narrative_cells=list(self.narrative_cells),
            lang=self.lang,
            slide_id=self.slide_id,
        )


def build_coverage_pairs(cells: Sequence[Cell]) -> list[CoveragePair]:
    """Walk ``cells`` and emit one (slide, voiceover) pair per language.

    Each slide/subslide cell starts a new pair for its language; the
    pair stays open and collects narrative cells (voiceover, notes) of
    that language until the next slide cell of the *same* language
    arrives. Narrative cells without a ``lang`` attribute attach to
    every open pair (rare; treated as language-neutral commentary).

    The j2 ``header()`` macro line anchors the title slide for any
    narrative cells that follow it before the first explicit slide
    cell. We synthesise a virtual slide cell for the title-macro so
    those narrative cells get checked too.
    """
    pairs: list[CoveragePair] = []
    open_pairs: dict[str, _OpenPair] = {}
    title_anchor_line: int | None = None  # line of last header() macro, while still active

    def close(lang: str) -> None:
        slot = open_pairs.pop(lang, None)
        if slot is not None:
            pairs.append(slot.finalize())

    for cell in cells:
        meta = cell.metadata

        if is_title_macro_cell(cell):
            for lang in list(open_pairs):
                close(lang)
            title_anchor_line = cell.line_number
            continue

        if meta.is_j2:
            continue

        if meta.is_slide_start:
            slide_lang = meta.lang
            if slide_lang is None:
                for open_lang in list(open_pairs):
                    close(open_lang)
                title_anchor_line = None
                continue
            close(slide_lang)
            open_pairs[slide_lang] = _OpenPair(
                slide_cell=cell,
                lang=slide_lang,
                slide_id=strip_preserve_marker(meta.slide_id) if meta.slide_id else "",
            )
            title_anchor_line = None
            continue

        if meta.is_narrative:
            narr_lang = meta.lang
            if narr_lang is None:
                for opened in open_pairs.values():
                    opened.narrative_cells.append(cell)
                continue
            if narr_lang not in open_pairs and title_anchor_line is not None:
                # First narrative cell for the title slide in this language.
                open_pairs[narr_lang] = _OpenPair(
                    slide_cell=_synthetic_title_cell(
                        line_number=title_anchor_line,
                        lang=narr_lang,
                    ),
                    lang=narr_lang,
                    slide_id=TITLE_SLIDE_ID,
                )
            if narr_lang in open_pairs:
                open_pairs[narr_lang].narrative_cells.append(cell)
            continue

    for trailing_lang in list(open_pairs):
        close(trailing_lang)

    return pairs


def _synthetic_title_cell(*, line_number: int, lang: str) -> Cell:
    """Stand-in for the title slide's missing markdown cell.

    The j2 ``header()`` macro has no markdown body to extract bullets
    from, so a title-anchored pair has zero bullets. We still create a
    pair so any narrative cells attached to the title slide are counted
    in ``pairs_total`` (and skipped cleanly because there is nothing to
    cover).
    """
    from clm.notebooks.slide_parser import CellMetadata

    metadata = CellMetadata(
        cell_type="markdown",
        lang=lang,
        tags=["slide"],
        slide_id=TITLE_SLIDE_ID,
        raw_header="",
    )
    return Cell(
        line_number=line_number,
        header="",
        content="",
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Bullet / narrative extraction
# ---------------------------------------------------------------------------


_BULLET_RE = re.compile(r"^#\s+[-*]\s+(?P<text>.+?)\s*$")
_NUMBERED_RE = re.compile(r"^#\s+\d+\.\s+(?P<text>.+?)\s*$")
_EMPHASIS_RE = re.compile(r"\*+([^*]+)\*+")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")


def extract_bullets(content: str) -> list[str]:
    """Pull out the top-level bullet text from a slide markdown cell.

    Strips the ``# `` Python comment prefix, the bullet marker, and
    inline markdown emphasis/code/link formatting so the LLM sees the
    semantic content rather than rendering noise.
    """
    bullets: list[str] = []
    for line in content.splitlines():
        m = _BULLET_RE.match(line) or _NUMBERED_RE.match(line)
        if not m:
            continue
        text = m.group("text").strip()
        text = _LINK_RE.sub(r"\1", text)
        text = _EMPHASIS_RE.sub(r"\1", text)
        text = _INLINE_CODE_RE.sub(r"\1", text)
        text = text.strip()
        if text:
            bullets.append(text)
    return bullets


def _narrative_text(cells: Sequence[Cell], *, max_chars: int = 6000) -> str:
    """Concatenate the plain text of ``cells``, capped at ``max_chars``."""
    parts: list[str] = []
    used = 0
    for cell in cells:
        for raw_line in cell.content.splitlines():
            if not raw_line.strip():
                continue
            if raw_line.startswith("# "):
                line = raw_line[2:]
            elif raw_line.startswith("#"):
                line = raw_line[1:]
            else:
                line = raw_line
            if not line.strip():
                continue
            parts.append(line)
            used += len(line) + 1
            if used >= max_chars:
                parts.append("[truncated]")
                return "\n".join(parts)
    return "\n".join(parts)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Core algorithm
# ---------------------------------------------------------------------------


@dataclass
class CoverageOptions:
    """Knobs for one coverage pass."""

    judge: CoverageJudge | None = None
    cache: CoverageCache | None = None
    report_only: bool = False  # if True, don't write cache; reads still happen
    severity: str = "warning"


def check_coverage_for_text(
    text: str,
    file_path: Path,
    options: CoverageOptions,
) -> CoverageResult:
    """Run the coverage check on one file's text.

    Returns a :class:`CoverageResult`. The text is never mutated.
    """
    result = CoverageResult(files_visited=1)
    cells = parse_cells(text)
    pairs = build_coverage_pairs(cells)
    file_str = str(file_path)

    prompt_version = _prompt_version(options.judge)

    for pair in pairs:
        result.pairs_total += 1
        bullets = extract_bullets(pair.slide_cell.content)
        if not bullets:
            # Nothing to cover. Skip silently — this is the common shape
            # for code-only slides, image slides, and the title slide.
            continue

        voiceover_text = _narrative_text(pair.narrative_cells)
        if not voiceover_text.strip():
            # Bullets exist but no voiceover yet — surface immediately
            # without hitting the LLM. This is the most common gap
            # authors care about during drafting.
            result.findings.append(
                CoverageFinding(
                    severity=options.severity,
                    file=file_str,
                    line=pair.slide_cell.line_number,
                    slide_id=pair.slide_id,
                    lang=pair.lang,
                    message=(
                        f"slide {pair.slide_id!r} ({pair.lang}) has "
                        f"{len(bullets)} bullet(s) but no voiceover"
                    ),
                    suggestion=(
                        "Add a voiceover cell directly after this slide and cover each bullet."
                    ),
                    uncovered_bullets=tuple(bullets),
                )
            )
            result.pairs_checked += 1
            continue

        slide_hash = _content_hash(pair.slide_cell.content)
        voiceover_hash = _content_hash(voiceover_text)

        verdict = _lookup_or_judge(
            slide_hash=slide_hash,
            voiceover_hash=voiceover_hash,
            prompt_version=prompt_version,
            bullets=bullets,
            voiceover_text=voiceover_text,
            pair=pair,
            options=options,
            result=result,
        )
        if verdict is None:
            result.pairs_skipped += 1
            continue

        result.pairs_checked += 1
        if verdict.has_gaps:
            uncovered = tuple(b.text for b in verdict.uncovered_bullets if b.text)
            result.findings.append(
                CoverageFinding(
                    severity=options.severity,
                    file=file_str,
                    line=pair.slide_cell.line_number,
                    slide_id=pair.slide_id,
                    lang=pair.lang,
                    message=(
                        f"slide {pair.slide_id!r} ({pair.lang}): voiceover "
                        f"does not cover {len(uncovered)} bullet(s)"
                    ),
                    suggestion=(
                        "Extend the voiceover to address each missing bullet. "
                        "Re-run `clm slides coverage` after editing."
                    ),
                    uncovered_bullets=uncovered or tuple(bullets),
                )
            )

    return result


def _prompt_version(judge: CoverageJudge | None) -> str:
    if judge is None:
        return "v1"
    return getattr(judge, "prompt_version", "v1")


def _lookup_or_judge(
    *,
    slide_hash: str,
    voiceover_hash: str,
    prompt_version: str,
    bullets: list[str],
    voiceover_text: str,
    pair: CoveragePair,
    options: CoverageOptions,
    result: CoverageResult,
) -> CoverageVerdict | None:
    from clm.infrastructure.llm.ollama_client import CoverageVerdict, OllamaError

    cache = options.cache
    if cache is not None:
        cached = cache.get(slide_hash, voiceover_hash, prompt_version, pair.lang)
        if cached is not None:
            result.cache_hits += 1
            verdict_str, gap_details = cached
            if gap_details:
                try:
                    return CoverageVerdict.from_json(gap_details)
                except (ValueError, TypeError) as exc:
                    logger.warning(
                        "discarding corrupt cached verdict for slide %r (%s): %s",
                        pair.slide_id,
                        pair.lang,
                        exc,
                    )
            return CoverageVerdict(verdict=verdict_str)

    judge = options.judge
    if judge is None:
        return None

    try:
        verdict = judge.judge(bullets, voiceover_text, lang=pair.lang)
    except OllamaError as exc:
        logger.warning(
            "coverage judge failed for slide %r (%s): %s",
            pair.slide_id,
            pair.lang,
            exc,
        )
        return None

    result.llm_calls += 1
    if cache is not None and not options.report_only:
        cache.put(
            slide_hash,
            voiceover_hash,
            prompt_version,
            pair.lang,
            verdict.verdict,
            verdict.to_json(),
        )
    return verdict


def check_coverage_in_file(path: Path, options: CoverageOptions) -> CoverageResult:
    """Process one slide file end-to-end."""
    text = path.read_text(encoding="utf-8")
    return check_coverage_for_text(text, path, options)


def check_coverage_in_directory(path: Path, options: CoverageOptions) -> CoverageResult:
    """Recurse over a directory and process every slide file we find."""
    from clm.core.topic_resolver import find_slide_files_recursive

    combined = CoverageResult()
    for slide_file in find_slide_files_recursive(path):
        single = check_coverage_in_file(slide_file, options)
        combined.files_visited += single.files_visited
        combined.pairs_total += single.pairs_total
        combined.pairs_checked += single.pairs_checked
        combined.cache_hits += single.cache_hits
        combined.llm_calls += single.llm_calls
        combined.pairs_skipped += single.pairs_skipped
        combined.findings.extend(single.findings)
    return combined
