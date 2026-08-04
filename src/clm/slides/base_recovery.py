"""Recovering the recorded base bytes for framed translation rows (#773 phase 1).

A ``verify_translation`` row says *both halves of a localized member moved off
base*; a ``translate_edit`` row says one did. Either way the reader's measured
cost is re-deriving *what changed on each side* by comparing two full cells by
eye — 68.4% of all framed rows over 200 reference-repo commits are
``verify_translation`` (``scripts/measure_sync_ceremony.py``). The ledger can
*recognize* the base state anywhere (it stores per-side fingerprints) but
cannot *reproduce* it — deliberately: ``sync-consistency-ledger.md`` §11.3
settled on ``hash + confirmed_commit``, no full row. Git, however, usually has
the bytes: decks sync and commit routinely, so the recorded state sits a few
commits back. This module walks the bundle's recent change-commits newest
first, finds for each row the newest commit whose bytes the ledger
fingerprints recognize, and renders per-side unified diffs against exactly
that state.

View-layer only (design §12.3 — the ``--since`` posture): read-only git, no
ledger schema change, no trust semantics, and the answer surface is untouched.
Recovery **degrades to absence**: a base that was never committed, a history
rewritten away, a renamed deck, or a repo without git simply yields no entry
for the row, and the report keeps shipping the full cells. Absence is honest,
and a match is the recorded state **modulo the slide_id attribute** — the same
equivalence the ledger's fingerprints define (both sides must match exactly,
and the match is key-aware: an id-keyed row only matches the member carrying
*its own* id, and a pos-keyed row only matches id-less members, so another
member's fingerprint lookalike — copy-pasted boilerplate — can neither steal
the match at a newer ref nor leak its id into the hunks).

Hash-version drift needs no handling here: a ledger entry recorded under an
older ``LEDGER_HASH_VERSION`` drops to cold at load (``doc_ledger``), so it
frames ``verify_cold`` — never one of the recovered actions — and no entry
with foreign-version fingerprints can reach this walk.

What must NOT grow out of this module (design note §4): no auto-resolution of
``verify_translation`` at any threshold, count, or similarity score. Diffs and
the batch observation inform a human/agent judgement; only explicit per-row
answers resolve rows.
"""

from __future__ import annotations

import difflib
from collections.abc import Sequence

from attrs import frozen

from clm.slides.bilingual_doc import Lang, Member, Observation
from clm.slides.doc_identity import content_fingerprint, iter_with_groups
from clm.slides.doc_lenses import LoadedBundle, parse_bundle
from clm.slides.git_text import bundle_texts_at_ref, recent_change_refs, resolve_commit
from clm.slides.sync_diff import DeckDiff, DiffItem

__all__ = [
    "BASE_DIFF_ACTIONS",
    "MemberBaseDiff",
    "batch_observation",
    "recover_base_diffs",
]

#: The framed actions whose rows carry a recorded base worth recovering.
#: ``verify_translation`` is the measured 68% ceremony class; ``translate_edit``
#: rides along because the same walk serves it at near-zero marginal cost
#: (maintainer decision on the design note's §7, 2026-08-04).
BASE_DIFF_ACTIONS = frozenset({"verify_translation", "translate_edit"})

#: How many bundle change-commits the walk may inspect (§7 default accepted
#: 2026-08-04). The last sync is typically 1–5 changes back; the cap bounds
#: the cost when it is not, and past it recovery degrades to absence.
_WALK_CAP = 30

#: Context lines per hunk. The full cells still ship next to the diff
#: (``de``/``en``/``*_body``), so hunks stay compact — locating context, not
#: reading context.
_DIFF_CONTEXT = 2

#: Rows before the deck-level batch observation is worth printing — the
#: ``uniform_drift_side`` floor (Q5), same rationale: below it the aggregation
#: collapses no ceremony.
_BATCH_MIN = 3

