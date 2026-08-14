"""The shared read-verb core: bundle → ledger baseline → diff → payload.

One place produces the sync report so its consumers cannot drift: the CLI
verbs (``clm slides sync report`` / the pre-``apply`` diff) and the MCP
``sync_report`` tool all read the same member table through these helpers
(#520 Phase 4 — at cutover the MCP tool moved off the deleted v2 planner
onto this module). Read-only: nothing here writes a file or the ledger.
"""

from __future__ import annotations

import hashlib

from clm.slides import doc_apply, doc_ledger
from clm.slides.base_recovery import BASE_DIFF_ACTIONS, MemberBaseDiff, batch_observation
from clm.slides.doc_lenses import LoadedBundle, project
from clm.slides.sync_diff import DeckDiff, diff_outcome

__all__ = [
    "cold_sweep_hint",
    "diff_bundle",
    "diff_bundle_at_ref",
    "diff_bundle_with_ledger",
    "item_payloads",
    "pair_payload",
    "report_id_for",
]


def diff_bundle(bundle: LoadedBundle) -> DeckDiff:
    """Diff ``bundle`` against its committed per-topic ledger baseline.

    A deck with no ledger entry diffs against ``None`` — every member is
    cold (``verify_cold``), never silently trusted (design §5).
    """
    return diff_bundle_with_ledger(bundle)[0]


def diff_bundle_with_ledger(bundle: LoadedBundle) -> tuple[DeckDiff, doc_ledger.TopicLedger]:
    """:func:`diff_bundle`, handing back the ledger it loaded.

    The report needs the same topic ledger twice — once for the baseline and
    once for the schema-4 ``report_id`` — and a directory sweep pays that per
    deck. Threading it through instead of loading it twice is most of the
    difference between a 37% and an 11% slowdown on a 24-deck module report.
    """
    ledger = doc_ledger.load(doc_ledger.ledger_path_for(bundle.de_path))
    deck_ledger = ledger.decks.get(doc_ledger.deck_key_for(bundle.de_path))
    base = doc_ledger.baseline_from_ledger(deck_ledger) if deck_ledger is not None else None
    return diff_outcome(bundle.outcome, base), ledger


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


def item_payloads(
    diff: DeckDiff, *, base_diffs: dict[str, MemberBaseDiff] | None = None
) -> list[dict]:
    """The §6.4 item rows, each carrying its vocabulary and how it resolves.

    ``answers`` is present on **every** item so consumers can filter with
    ``item["answers"]`` without guarding a missing key — agent drivers
    provably crashed on the key's absence before this was guaranteed. But an
    empty list alone is ambiguous, and that ambiguity is finding M6: on a
    mechanical row it means "nothing to answer, apply executes it", on a
    framed row it means "blocked — repair the files yourself". Schema 4 adds
    ``resolution`` (``mechanical`` / ``decision`` / ``manual``) so the two
    cases are distinguishable without a hardcoded action list.

    ``base_diffs`` is #773's recovery result (schema 5, additive): a
    recovered ``verify_translation`` / ``translate_edit`` row additionally
    carries ``base_ref`` and per-side ``de_diff`` / ``en_diff``, so the
    reader judges the hunks instead of re-diffing two full cells by eye. An
    unrecovered row ships exactly as before — absence is the honest degrade.
    """
    items = []
    for item in diff.items:
        payload = item.payload()
        payload["answers"] = list(doc_apply.item_answers(item))
        payload["resolution"] = doc_apply.item_resolution(item)
        recovered = base_diffs.get(item.key) if base_diffs else None
        # The action guard matters: keys are shared across a member's aspect
        # rows (e.g. a conflict_tags beside a verify_translation), and the
        # recovery is a claim about the recovered actions only.
        if recovered is not None and item.action in BASE_DIFF_ACTIONS:
            payload["base_ref"] = recovered.base_ref
            if recovered.de_diff is not None:
                payload["de_diff"] = recovered.de_diff
            if recovered.en_diff is not None:
                payload["en_diff"] = recovered.en_diff
        items.append(payload)
    return items


def cold_sweep_hint(diff: DeckDiff) -> str | None:
    """A next-step hint when the whole report is cold (never-recorded deck).

    An all-``verify_cold`` report is the seeding case, and the efficient
    answer is the ``record`` verb, not a hand-built confirm-all decision
    document — agents reliably scripted the latter when nothing said so.

    The wording keeps "review the pair" load-bearing: ``record`` asserts
    trust. Since D8 the verb's own gate reads the separated voiceover
    companions too, so a divergence hidden in the narration refuses the
    record rather than being swept up by it — but the gate is structural, and
    only the reader can judge whether the two halves *say the same thing*.

    "Whole report" means every **question**. Since #764 a cold deck also emits
    mechanical ``record_neutral`` rows for the members the engine could settle
    by observation, and those change nothing about the advice — the remaining
    items are still an all-cold seeding case, and ``record`` is still the
    efficient answer. Keying the test on *all* items would have silently
    withdrawn the hint from exactly the freshly-authored decks it exists for.
    """
    questions = [item for item in diff.items if item.action != "record_neutral"]
    if questions and all(item.action == "verify_cold" for item in questions):
        hint = (
            "every member is cold (no ledger entry) — for a freshly authored or "
            "never-recorded deck, review both halves (`record` asserts they are in "
            "sync; its gate only proves the pair is structurally sound, companions "
            "included) and then bank it wholesale with `clm slides sync record DECK` "
            "instead of confirming per item"
        )
        # Y5: an all-cold report is also what the stamp trust gate produces —
        # and record banks the positionally guessed id-stamp pairings along
        # with everything else, so the hint must name them as part of the
        # review it calls for. (Only GATED stamps are named: an emitted
        # stamp_twin_id row is ledger-known by construction.)
        stamped = {item.key for item in diff.items if item.action == "stamp_twin_id"}
        if any(
            obs.kind == "id_stamp_pending_twin"
            and obs.member is not None
            and obs.member.render() not in stamped
            for obs in diff.observations
        ):
            hint += (
                "; review any pending id-stamp pairings especially — `record` "
                "banks a positionally paired twin as the member's identity, so "
                "a swapped pool order becomes permanent"
            )
        return hint
    return None


