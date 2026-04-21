"""Match slides between two slide-file revisions.

Used by ``port-voiceover`` (and the future ``compare``) to decide, for
each slide in the target file, which slide in the source file (if any)
it corresponds to. The matching strategy (per proposal §3.3):

1. **Primary key** — ``slide_id`` metadata attribute (stable across
   edits, written once by ``clm normalize-slides --operations slide_ids``).
2. **Fallback** — fuzzy title match on the slide heading.
3. **Tie-break** — content-fingerprint similarity when titles collide.

The module is deliberately side-effect-free: it returns a list of
:class:`MatchedSlide` records that the CLI then feeds to
:func:`clm.voiceover.port.polish_and_port` one by one.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum

from clm.notebooks.slide_parser import SlideGroup

logger = logging.getLogger(__name__)

# Minimum fuzzy similarity for two titles to count as a match.
TITLE_MATCH_THRESHOLD = 75.0

# Below this content similarity, a slide pair with matching title/id is
# still considered "modified" (content changed enough that porting needs
# the LLM to be aware).
CONTENT_UNCHANGED_THRESHOLD = 90.0


class MatchKind(str, Enum):
    """How a target slide was matched to a source slide.

    * ``unchanged`` — title/id match, content essentially identical.
    * ``modified`` — title/id match, content changed enough to warn the LLM.
    * ``new_at_head`` — target slide has no source counterpart.
    * ``removed_at_head`` — source slide has no target counterpart; the
      entry appears in :func:`match_slides` output so the CLI can report
      dropped content, but it has no target index.
    * ``manual_review`` — ambiguous match (e.g. duplicate titles that we
      couldn't disambiguate); the CLI should surface for human review.
    """

    UNCHANGED = "unchanged"
    MODIFIED = "modified"
    NEW_AT_HEAD = "new_at_head"
    REMOVED_AT_HEAD = "removed_at_head"
    MANUAL_REVIEW = "manual_review"


@dataclass
class MatchedSlide:
    """One slide-level match result.

    ``target_index`` and ``source_index`` refer to the position in the
    respective :func:`~clm.notebooks.slide_parser.group_slides` output
    (same scheme ``slide_writer`` uses for its notes map). Either may
    be ``None`` for new/removed slides.
    """

    kind: MatchKind
    target_index: int | None
    source_index: int | None
    target_group: SlideGroup | None
    source_group: SlideGroup | None
    key: str  # slide_id, title, or "<untitled>" fallback — for reports
    content_similarity: float = 0.0

    @property
    def content_changed(self) -> bool:
        return self.kind is MatchKind.MODIFIED


def match_slides(
    source_groups: list[SlideGroup],
    target_groups: list[SlideGroup],
) -> list[MatchedSlide]:
    """Match ``target_groups`` against ``source_groups``.

    Returns one :class:`MatchedSlide` per target slide, followed by one
    per source slide that had no target match (with
    ``kind=removed_at_head``). Header groups (``slide_type="header"``)
    are ignored — they never receive voiceover.

    Matching is deterministic and case-insensitive for fallback keys.
    Duplicate source slide_ids/titles are disambiguated by first-seen
    + content fingerprint; if still ambiguous, the match is demoted to
    ``manual_review``.
    """
    source_usable = [(i, g) for i, g in enumerate(source_groups) if g.slide_type != "header"]
    target_usable = [(i, g) for i, g in enumerate(target_groups) if g.slide_type != "header"]

    source_by_id = _build_index(source_usable, key_fn=_slide_id_of)
    source_by_title = _build_index(source_usable, key_fn=_title_key_of)

    consumed: set[int] = set()
    results: list[MatchedSlide] = []

    for t_idx, t_group in target_usable:
        match = _match_one(
            t_idx=t_idx,
            t_group=t_group,
            source_by_id=source_by_id,
            source_by_title=source_by_title,
            source_groups=source_groups,
            consumed=consumed,
        )
        results.append(match)

    # Collect unmatched source slides as removed_at_head entries.
    for s_idx, s_group in source_usable:
        if s_idx in consumed:
            continue
        s_id = _slide_id_of(s_group)
        s_title = _title_key_of(s_group)
        if s_id:
            key = f"id:{s_id}"
        elif s_title:
            key = f"title:{s_title}"
        else:
            key = "<untitled>"
        results.append(
            MatchedSlide(
                kind=MatchKind.REMOVED_AT_HEAD,
                target_index=None,
                source_index=s_idx,
                target_group=None,
                source_group=s_group,
                key=key,
            )
        )

    return results


def content_similarity(a: str, b: str) -> float:
    """Return a 0-100 similarity score between two slide content blobs.

    Uses ``fuzz.ratio`` (character-level Levenshtein) rather than
    ``token_set_ratio`` so that *adding* content registers as a
    difference — a slide that gained a paragraph of explanation is
    "modified", not "unchanged".
    """
    from rapidfuzz import fuzz

    a_norm = _normalize_content(a)
    b_norm = _normalize_content(b)
    if not a_norm and not b_norm:
        return 100.0
    if not a_norm or not b_norm:
        return 0.0
    return float(fuzz.ratio(a_norm, b_norm))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_index(
    items: list[tuple[int, SlideGroup]],
    *,
    key_fn,
) -> dict[str, list[tuple[int, SlideGroup]]]:
    index: dict[str, list[tuple[int, SlideGroup]]] = {}
    for idx, group in items:
        key = key_fn(group)
        if not key:
            continue
        index.setdefault(key, []).append((idx, group))
    return index


def _match_one(
    *,
    t_idx: int,
    t_group: SlideGroup,
    source_by_id: dict[str, list[tuple[int, SlideGroup]]],
    source_by_title: dict[str, list[tuple[int, SlideGroup]]],
    source_groups: list[SlideGroup],
    consumed: set[int],
) -> MatchedSlide:
    t_id = _slide_id_of(t_group)
    t_title = _title_key_of(t_group)

    # 1. Exact slide_id match (primary).
    if t_id and t_id in source_by_id:
        candidates = source_by_id[t_id]
        picked = _pick_candidate(t_group, candidates, consumed)
        if picked is not None:
            return _finalise_match(t_idx, t_group, picked, "id:" + t_id)
        if len(candidates) > 1:
            return MatchedSlide(
                kind=MatchKind.MANUAL_REVIEW,
                target_index=t_idx,
                source_index=None,
                target_group=t_group,
                source_group=None,
                key="id:" + t_id,
            )

    # 2. Fuzzy title match (fallback).
    title_match = _fuzzy_title_pick(t_group, source_by_title, consumed)
    if title_match is not None:
        return _finalise_match(t_idx, t_group, title_match, "title:" + (t_title or ""))

    # 3. No match.
    return MatchedSlide(
        kind=MatchKind.NEW_AT_HEAD,
        target_index=t_idx,
        source_index=None,
        target_group=t_group,
        source_group=None,
        key=("id:" + t_id) if t_id else ("title:" + t_title if t_title else "<untitled>"),
    )


def _finalise_match(
    t_idx: int,
    t_group: SlideGroup,
    picked: tuple[int, SlideGroup],
    key: str,
) -> MatchedSlide:
    s_idx, s_group = picked
    similarity = content_similarity(s_group.text_content, t_group.text_content)
    kind = MatchKind.UNCHANGED if similarity >= CONTENT_UNCHANGED_THRESHOLD else MatchKind.MODIFIED
    return MatchedSlide(
        kind=kind,
        target_index=t_idx,
        source_index=s_idx,
        target_group=t_group,
        source_group=s_group,
        key=key,
        content_similarity=similarity,
    )


def _pick_candidate(
    t_group: SlideGroup,
    candidates: list[tuple[int, SlideGroup]],
    consumed: set[int],
) -> tuple[int, SlideGroup] | None:
    available = [c for c in candidates if c[0] not in consumed]
    if not available:
        return None
    if len(available) == 1:
        consumed.add(available[0][0])
        return available[0]

    # Duplicate id/title: disambiguate by content similarity.
    t_text = t_group.text_content
    scored = [(content_similarity(g.text_content, t_text), idx, g) for idx, g in available]
    scored.sort(reverse=True, key=lambda x: x[0])
    top_score, top_idx, top_group = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if top_score - runner_up < 10.0:
        # Too close to call — let the caller flag manual_review.
        return None
    consumed.add(top_idx)
    return top_idx, top_group


def _fuzzy_title_pick(
    t_group: SlideGroup,
    source_by_title: dict[str, list[tuple[int, SlideGroup]]],
    consumed: set[int],
) -> tuple[int, SlideGroup] | None:
    t_title = _title_key_of(t_group)
    if not t_title:
        return None

    if t_title in source_by_title:
        picked = _pick_candidate(t_group, source_by_title[t_title], consumed)
        if picked is not None:
            return picked

    # Try fuzzy match across remaining titles. ``ratio`` penalises
    # added/missing words, unlike ``token_set_ratio`` which happily
    # matches titles that share only a single token.
    from rapidfuzz import fuzz

    best_score = 0.0
    best_candidate: tuple[int, SlideGroup] | None = None
    for title, entries in source_by_title.items():
        score = fuzz.ratio(t_title, title)
        if score < TITLE_MATCH_THRESHOLD or score <= best_score:
            continue
        for idx, group in entries:
            if idx in consumed:
                continue
            best_score = score
            best_candidate = (idx, group)
            break

    if best_candidate is not None:
        consumed.add(best_candidate[0])
    return best_candidate


def _slide_id_of(group: SlideGroup) -> str:
    cell = group.cells[0] if group.cells else None
    if cell is None:
        return ""
    return cell.metadata.slide_id or ""


def _title_key_of(group: SlideGroup) -> str:
    if not group.title:
        return ""
    return " ".join(group.title.lower().split())


def _normalize_content(text: str) -> str:
    # Strip markdown syntax noise so reformatting doesn't look like a content change.
    cleaned = re.sub(r"[*_`#>]+", " ", text)
    return " ".join(cleaned.lower().split())
