"""Shared structured-output schema for the per-slide LLM operations.

Both the future ``clm voiceover compare`` judge and the
``polish_and_port`` primitive (backing ``clm voiceover port-voiceover``
and ``clm voiceover backfill``) need to reason about bullet-level
provenance: for every bullet in an output or target, did it survive
untouched, get modified, appear from nowhere, or vanish from the source?

This module centralizes:

* the canonical :class:`BulletStatus` enum and its JSON spelling,
* :class:`BulletOutcome` — one row of structured bullet-level feedback,
* :class:`PerSlidePack` — the shared input packer that both the port
  and compare per-slide prompts consume, so the two prompt files diverge
  only in tone and instructions, never in field names, and
* ``parse_structured_response`` — the forgiving JSON reader used by
  both call sites.

Keeping this separate from ``clm/voiceover/merge.py`` preserves that
module's single-purpose narrative (merge transcript + baseline) while
giving port/compare their own symmetric home.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class BulletStatus(str, Enum):
    """Per-bullet outcome, shared by port and compare.

    * ``covered`` — the bullet is present in both source and target with
      essentially the same content (modulo style).
    * ``rewritten`` — the bullet is present in both but substantively
      edited (a correction, clarification, or merge).
    * ``added`` — the bullet is new in the output and has no counterpart
      in the prior side.
    * ``dropped`` — the bullet existed on the prior side and is
      intentionally absent from the output.
    * ``manual_review`` — the model could not confidently classify the
      bullet; callers should surface these for human judgment.
    """

    COVERED = "covered"
    REWRITTEN = "rewritten"
    ADDED = "added"
    DROPPED = "dropped"
    MANUAL_REVIEW = "manual_review"


@dataclass
class BulletOutcome:
    """One row of structured feedback about a single bullet.

    ``source`` and ``target`` are optional because ``added`` entries have
    no source bullet and ``dropped`` entries have no target bullet.
    """

    status: BulletStatus
    target: str | None = None
    source: str | None = None
    note: str | None = None

    def to_json(self) -> dict:
        payload: dict = {"status": self.status.value}
        if self.target is not None:
            payload["target"] = self.target
        if self.source is not None:
            payload["source"] = self.source
        if self.note is not None:
            payload["note"] = self.note
        return payload

    @classmethod
    def from_json(cls, data: dict) -> BulletOutcome:
        raw_status = data.get("status", "")
        try:
            status = BulletStatus(raw_status)
        except ValueError:
            logger.debug("Unknown bullet status %r, mapping to manual_review", raw_status)
            status = BulletStatus.MANUAL_REVIEW
        return cls(
            status=status,
            target=data.get("target"),
            source=data.get("source"),
            note=data.get("note"),
        )


@dataclass
class PerSlidePack:
    """Per-slide input packet for a port or compare LLM call.

    All three text fields use the same packing so both prompt families
    see identical structure. ``slide_content_prior`` is omitted when the
    slide wasn't modified between the two revisions.
    """

    slide_id: str
    language: str
    baseline_bullets: str
    prior_bullets: str
    slide_content_head: str
    slide_content_prior: str | None = None
    content_changed: bool = False
    notes: list[str] = field(default_factory=list)

    def estimate_chars(self) -> int:
        total = len(self.baseline_bullets) + len(self.prior_bullets) + len(self.slide_content_head)
        if self.slide_content_prior:
            total += len(self.slide_content_prior)
        return total

    def build_user_message(self) -> str:
        """Render the per-slide user message.

        The same layout is consumed by ``port_{lang}.md`` (constructive)
        and ``compare_{lang}.md`` (evaluative) — the prompts differ in
        what they ask the model to *do* with the fields, not in how
        those fields are named or ordered.
        """
        parts: list[str] = [f"SLIDE ID: {self.slide_id}"]

        if self.slide_content_head.strip():
            parts.append(
                "SLIDE CONTENT (current HEAD version; do not include in output):\n"
                f"{self.slide_content_head.strip()}"
            )
        if self.content_changed and self.slide_content_prior is not None:
            parts.append(
                "SLIDE CONTENT (prior/source version; for matching context only):\n"
                f"{self.slide_content_prior.strip()}"
            )
        elif self.content_changed:
            parts.append(
                "NOTE: The slide content changed between the source and target "
                "revisions, but the prior slide text is not available."
            )

        prior_label = "PRIOR BULLETS (from the video-derived voiceover on the source revision):"
        parts.append(f"{prior_label}\n{self.prior_bullets.strip() or '(empty)'}")

        baseline_label = "BASELINE BULLETS (already present at HEAD; preserve unless contradicted):"
        parts.append(
            f"{baseline_label}\n{self.baseline_bullets.strip() or '(empty -- no existing voiceover at HEAD)'}"
        )

        for note in self.notes:
            parts.append(f"NOTE: {note}")

        return "\n\n".join(parts)


def parse_structured_response(
    raw: str,
    *,
    default_bullets: str = "",
) -> tuple[str, list[BulletOutcome], str | None]:
    """Parse a port/compare structured response into its three pieces.

    Returns ``(bullets, outcomes, notes)`` where ``bullets`` is the
    merged-or-critiqued bullet text, ``outcomes`` is a list of
    :class:`BulletOutcome`, and ``notes`` is an optional free-text
    summary the model may include.

    The parser is forgiving: it strips markdown fences, accepts either a
    bare JSON object or one wrapped in ``{"result": {...}}``, and
    returns ``default_bullets`` with an empty outcome list when the
    response isn't parseable. Callers may log or reject on an empty
    outcome list.
    """
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if len(lines) >= 2 and lines[-1].strip().startswith("```"):
            text = "\n".join(lines[1:-1]).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("LLM response is not valid JSON: %s", exc)
        return default_bullets, [], None

    if isinstance(data, dict) and "result" in data and isinstance(data["result"], dict):
        data = data["result"]

    if not isinstance(data, dict):
        logger.warning("LLM response is not a JSON object (got %s)", type(data).__name__)
        return default_bullets, [], None

    bullets = str(data.get("bullets") or data.get("merged_bullets") or default_bullets)

    outcomes_raw = data.get("outcomes") or data.get("bullet_outcomes") or []
    outcomes: list[BulletOutcome] = []
    if isinstance(outcomes_raw, list):
        for row in outcomes_raw:
            if isinstance(row, dict):
                outcomes.append(BulletOutcome.from_json(row))

    notes_value = data.get("notes")
    notes = str(notes_value) if isinstance(notes_value, str) and notes_value.strip() else None

    return bullets, outcomes, notes