_SIDES: tuple[Lang, Lang] = ("de", "en")


@frozen
class MemberBaseDiff:
    """One recovered row: the newest commit holding its base, plus per-side hunks.

    ``de_diff`` / ``en_diff`` are unified-diff hunks (no ``---``/``+++`` file
    headers) from the base cell's lines to the current cell's lines. ``""``
    means the side is byte-identical to its base (the unmoved side of a
    ``translate_edit``); ``None`` means the side exists neither now nor at
    base, so there is nothing to diff.
    """

    base_ref: str  # full commit sha — `git show <sha>` works verbatim
    de_diff: str | None
    en_diff: str | None

    def side_diff(self, lang: Lang) -> str | None:
        return self.de_diff if lang == "de" else self.en_diff


def recover_base_diffs(
    bundle: LoadedBundle,
    diff: DeckDiff,
    *,
    candidates: Sequence[str] | None = None,
    cap: int = _WALK_CAP,
) -> dict[str, MemberBaseDiff]:
    """Per-item base diffs for ``diff``'s recoverable rows, keyed by item key.

    Walks ``candidates`` (or, by default, the bundle's recent change-commits,
    newest first, capped) and resolves each target row at the first ref whose
    parse contains a member matching the row's recorded fingerprints on both
    sides. Rows that match nowhere inside the cap are simply absent from the
    result — the caller ships them exactly as before.

    ``candidates`` is for callers that already know the base ref (the
    ``--since REF`` forensic view diffs against a *named* commit, so walking
    history for it would be both wasteful and wrong). Each candidate is
    resolved to its full commit sha first — ``base_ref`` is a *stored* value,
    and a relative spelling (``HEAD~2``) would name a different commit by the
    time a consumer reads it.
    """
    targets: dict[str, DiffItem] = {}
    for item in diff.items:
        if item.action not in BASE_DIFF_ACTIONS or item.base is None:
            continue
        if item.base.de_fp is None and item.base.en_fp is None:
            continue  # nothing recorded — no state to recognize
        # One member frames at most one of these actions per pass (the emit
        # branches are mutually exclusive), so the key cannot collide here.
        targets[item.key] = item
    if not targets:
        return {}
    if candidates is not None:
        refs = [
            sha
            for sha in (resolve_commit(bundle.de_path, ref) for ref in candidates)
            if sha is not None
        ]
    else:
        refs = recent_change_refs(bundle.de_path, bundle.en_path, cap=cap)
    recovered: dict[str, MemberBaseDiff] = {}
    for ref in refs:
        if len(recovered) == len(targets):
            break
        index = _members_by_fps_at_ref(bundle, ref)
        if index is None:
            continue  # bundle absent or refusing to parse at this ref
        for key, item in targets.items():
            if key in recovered:
                continue
            base = item.base
            assert base is not None  # targets filtered above
            matches = index.get((base.de_fp, base.en_fp))
            if not matches:
                continue
            member = _key_aware_match(key, matches)
            if member is None:
                continue
            recovered[key] = _member_base_diff(item, member, ref)
    return recovered


def _key_aware_match(key: str, matches: list[Member]) -> Member | None:
    """The member that may serve as ``key``'s base among fingerprint matches.

    Fingerprints are modulo the ``slide_id`` attribute, so a fingerprint
    lookalike can be *another* member entirely — copy-pasted boilerplate under
    its own id. Matching it would steal the recovery at a newer ref (the true
    base sits one ref older, unreachable once this row resolves) and fabricate
    an id-rename hunk that never happened. The true base satisfies the key
    rule *under the row's recorded identity*: a rename's row frames under the
    old handle (which old refs carry), and a §7.3 key migration re-keys the
    entry. The one residual window is a member id-stamped since its last
    record whose base was committed only id-less — there recovery degrades to
    absence (honest, never wrong bytes):

    * id-keyed row → only the member carrying its own key;
    * pos-keyed row → only id-less members (ordinals alias across states, so
      the rendered key means nothing, but an id'd lookalike would leak its
      ``slide_id`` line into the hunks).
    """
    if key.startswith("id:"):
        return next((m for m in matches if m.key.render() == key), None)
    return next((m for m in matches if m.key.scheme == "pos"), None)


