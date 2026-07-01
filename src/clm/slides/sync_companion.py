"""Companion-aware projection for ``clm slides sync`` (issue #501).

``clm slides sync`` reconciles the two deck halves of a split pair but is
structurally blind to **separated voiceover companion files**
(``voiceover_*.de.py`` / ``voiceover_*.en.py``): editing one companion is never
propagated to the other language and no ``sync`` subcommand reports it. This
module bridges that gap with an *in-memory projection*: a separated pair's
voiceover is inlined into each half's deck text (a pure text→text transform,
:func:`clm.slides.voiceover_tools.inline_pair_text`) so the existing plan engine
sees — and translates — the narration like any other cell, and read modes surface
the cross-language drift without touching disk.

The projection is designed so the plan is built over the *same* representation the
apply write-back will produce (design ``sync-separated-voiceover-companions.md``):

* **plain** — neither half has a companion → the projection is the identity, the
  deck text is byte-untouched, and the engine behaves exactly as before.
* **separated** — ≥1 half has a companion and no half keeps voiceover *both*
  inline and in a companion → inline each companion in memory and reconcile. This
  is the feature.
* **mixed** — a half keeps ``voiceover`` inline **and** in a companion (a genuine
  partial split, out of scope per the maintainer decision) → **refuse** with a
  normalize hint.
* **cross-language** — one half is separated while the other carries inline
  ``voiceover`` → **refuse** (the two languages disagree on representation).

An *unplaceable* companion cell (its ``for_slide`` no longer resolves in the deck
— a renamed or removed slide) is a total-transform failure: the pair refuses and
writes nothing rather than silently dropping the narration.

The classification predicate is deliberately **voiceover-only**: a separated deck
legitimately keeps ``notes`` inline while its voiceover lives in the companion
(the post-#387 default), so inline *notes* never make a pair *mixed*.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from clm.notebooks.slide_parser import comment_token_for_path
from clm.slides.voiceover_tools import (
    has_voiceover_cells_text,
    inline_pair_text,
    resolve_companion,
)


class Representation(Enum):
    """How a resolved DE/EN deck pair stores its voiceover (issue #501)."""

    PLAIN = "plain"
    SEPARATED = "separated"
    MIXED = "mixed"
    CROSS_LANGUAGE = "cross-language"


@dataclass(frozen=True)
class _HalfState:
    """One half's voiceover representation, read from the working tree."""

    companion: Path | None
    has_inline_voiceover: bool


@dataclass
class ProjectedPair:
    """The companion-aware projection of a resolved ``(de, en)`` deck pair.

    ``de_text`` / ``en_text`` are the deck texts the plan engine should read: the
    companion-inlined projection for a **separated** pair, and the raw working-tree
    text (byte-untouched) for every other representation. ``refusal`` is a blocking
    reason (mixed / cross-language / unplaceable-companion-cell) or ``None``.
    """

    representation: Representation
    de_text: str
    en_text: str
    de_companion: Path | None
    en_companion: Path | None
    refusal: str | None = None

    @property
    def is_separated(self) -> bool:
        """True for the projected (companion-inlined) case with no refusal."""
        return self.representation is Representation.SEPARATED and self.refusal is None

    @property
    def touches_companion(self) -> bool:
        """True whenever ≥1 companion is involved (separated / mixed / cross-language).

        The 2-file deck engine must never *write* such a pair through its plain
        flush: a separated pair needs the ≤4-file companion write-back (Phase 2),
        and a mixed / cross-language pair is refused outright. Plain pairs are
        ``False`` and keep the untouched fast path.
        """
        return self.representation is not Representation.PLAIN


def _half_state(deck_path: Path, deck_text: str) -> _HalfState:
    return _HalfState(
        companion=resolve_companion(deck_path),
        # Voiceover-only (never notes): inline notes beside a voiceover companion is
        # the sanctioned steady state, not a partial split (see module docstring).
        has_inline_voiceover=has_voiceover_cells_text(deck_text, comment_token_for_path(deck_path)),
    )


def _classify(de: _HalfState, en: _HalfState) -> Representation:
    de_comp = de.companion is not None
    en_comp = en.companion is not None
    # A half with BOTH a companion and inline voiceover is a partial split.
    if (de_comp and de.has_inline_voiceover) or (en_comp and en.has_inline_voiceover):
        return Representation.MIXED
    if not de_comp and not en_comp:
        return Representation.PLAIN
    # ≥1 companion, and no half is mixed. If the *other* half carries inline
    # voiceover, the two languages disagree on how they store narration.
    if (de_comp and en.has_inline_voiceover) or (en_comp and de.has_inline_voiceover):
        return Representation.CROSS_LANGUAGE
    return Representation.SEPARATED


_MIXED_REFUSAL = (
    "voiceover is stored both inline in the deck and in a separated companion file "
    "(a partial split); `clm slides sync` needs one representation. Run "
    "`clm voiceover inline <deck>` to fold all voiceover into the deck, or "
    "`clm voiceover extract <deck>` to move it all into the companion, then re-run sync."
)

_CROSS_LANGUAGE_REFUSAL = (
    "the two languages store voiceover differently — one half keeps it in a "
    "separated companion while the other carries it inline. Normalize both halves "
    "to the same representation (`clm voiceover extract` or `clm voiceover inline`) "
    "before syncing."
)


def _inline_half(deck_path: Path, deck_text: str, companion: Path | None) -> tuple[str, list[str]]:
    """Return ``(inlined_text, unmatched_for_slide_ids)`` for one half.

    A half with no companion projects to itself with no unmatched cells. The
    companion cells are parsed fresh inside :func:`inline_pair_text`, so this never
    mutates a caller's cell objects — the projection is safe in a non-mutating read
    mode (design §5.7).
    """
    if companion is None:
        return deck_text, []
    companion_text = companion.read_text(encoding="utf-8")
    result = inline_pair_text(deck_text, companion_text, comment_token_for_path(deck_path))
    unmatched = [c.metadata.for_slide or "<no for_slide>" for c in result.unmatched]
    return result.inlined_text, unmatched


def project_pair(de_path: Path, en_path: Path, de_text: str, en_text: str) -> ProjectedPair:
    """Classify the pair's voiceover representation and project it in memory.

    ``de_text`` / ``en_text`` are the raw working-tree texts (the caller already
    read them). Returns a :class:`ProjectedPair`: for a **separated** pair the deck
    texts are companion-inlined and ready for the plan engine; for **plain** they
    are returned untouched; for **mixed** / **cross-language** they are returned
    untouched with a blocking ``refusal``. A separated pair whose companion holds a
    cell that no longer resolves to a slide also carries a ``refusal`` (total
    transform — never drop narration).
    """
    de = _half_state(de_path, de_text)
    en = _half_state(en_path, en_text)
    representation = _classify(de, en)

    if representation is Representation.PLAIN:
        return ProjectedPair(representation, de_text, en_text, None, None)

    if representation is Representation.MIXED:
        return ProjectedPair(
            representation, de_text, en_text, de.companion, en.companion, refusal=_MIXED_REFUSAL
        )

    if representation is Representation.CROSS_LANGUAGE:
        return ProjectedPair(
            representation,
            de_text,
            en_text,
            de.companion,
            en.companion,
            refusal=_CROSS_LANGUAGE_REFUSAL,
        )

    # SEPARATED: inline each half that has a companion.
    de_inlined, de_unmatched = _inline_half(de_path, de_text, de.companion)
    en_inlined, en_unmatched = _inline_half(en_path, en_text, en.companion)
    refusal: str | None = None
    unmatched = de_unmatched + en_unmatched
    if unmatched:
        refusal = (
            "a voiceover companion carries narration whose owning slide no longer "
            "exists in the deck (unresolved for_slide: "
            f"{', '.join(sorted(set(unmatched)))}); syncing would drop it. Fix the "
            "slide_id / for_slide, or remove the orphaned narration, then re-run."
        )
    return ProjectedPair(
        Representation.SEPARATED,
        de_inlined,
        en_inlined,
        de.companion,
        en.companion,
        refusal=refusal,
    )
