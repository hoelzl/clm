"""``clm slides coverage report`` / ``accept`` — the agent-toolkit revision (#963).

Frames the voiceover-coverage judgment as a read-only report and validates
an agent's answer — the emit-don't-invoke revision of what ``clm slides
coverage`` used to do in-process through a local Ollama judge (that path
now lives behind ``coverage autopilot``). The contract is the shared
kit's (``clm info agent-tasks``): read by default, freshness tokens
echoed and re-checked, a validator label naming the judge, load-bearing
exit codes, and **no model and no Ollama client anywhere on the
report/accept path**.

The framing reuses the engine's own pair construction
(:func:`clm.slides.coverage.build_coverage_pairs`), bullet extraction,
and the judge's system prompt verbatim
(:data:`clm.infrastructure.llm.prompts.COVERAGE_SYSTEM_PROMPT` — the
model-free prompt seam), so an agent-driven verdict and an ``autopilot``
verdict are judged by the exact same instructions. The accept path
validates shape + freshness + coverage, then banks each verdict into the
existing :class:`~clm.infrastructure.llm.cache.CoverageCache` rows —
same ``(slide_hash, voiceover_hash, prompt_version, lang)`` key the
embedded judge wrote, byte-compatible ``gap_details``. The cache **is**
the trust store here (issue #963); no ledger row is involved.

Freshness is per-pair content hashes, not file bytes: the answer echoes
each judged pair's ``(slide_hash, voiceover_hash, lang)`` and accept
re-derives the live pairs — a deck edit that changes a pair changes its
hash, so the answer no longer covers exactly the pending set and is
refused wholesale (never merged).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from clm.core.slide_text.slide_parser import parse_cells
from clm.core.utils.prog_lang_utils import comment_token_for_path
from clm.infrastructure.llm.prompts import (
    COVERAGE_PROMPT_VERSION,
    COVERAGE_SYSTEM_PROMPT,
    BulletVerdict,
    CoverageVerdict,
)
from clm.slides.agent_task import VALIDATORS, AnswerRejected, envelope
from clm.slides.coverage import (
    CoveragePair,
    build_coverage_pairs,
    content_hash,
    extract_bullets,
    narrative_text,
)

if TYPE_CHECKING:
    from clm.infrastructure.llm.cache import CoverageCache

__all__ = [
    "ANSWER_SCHEMA",
    "ANSWER_VALIDATOR",
    "TASK_SCHEMA",
    "CoverageAnswer",
    "build_report",
    "parse_answer",
    "pending_pairs",
    "prepare_accept",
]

#: The wire schema of the report envelope and the answer document.
TASK_SCHEMA = 1

#: The validator label the report announces; registered in the shared
#: kit's registry (:data:`clm.slides.agent_task.VALIDATORS`) at import of
#: this module — :func:`parse_answer` is the function this label names.
ANSWER_VALIDATOR = "coverage-verdicts"

_VERDICTS = ("covered", "gaps")

_INSTRUCTIONS = f"""\
# Judging voiceover coverage

For every item in `pending`, decide whether its `voiceover` covers each of
the slide's `bullets` — semantic coverage is enough; do not require a
word-for-word match. The judge's instructions (the same system prompt the
embedded model used) apply verbatim:

```
{COVERAGE_SYSTEM_PROMPT}```