def batch_observation(diff: DeckDiff, recovered: dict[str, MemberBaseDiff]) -> Observation | None:
    """One deck-level line when every ``verify_translation`` row shares a base.

    The 32-row deck is one editing session, and the reader should learn that
    once, not 32 times: all rows recovering the *same* ``base_ref`` means they
    diverged from one sync point. Like ``uniform_drift_side`` (Q5), this adds
    aggregation, never information — and it must never overclaim: a row whose
    base did not recover suppresses the observation (a claim about "all N"
    that covers fewer than N is a false summary), and the detail says
    explicitly that no batch answer exists (design note §4).
    """
    rows = [item for item in diff.items if item.action == "verify_translation"]
    if len(rows) < _BATCH_MIN:
        return None
    if any(item.key not in recovered for item in rows):
        return None
    refs = {recovered[item.key].base_ref for item in rows}
    if len(refs) != 1:
        return None
    ref = next(iter(refs))
    return Observation(
        kind="verify_translation_batch",
        detail=(
            f"all {len(rows)} verify_translation rows diverge from the same base "
            f"{ref[:12]} — one editing session, most likely. Read each row's "
            f"de_diff/en_diff instead of comparing full cells; if the hunks repeat "
            f"one pattern (a rename, renumbering, a formatting sweep), judge the "
            f"pattern once. Every row still takes its own answer — there is no "
            f"batch answer, at any count."
        ),
    )


def _members_by_fps_at_ref(
    bundle: LoadedBundle, ref: str
) -> dict[tuple[str | None, str | None], list[Member]] | None:
    """The ref's members indexed by per-side content fingerprints, or ``None``.

    ``None`` when either deck half is absent at the ref or the bundle there
    refuses to parse — the walk skips such refs and keeps going (an
    intermediate broken commit must not end recovery for older, valid bases).
    """
    de, en, de_companion, en_companion = bundle_texts_at_ref(bundle.de_path, bundle.en_path, ref)
    if de is None or en is None:
        return None
    outcome = parse_bundle(de, en, de_companion, en_companion, comment_token=bundle.comment_token)
    if outcome.deck is None:
        return None
    index: dict[tuple[str | None, str | None], list[Member]] = {}
    for member, _group in iter_with_groups(outcome.deck):
        fps = (
            content_fingerprint(member.de) if member.de is not None else None,
            content_fingerprint(member.en) if member.en is not None else None,
        )
        index.setdefault(fps, []).append(member)
    return index


def _member_base_diff(item: DiffItem, base_member: Member, ref: str) -> MemberBaseDiff:
    diffs: dict[Lang, str | None] = {}
    for lang in _SIDES:
        # The DiffItem side convention (`payload()` uses the same): when `twin`
        # is set, `member` carries the slot's DE cell and `twin` its EN cell.
        holder = item.twin if (item.twin is not None and lang == "en") else item.member
        current = holder.side(lang) if holder is not None else None
        base_cell = base_member.side(lang)
        if current is None and base_cell is None:
            diffs[lang] = None
            continue
        hunks = list(
            difflib.unified_diff(
                list(base_cell.lines) if base_cell is not None else [],
                list(current.lines) if current is not None else [],
                n=_DIFF_CONTEXT,
                lineterm="",
            )
        )
        # unified_diff yields nothing for equal inputs and two `---`/`+++`
        # file-header lines otherwise; the hunks are what the reader needs.
        diffs[lang] = "\n".join(hunks[2:])
    return MemberBaseDiff(base_ref=ref, de_diff=diffs["de"], en_diff=diffs["en"])
