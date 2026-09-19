"""``clm harvest task --kind port`` — frame revision porting for the agent (#960).

The agent-first replacement for the embedded-LLM ``harvest port``: the
deterministic slide pairing (:func:`~clm.voiceover.slide_matcher.match_slides`)
decides *which* old-revision slide maps to which HEAD slide; the framed task
hands the agent both bullet sets plus the slide contents; the answer is the
standard bullet-list document (validator ``harvest-bullets``), written through
the existing ``harvest accept`` path — id-keyed, companion-aware, atomic.

A port answer carries no ``video_fingerprint``, so ``--record`` is refused
(the ledger provenance ``harvest:<video-fingerprint>`` has no meaning for a
file-to-file port; a port-specific provenance is future work). Freshness is
the usual per-member ``baseline_fingerprints`` of the **target** deck.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from clm.slides.doc_identity import content_fingerprint
from clm.voiceover.harvest_task import ANSWER_SCHEMA, TaskUnavailable, slide_content
from clm.voiceover.slide_matcher import MatchKind, match_slides

__all__ = ["build_port_tasks"]

_PORTABLE_KINDS = (MatchKind.UNCHANGED, MatchKind.MODIFIED)


def _port_instructions(lang: str) -> str:
    prompt = Path(__file__).parent / "prompts" / f"port_{lang}.md"
    if not prompt.exists():
        prompt = Path(__file__).parent / "prompts" / "port_en.md"
    return prompt.read_text(encoding="utf-8")


def _narrative_cells_and_tokens(
    deck, slide_id: str
) -> tuple[list[dict[str, Any]], dict[str, dict[str, str | None]]]:
    """The target slide's narrative members (both sides) + freshness tokens."""
    from clm.voiceover.harvest_accept import narrative_members

    members = narrative_members(deck, slide_id) or []
    merged: dict[str, dict[str, Any]] = {}
    tokens: dict[str, dict[str, str | None]] = {}
    for member in members:
        key = member.key.render()
        entry = merged.setdefault(
            key,
            {
                "member": key,
                "role": member.role,
                "layout": member.layout,
                "de": None,
                "en": None,
            },
        )
        tokens.setdefault(key, {"de": None, "en": None})
        for side in ("de", "en"):
            cell = member.side(side)
            if cell is not None:
                entry[side] = cell.body
                tokens[key][side] = content_fingerprint(cell)
    return list(merged.values()), tokens


def _frame_port(match, deck, *, lang: str) -> dict[str, Any]:
    target = match.target_group
    source = match.source_group
    assert target is not None and source is not None
    slide_id = target.cells[0].slide_id if target.cells else None
    if not slide_id:
        raise TaskUnavailable(
            f"target slide {match.key} has no slide_id — run "
            "`clm slides normalize --stamp-ids` before porting into it"
        )
    cells, tokens = _narrative_cells_and_tokens(deck, slide_id)
    inputs: dict[str, Any] = {
        "language": lang,
        "baseline": cells,
        "prior_bullets": source.notes_text,
        "slide": {"title": target.title, "content": slide_content(deck, slide_id, lang)},
        "content_changed": match.content_changed,
        "content_similarity": match.content_similarity,
    }
    if match.content_changed:
        inputs["prior_slide"] = {"title": source.title, "content": source.text_content}
    return {
        "item": f"id:{slide_id}",
        "kind": "port",
        "class": match.kind.value,
        "validator": "harvest-bullets",
        "language": lang,
        "baseline_fingerprints": tokens,
        "instructions": _port_instructions(lang),
        "inputs": inputs,
        "answer_schema": ANSWER_SCHEMA,
    }


def build_port_tasks(
    deck,
    target_groups,
    source_groups,
    *,
    lang: str,
    slide: str | None = None,
) -> list[dict[str, Any]]:
    """Frame port tasks for the matched slide pairs (read-only).

    One task per ``unchanged``/``modified`` pair whose source slide carries
    voiceover. ``manual_review`` pairs are skipped in the sweep (the matcher
    discarded the candidates) and raise :class:`TaskUnavailable` when named
    via ``slide``; ``new_at_head``/``removed_at_head`` carry nothing to port.
    ``slide`` accepts the bare slide id or the ``id:...`` handle.
    """
    matches = match_slides(source_groups, target_groups)
    portable = [
        m
        for m in matches
        if m.kind in _PORTABLE_KINDS
        and m.source_group is not None
        and m.source_group.notes_text.strip()
    ]
    if slide is not None:
        handle = slide if slide.startswith(("id:", "pos:")) else f"id:{slide}"
        named = [m for m in matches if m.key == handle]
        if not named:
            raise TaskUnavailable(f"no slide {handle} in the match result")
        match = named[0]
        if match.kind is MatchKind.MANUAL_REVIEW:
            raise TaskUnavailable(
                f"slide {handle} matched ambiguously (duplicate source candidates "
                "too close to call) — the matcher retains no candidates to frame; "
                "fix the duplicate slide ids/titles and re-run"
            )
        if match.kind not in _PORTABLE_KINDS or match.source_group is None:
            raise TaskUnavailable(f"slide {handle} is '{match.kind.value}' — nothing to port")
        if not match.source_group.notes_text.strip():
            raise TaskUnavailable(f"the source slide for {handle} carries no voiceover")
        return [_frame_port(match, deck, lang=lang)]
    tasks = []
    for match in portable:
        try:
            tasks.append(_frame_port(match, deck, lang=lang))
        except TaskUnavailable:
            continue
    return tasks
