"""The shared read-verb core: bundle → ledger baseline → diff → payload.

One place produces the sync report so its consumers cannot drift: the CLI
verbs (``clm slides sync report`` / the pre-``apply`` diff) and the MCP
``sync_report`` tool all read the same member table through these helpers
(#520 Phase 4 — at cutover the MCP tool moved off the deleted v2 planner
onto this module). Read-only: nothing here writes a file or the ledger.
"""

from __future__ import annotations

from clm.slides import doc_apply, doc_ledger
from clm.slides.doc_lenses import LoadedBundle
from clm.slides.sync_diff import DeckDiff, diff_outcome

__all__ = ["diff_bundle", "diff_bundle_at_ref", "item_payloads", "pair_payload"]


def diff_bundle(bundle: LoadedBundle) -> DeckDiff:
    """Diff ``bundle`` against its committed per-topic ledger baseline.

    A deck with no ledger entry diffs against ``None`` — every member is
    cold (``verify_cold``), never silently trusted (design §5).
    """
    ledger = doc_ledger.load(doc_ledger.ledger_path_for(bundle.de_path))
    deck_ledger = ledger.decks.get(doc_ledger.deck_key_for(bundle.de_path))
    base = doc_ledger.baseline_from_ledger(deck_ledger) if deck_ledger is not None else None
    return diff_outcome(bundle.outcome, base)


def diff_bundle_at_ref(bundle: LoadedBundle, ref: str) -> tuple[DeckDiff, list[str]]:
    """The ``--since REF`` forensic view: diff against the bundle at a git ref.

    Reads the ≤4-file bundle at ``ref``, parses it into a complete
    :class:`~clm.slides.sync_diff.DeckBaseline`, and diffs the working tree
    against it — "show me what changed in this git window". A *view*, never a
    trust source (design §12.3): the ledger is not consulted and not written.

    Returns ``(diff, base_refusal_codes)``. When the bundle at ``ref`` is
    absent or refuses to parse, the diff falls back to a ``None`` base (every
    member cold) and the refusal codes say why.
    """
    from clm.slides.doc_identity import baseline_from_deck
    from clm.slides.doc_lenses import parse_bundle
    from clm.slides.git_text import bundle_texts_at_ref

    base = None
    base_refusal: list[str] = []
    base_de, base_en, base_de_c, base_en_c = bundle_texts_at_ref(
        bundle.de_path, bundle.en_path, ref
    )
    if base_de is not None and base_en is not None:
        base_outcome = parse_bundle(
            base_de, base_en, base_de_c, base_en_c, comment_token=bundle.comment_token
        )
        if base_outcome.refusal is not None:
            base_refusal = [r.code for r in base_outcome.refusal.reasons]
        elif base_outcome.deck is not None:
            base = baseline_from_deck(base_outcome.deck)
    return diff_outcome(bundle.outcome, base), base_refusal


def item_payloads(diff: DeckDiff) -> list[dict]:
    """The §6.4 item rows, each framed item carrying its answer vocabulary."""
    items = []
    for item in diff.items:
        payload = item.payload()
        answers = doc_apply.decision_vocabulary(item.action)
        if answers:
            payload["answers"] = list(answers)
        items.append(payload)
    return items


def pair_payload(bundle: LoadedBundle, diff: DeckDiff) -> dict:
    """The full schema-3 report payload for one pair."""
    payload = diff.to_payload()
    payload["items"] = item_payloads(diff)
    payload["de_path"] = str(bundle.de_path)
    payload["en_path"] = str(bundle.en_path)
    return payload