def report_id_for(bundle: LoadedBundle, ledger: doc_ledger.TopicLedger | None = None) -> str:
    """The schema-4 freshness token for one pair (:mod:`clm.slides.sync_wire`).

    ``hash(bundle bytes + this deck's ledger section)`` — the two inputs that
    together decide every verdict in the report. An agent echoes the value in
    its decision document and ``apply`` refuses the document when the current
    value differs, so "the report you answered no longer describes this deck"
    becomes a first-class, self-explaining refusal instead of a set of
    handle-by-handle rejections whose writes had already landed (#649, Q2).

    Deliberately covers the *whole* bundle, not just the deck halves: a
    separated voiceover companion is part of the same member table, and the
    ``voiceover_x`` / ``slides_x`` CLI spellings resolve to one deck, so a
    companion edit must invalidate the deck's report.

    The bundle half comes from the **projection** of the parsed deck, not from
    re-reading the files. ``project . parse`` is byte-identity by construction
    (design §4, property-tested), so the value is the same while a sweep pays
    no second read per file — and, more importantly, report time and apply
    time run the *same* function, so the two ends cannot disagree even about a
    byte the lens would normalize. A bundle that refuses to parse has no
    projection; there the file bytes are hashed directly.
    """
    digest = hashlib.sha256()
    deck = bundle.outcome.deck
    for path, lang, part in (
        (bundle.de_path, "de", "deck"),
        (bundle.en_path, "en", "deck"),
        (bundle.de_companion_path, "de", "companion"),
        (bundle.en_companion_path, "en", "companion"),
    ):
        if path is None:
            digest.update(b"\x00absent\x00")
            continue
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\x00")
        text = project(deck, lang, part) if deck is not None else None  # type: ignore[arg-type]
        if text is not None:
            digest.update(text.encode("utf-8"))
        else:
            try:
                digest.update(path.read_bytes())
            except OSError:  # pragma: no cover - the bundle was just read
                digest.update(b"<unreadable>")
        digest.update(b"\x00")
    if ledger is None:
        ledger = doc_ledger.load(doc_ledger.ledger_path_for(bundle.de_path))
    digest.update(
        doc_ledger.deck_section_fingerprint(ledger, doc_ledger.deck_key_for(bundle.de_path)).encode(
            "utf-8"
        )
    )
    return digest.hexdigest()[:16]


def pair_payload(
    bundle: LoadedBundle,
    diff: DeckDiff,
    *,
    ledger: doc_ledger.TopicLedger | None = None,
    base_diffs: dict[str, MemberBaseDiff] | None = None,
    batch: bool = True,
) -> dict:
    """The full schema-5 report payload for one pair.

    ``ledger`` is the already-loaded topic ledger when the caller has one
    (:func:`diff_bundle_with_ledger`): the token needs the same section the
    baseline came from, and re-loading it per deck is pure waste on a sweep.

    ``base_diffs`` comes from :func:`clm.slides.base_recovery.
    recover_base_diffs` — the caller runs the recovery because only it knows
    the right candidate refs (the ``--since`` view diffs against a *named*
    commit; the ledger view walks history). Passed through to the item rows,
    and the deck-level ``verify_translation_batch`` observation is appended
    when every such row recovered the same base.

    ``batch=False`` is the ``--since`` caller: there every changed row
    trivially "recovers" at the named ref (the base fps were *computed* from
    it), so the same-base grouping is the query parameter echoed back, not a
    discovered common sync point — the observation would always fire and
    always overclaim "one editing session".
    """
    payload = diff.to_payload()
    payload["items"] = item_payloads(diff, base_diffs=base_diffs)
    if base_diffs and batch:
        batch_obs = batch_observation(diff, base_diffs)
        if batch_obs is not None:
            payload["observations"] = [
                *payload["observations"],
                {
                    "kind": batch_obs.kind,
                    "member": None,
                    "side": batch_obs.side,
                    "detail": batch_obs.detail,
                },
            ]
    payload["de_path"] = str(bundle.de_path)
    payload["en_path"] = str(bundle.en_path)
    # The deck's trust identity, spelled out: `voiceover_x` and `slides_x` are
    # two CLI spellings of ONE deck sharing ONE ledger section, which is how
    # #649's second apply found its decisions already satisfied.
    payload["deck_key"] = doc_ledger.deck_key_for(bundle.de_path)
    payload["ledger"] = str(doc_ledger.ledger_path_for(bundle.de_path))
    payload["report_id"] = report_id_for(bundle, ledger)
    hint = cold_sweep_hint(diff)
    if hint is not None:
        payload["hint"] = hint
    return payload