Answer with one `pairs` row per pending item, keyed by its
`slide_hash` / `voiceover_hash` / `lang` echo: one `bullets` list (the
item's bullet `text` verbatim, in order, each with `covered` and a short
`reason`) and a `verdict` consistent with them (`covered` only when every
bullet is covered). Echo `prompt_version` verbatim. `accept` re-derives
the live pairs and re-checks: an answer that does not cover EXACTLY the
pending items — stale hashes, missing rows, extra rows, or rewritten
bullet texts — is refused wholesale, never merged. Items already carrying
a cached verdict are reported as `cached` and are not answerable; items
with status `duplicate` repeat a framed pair's content (the cache is
keyed by content) — answer nothing for them.
"""

#: The answer contract: per-pair verdicts plus the echoed prompt version.
#: Validated by :func:`parse_answer` (shape) and :func:`prepare_accept`
#: (freshness + coverage) — the engine banks the verdict rows, it never
#: judges their quality.
ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["schema", "prompt_version", "pairs"],
    "properties": {
        "schema": {"const": TASK_SCHEMA},
        "prompt_version": {
            "type": "string",
            "description": "echoed verbatim from the report document",
        },
        "pairs": {
            "type": "array",
            "description": "one row per pending item: the verdict for that "
            "(slide, voiceover, lang) pair",
            "items": {
                "type": "object",
                "required": [
                    "slide_hash",
                    "voiceover_hash",
                    "lang",
                    "verdict",
                    "bullets",
                ],
                "additionalProperties": False,
                "properties": {
                    "slide_hash": {"type": "string"},
                    "voiceover_hash": {"type": "string"},
                    "lang": {"enum": ["de", "en"]},
                    "verdict": {"enum": list(_VERDICTS)},
                    "bullets": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": ["text", "covered"],
                            "additionalProperties": False,
                            "properties": {
                                "text": {"type": "string"},
                                "covered": {"type": "boolean"},
                                "reason": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
    },
}


# ---------------------------------------------------------------------------
# The report side (read-only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JudgedPair:
    """One pair resolved for framing: the texts, the cache keys, the state.

    ``voiceover`` is ``None`` for a no-voiceover pair — those surface as
    findings immediately (no judgment needed) exactly like the engine's
    own classification.
    """

    pair: CoveragePair
    bullets: list[str]
    voiceover: str | None
    slide_hash: str
    voiceover_hash: str | None
    file: str


def _iter_deck_files(path: Path) -> list[Path]:
    """A single deck file as-is; a directory expanded to every slide file."""
    if path.is_dir():
        from clm.core.topic_resolver import find_slide_files_recursive

        return list(find_slide_files_recursive(path))
    return [path]


def _pairs_for_text(path: Path) -> list[JudgedPair]:
    text = path.read_text(encoding="utf-8")
    cells = parse_cells(text, comment_token_for_path(path))
    out: list[JudgedPair] = []
    for pair in build_coverage_pairs(cells):
        bullets = extract_bullets(pair.slide_cell.content)
        if not bullets:
            continue  # nothing to cover — the engine skips these silently
        voiceover = narrative_text(pair.narrative_cells)
        out.append(
            JudgedPair(
                pair=pair,
                bullets=bullets,
                voiceover=voiceover if voiceover.strip() else None,
                slide_hash=content_hash(pair.slide_cell.content),
                voiceover_hash=content_hash(voiceover) if voiceover.strip() else None,
                file=str(path),
            )
        )
    return out


def _pairs_for_path(path: Path) -> list[JudgedPair]:
    pairs: list[JudgedPair] = []
    for file in _iter_deck_files(path):
        pairs.extend(_pairs_for_text(file))
    return pairs


def pending_pairs(path: Path, *, cache: CoverageCache | None = None) -> list[JudgedPair]:
    """The pairs awaiting judgment: bullets present, voiceover present, no
    cached verdict — the answerable set of a report."""
    out: list[JudgedPair] = []
    for jp in _pairs_for_path(path):
        if jp.voiceover is None or jp.voiceover_hash is None:
            continue
        if cache is not None and (
            cache.get(jp.slide_hash, jp.voiceover_hash, COVERAGE_PROMPT_VERSION, jp.pair.lang)
            is not None
        ):
            continue
        out.append(jp)
    return out


def build_report(path: Path, *, cache: CoverageCache | None = None) -> dict[str, Any]:
    """Frame the coverage judgment as one report document (read-only).

    One item per (slide, lang) pair with bullets; pairs without voiceover
    surface as findings (the engine's own classification — no judgment
    needed); pairs with a cached verdict are reported as verdicts, not
    re-framed.
    """
    items: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    cached = 0
    framed_pending: set[tuple[str, str, str]] = set()
    for jp in _pairs_for_path(path):
        base = {
            "file": jp.file,
            "line": jp.pair.slide_cell.line_number,
            "slide_id": jp.pair.slide_id,
            "lang": jp.pair.lang,
            "bullets": jp.bullets,
        }
        if jp.voiceover is None:
            findings.append(
                {
                    "severity": "warning",
                    "file": jp.file,
                    "line": jp.pair.slide_cell.line_number,
                    "slide_id": jp.pair.slide_id,
                    "lang": jp.pair.lang,
                    "message": (
                        f"slide {jp.pair.slide_id!r} ({jp.pair.lang}) has "
                        f"{len(jp.bullets)} bullet(s) but no voiceover"
                    ),
                    "uncovered_bullets": jp.bullets,
                }
            )
            items.append({**base, "status": "no-voiceover"})
            continue
        verdict_row: dict[str, Any] | None = None
        if cache is not None and jp.voiceover_hash is not None:
            row = cache.get(jp.slide_hash, jp.voiceover_hash, COVERAGE_PROMPT_VERSION, jp.pair.lang)
            if row is not None:
                cached += 1
                verdict_str, gap_details = row
                verdict_row = {
                    "verdict": verdict_str,
                    "details": _parse_gap_details(gap_details),
                }
        if verdict_row is not None:
            if verdict_row["verdict"] != "covered":
                # A banked gap surfaces as a finding — the same read the
                # autopilot's cache-only mode performs.
                details = verdict_row["details"] or {}
                uncovered = [
                    b.get("text", "") for b in details.get("bullets", []) if not b.get("covered")
                ]
                findings.append(
                    {
                        "severity": "warning",
                        "file": jp.file,
                        "line": jp.pair.slide_cell.line_number,
                        "slide_id": jp.pair.slide_id,
                        "lang": jp.pair.lang,
                        "message": (
                            f"slide {jp.pair.slide_id!r} ({jp.pair.lang}): voiceover "
                            f"does not cover {len(uncovered) or '≥1'} bullet(s) "
                            "(cached verdict)"
                        ),
                        "uncovered_bullets": uncovered or jp.bullets,
                    }
                )
            items.append(
                {
                    **base,
                    "status": "cached",
                    "slide_hash": jp.slide_hash,
                    "voiceover_hash": jp.voiceover_hash,
                    "verdict": verdict_row,
                }
            )
        else:
            assert jp.voiceover_hash is not None  # guarded above
            key = (jp.slide_hash, jp.voiceover_hash, jp.pair.lang)
            if key in framed_pending:
                # Two pairs share identical slide + voiceover content (a
                # recap slide repeated across decks). The cache is keyed by
                # content: one verdict covers both — answer the first
                # occurrence; a duplicate answer row would be unanswerable
                # (the validator rejects duplicate keys).
                items.append({**base, "status": "duplicate", "slide_hash": jp.slide_hash})
                continue
            framed_pending.add(key)
            items.append(
                {
                    **base,
                    "status": "pending",
                    "slide_hash": jp.slide_hash,
                    "voiceover_hash": jp.voiceover_hash,
                    "voiceover": jp.voiceover,
                }
            )
    pending = sum(1 for i in items if i["status"] == "pending")
    return envelope(
        TASK_SCHEMA,
        tool="coverage",
        verb="report",
        body={
            "source": str(path),
            "prompt_version": COVERAGE_PROMPT_VERSION,
            "instructions": _INSTRUCTIONS,
            "items": items,
            "counts": {
                "pairs_total": len(items),
                "pending": pending,
                "cached": cached,
                "duplicates": sum(1 for i in items if i["status"] == "duplicate"),
                "no_voiceover": sum(1 for i in items if i["status"] == "no-voiceover"),
                "findings": len(findings),
            },
            "findings": findings,
            "answer_schema": ANSWER_SCHEMA,
            "validator": ANSWER_VALIDATOR,
        },
    )


def _parse_gap_details(gap_details: str | None) -> dict[str, Any] | None:
    if not gap_details:
        return None
    try:
        import json

        payload = json.loads(gap_details)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


# ---------------------------------------------------------------------------
# The answer side
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerdictRow:
    """One parsed answer row (shape-validated; freshness pending)."""

    slide_hash: str
    voiceover_hash: str
    lang: str
    verdict: CoverageVerdict


@dataclass(frozen=True)
class CoverageAnswer:
    """A parsed answer document (shape-validated; freshness pending)."""

    prompt_version: str
    rows: list[VerdictRow]


_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _parse_verdict_row(payload: Any, where: str) -> VerdictRow:
    if not isinstance(payload, dict) or set(payload) != {
        "slide_hash",
        "voiceover_hash",
        "lang",
        "verdict",
        "bullets",
    }:
        raise AnswerRejected(
            f"{where} must be an object with exactly slide_hash, voiceover_hash, "
            "lang, verdict, bullets"
        )
    slide_hash = payload["slide_hash"]
    voiceover_hash = payload["voiceover_hash"]
    if not isinstance(slide_hash, str) or not _HEX64.match(slide_hash):
        raise AnswerRejected(f"{where}.slide_hash must be 64 hex chars, echoed verbatim")
    if not isinstance(voiceover_hash, str) or not _HEX64.match(voiceover_hash):
        raise AnswerRejected(f"{where}.voiceover_hash must be 64 hex chars, echoed verbatim")
    lang = payload["lang"]
    if lang not in ("de", "en"):
        raise AnswerRejected(f"{where}.lang must be 'de' or 'en', got {lang!r}")
    verdict_str = payload["verdict"]
    if verdict_str not in _VERDICTS:
        raise AnswerRejected(f"{where}.verdict must be 'covered' or 'gaps'")
    raw_bullets = payload["bullets"]
    if not isinstance(raw_bullets, list) or not raw_bullets:
        raise AnswerRejected(f"{where}.bullets must be a non-empty list")
    bullets: list[BulletVerdict] = []
    for i, entry in enumerate(raw_bullets):
        if not isinstance(entry, dict) or set(entry) - {"text", "covered", "reason"}:
            raise AnswerRejected(f"{where}.bullets[{i}] must be an object with text/covered/reason")
        text, covered, reason = entry.get("text"), entry.get("covered"), entry.get("reason", "")
        if not isinstance(text, str) or not text.strip():
            raise AnswerRejected(f"{where}.bullets[{i}].text must be a non-empty string")
        if not isinstance(covered, bool):
            raise AnswerRejected(f"{where}.bullets[{i}].covered must be a boolean")
        if not isinstance(reason, str):
            raise AnswerRejected(f"{where}.bullets[{i}].reason must be a string")
        bullets.append(BulletVerdict(text=text, covered=covered, reason=reason))
    derived = "covered" if all(b.covered for b in bullets) else "gaps"
    if verdict_str != derived:
        raise AnswerRejected(
            f"{where}: verdict {verdict_str!r} contradicts its bullets "
            f"(all-covered would be {derived!r})"
        )
    return VerdictRow(
        slide_hash=slide_hash,
        voiceover_hash=voiceover_hash,
        lang=lang,
        verdict=CoverageVerdict(verdict=verdict_str, bullets=tuple(bullets)),
    )


def parse_answer(payload: Any) -> CoverageAnswer:
    """The ``coverage-verdicts`` validator: shape-check the answer document.

    Registered in the shared kit's registry; the accept verb resolves this
    function through :data:`clm.slides.agent_task.VALIDATORS`. Freshness
    and coverage are judged against the live pairs in :func:`prepare_accept`.
    """
    if not isinstance(payload, dict):
        raise AnswerRejected("the answer must be a JSON object (see the report's answer_schema)")
    if payload.get("schema") != TASK_SCHEMA:
        raise AnswerRejected(f"answer schema must be {TASK_SCHEMA}, got {payload.get('schema')!r}")
    prompt_version = payload.get("prompt_version")
    if not isinstance(prompt_version, str) or not prompt_version:
        raise AnswerRejected("prompt_version must be echoed verbatim from the report")
    rows_payload = payload.get("pairs")
    if not isinstance(rows_payload, list) or not rows_payload:
        raise AnswerRejected("'pairs' must be a non-empty list of verdict rows")
    rows = [_parse_verdict_row(row, f"pairs[{i}]") for i, row in enumerate(rows_payload)]
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = (row.slide_hash, row.voiceover_hash, row.lang)
        if key in seen:
            raise AnswerRejected(f"pairs: duplicate answer for slide_hash {row.slide_hash[:12]}…")
        seen.add(key)
    return CoverageAnswer(prompt_version=prompt_version, rows=rows)


VALIDATORS.register(ANSWER_VALIDATOR, parse_answer)


@dataclass(frozen=True)
class AcceptPlan:
    """The validated bank: cache keys -> verdicts, in answer order."""

    rows: list[tuple[str, str, str, CoverageVerdict]]  # (slide_hash, voiceover_hash, lang, v)


def prepare_accept(
    path: Path, answer: CoverageAnswer, *, cache: CoverageCache | None = None
) -> AcceptPlan:
    """Validate prompt version + freshness + coverage against the live pairs.

    Raises :class:`AnswerRejected` on the first violation; nothing is
    written (the caller owns the cache transaction).
    """
    if answer.prompt_version != COVERAGE_PROMPT_VERSION:
        raise AnswerRejected(
            f"prompt_version mismatch: the answer was framed for "
            f"{answer.prompt_version!r}, but the engine judges with "
            f"{COVERAGE_PROMPT_VERSION!r} — re-run `clm slides coverage report`"
        )
    live = {
        (jp.slide_hash, jp.voiceover_hash, jp.pair.lang): jp
        for jp in pending_pairs(path, cache=cache)
    }
    answered = {(r.slide_hash, r.voiceover_hash, r.lang): r for r in answer.rows}
    missing = sorted(set(live) - set(answered))
    extra = sorted(set(answered) - set(live))
    if missing or extra:
        parts = []
        if missing:
            parts.append(
                f"missing answers for {len(missing)} pending pair(s) "
                f"(e.g. slide_hash {missing[0][0][:12]}…)"
            )
        if extra:
            parts.append(
                f"answers for {len(extra)} pair(s) that are not pending now "
                "(stale hashes, an already-cached pair, or a deck edit) "
                f"(e.g. slide_hash {extra[0][0][:12]}…)"
            )
        raise AnswerRejected(
            "the answer must cover exactly the pending pairs — "
            + "; ".join(parts)
            + ". Re-run `clm slides coverage report` and answer the fresh document."
        )
    for key, row in answered.items():
        jp = live[key]
        answered_texts = [b.text for b in row.verdict.bullets]
        if answered_texts != jp.bullets:
            raise AnswerRejected(
                f"slide_hash {key[0][:12]}…: the answer judges different bullet "
                "texts than the live slide carries (echo each bullet verbatim, "
                "in order) — re-run `clm slides coverage report`"
            )
    return AcceptPlan(
        rows=[(r.slide_hash, r.voiceover_hash, r.lang, r.verdict) for r in answer.rows]
    )
