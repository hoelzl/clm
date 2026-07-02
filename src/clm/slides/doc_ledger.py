"""The v3 member-keyed sync ledger — the committed trust store (#520 Phase 3).

Design: ``docs/claude/design/sync-total-identity-document-model.md`` §5. The
per-topic committed file ``<topic>/.clm/sync-ledger.json`` becomes the **only**
trust store of the v3 engine: per :class:`~clm.slides.bilingual_doc.MemberKey`,
it records the member's verified state — langness, layout, per-side content
fingerprints, tags, provenance, and the hash-function version — plus the
per-deck order context (id-keyed member order, group order, preamble
fingerprints) the differ needs to judge ``order`` outcomes.

**Coexistence with the v1 ledger (Phase 3 only).** Through Phase 3 the v2
engine remains the default and keeps its ``(slide_id, role)``-keyed sections in
the same file. The file therefore carries a schema-2 envelope holding *both*:
the v1 ``slides`` / ``idless`` sections (opaque to this module — preserved
verbatim on save) and the v3 ``decks`` section (opaque to ``sync_ledger`` —
preserved verbatim there). Neither engine can clobber the other's trust.
Phase 4 deletes the v1 sections with the v2 core.

**Trust semantics (§5).**

* A member with **no entry is cold** — the differ reports it ``unverified``
  with a framed verification task, never a silent assumption. This is what
  :attr:`~clm.slides.sync_diff.DeckBaseline.complete` ``= False`` encodes.
* **Stale = fingerprint mismatch** — fail-safe by construction: a drifted
  member produces a re-check item.
* ``hash_version`` gates every entry: an entry recorded under an older
  fingerprint function is dropped to cold at load (re-verify, never trust a
  hash a newer engine would compute differently — the #458 lesson).
* Ledger merge conflicts are true positives; canonical sorted JSON keeps a
  per-topic merge local and line-mergeable (drop-to-``unverified`` on a
  genuine same-member conflict).

This module is part of the v3 core: pure storage + snapshot plumbing, no
imports from the v2 sync core (``sync_plan`` / ``sync_apply`` / ``sync_code``)
— enforced by the import-cleanliness test (design §12.5). The structural
verify gate on the write path lives at the verb layer (the CLI ``record`` /
``apply`` runners), because ``sync_verify`` still imports v2 modules.
"""

from __future__ import annotations

import json
from pathlib import Path

from attrs import define, field, frozen

from clm.slides.bilingual_doc import BilingualDeck, Lang
from clm.slides.sync_diff import DeckBaseline, MemberBaseline, baseline_from_deck

__all__ = [
    "LEDGER_FILENAME",
    "LEDGER_HASH_VERSION",
    "LEDGER_SUBDIR",
    "SCHEMA_VERSION",
    "DeckLedger",
    "LedgerMember",
    "TopicLedger",
    "baseline_from_ledger",
    "deck_key_for",
    "ledger_path_for",
    "load",
    "record_deck_snapshot",
    "save",
]

#: The v3 envelope schema. Schema 1 files (v1-only) load as an empty v3 store
#: with the v1 payload preserved; schema 2 carries both engines' sections.
SCHEMA_VERSION = 2

#: Version of the v3 fingerprint functions (:func:`~clm.slides.sync_diff.content_fingerprint`
#: and friends). Bump when the hashing form changes; entries recorded under an
#: older version drop to cold at load (§5's lazy migration rule, #458).
LEDGER_HASH_VERSION = 1

#: Same committed location the v1 ledger established (issue #448 / #453);
#: duplicated here (not imported) so Phase 4 can delete ``sync_ledger`` whole.
LEDGER_SUBDIR = ".clm"
LEDGER_FILENAME = "sync-ledger.json"

#: The v1 sections this module must round-trip untouched.
_V1_KEYS = ("slides", "idless")

_SIDES: tuple[Lang, Lang] = ("de", "en")


@frozen
class LedgerMember:
    """One member's recorded §5 entry: the engine view plus trust metadata.

    ``entry`` is the exact :class:`~clm.slides.sync_diff.MemberBaseline` the
    differ compares against — fingerprints per side, tags, owner-free
    signatures. ``provenance`` records *who* asserted the verification
    (``apply`` / ``accept`` / ``record`` / ``agent`` / ``semantic:<model>``),
    kept so a later run can selectively distrust a source without nuking the
    ledger. ``state`` is always ``verified`` today (an unverified member is
    simply absent).
    """

    entry: MemberBaseline
    provenance: str
    state: str = "verified"
    hash_version: int = LEDGER_HASH_VERSION
    confirmed_commit: str | None = None


@define
class DeckLedger:
    """The recorded state of one deck bundle inside its topic ledger.

    Mirrors :class:`~clm.slides.sync_diff.DeckBaseline` (members + order
    context) with per-member trust metadata. Order context is recorded when
    the corresponding scope was verified (a full ``record``, or an applied
    order item) — a scope with no recorded order simply contributes no order
    trust, it is never assumed.
    """

    members: dict[str, LedgerMember] = field(factory=dict)
    group_order: list[str] = field(factory=list)
    group_order_by_side: dict[str, list[str]] = field(factory=dict)
    #: keyed ``(lang, group, part)`` exactly as ``DeckBaseline.member_order``
    member_order: dict[tuple[str, str, str], list[str]] = field(factory=dict)
    #: keyed ``(lang, part)`` exactly as ``DeckBaseline.preamble_fps``
    preamble_fps: dict[tuple[str, str], str | None] = field(factory=dict)


@define
class TopicLedger:
    """The whole per-topic file: v3 deck sections + the opaque v1 payload."""

    decks: dict[str, DeckLedger] = field(factory=dict)
    #: v1 ``slides`` / ``idless`` sections, preserved verbatim (never read).
    v1_payload: dict[str, object] = field(factory=dict)


# ---------------------------------------------------------------------------
# Paths and deck keys
# ---------------------------------------------------------------------------


def ledger_path_for(de_path: Path) -> Path:
    """The committed ledger path for the topic owning ``de_path``."""
    return de_path.parent / LEDGER_SUBDIR / LEDGER_FILENAME


def deck_key_for(de_path: Path) -> str:
    """The deck's key inside its topic ledger: the language-free stem.

    ``slides_intro.de.py`` → ``slides_intro`` (any source suffix). Deck
    identity stays path-derived through Phase 3 (design §12 decision 4); a
    renamed deck cold-starts, which is fail-safe.
    """
    stem = de_path.name
    stem = stem[: -len(de_path.suffix)] if de_path.suffix else stem
    for lang_suffix in (".de", ".en"):
        if stem.endswith(lang_suffix):
            return stem[: -len(lang_suffix)]
    return stem


# ---------------------------------------------------------------------------
# Serialization — canonical sorted JSON (merge-local, line-mergeable)
# ---------------------------------------------------------------------------


def _member_to_json(lm: LedgerMember) -> dict[str, object]:
    e = lm.entry
    return {
        "langness": e.langness,
        "layout": e.layout,
        "kind": e.kind,
        "role": e.role,
        "owner": e.owner,
        "de_fp": e.de_fp,
        "en_fp": e.en_fp,
        "de_body_fp": e.de_body_fp,
        "en_body_fp": e.en_body_fp,
        "de_tags": list(e.de_tags) if e.de_tags is not None else None,
        "en_tags": list(e.en_tags) if e.en_tags is not None else None,
        "de_sig": e.de_sig,
        "en_sig": e.en_sig,
        "provenance": lm.provenance,
        "state": lm.state,
        "hash_version": lm.hash_version,
        "confirmed_commit": lm.confirmed_commit,
    }


def _member_from_json(key: str, rec: dict) -> LedgerMember | None:
    try:
        entry = MemberBaseline(
            key=key,
            langness=rec["langness"],
            layout=rec["layout"],
            kind=rec["kind"],
            role=rec["role"],
            owner=rec.get("owner"),
            de_fp=rec.get("de_fp"),
            en_fp=rec.get("en_fp"),
            de_body_fp=rec.get("de_body_fp"),
            en_body_fp=rec.get("en_body_fp"),
            de_tags=tuple(rec["de_tags"]) if rec.get("de_tags") is not None else None,
            en_tags=tuple(rec["en_tags"]) if rec.get("en_tags") is not None else None,
            de_sig=rec.get("de_sig"),
            en_sig=rec.get("en_sig"),
        )
    except (KeyError, TypeError):
        return None  # malformed entry: cold, never a crash
    return LedgerMember(
        entry=entry,
        provenance=str(rec.get("provenance", "record")),
        state=str(rec.get("state", "verified")),
        hash_version=int(rec.get("hash_version", 0)),
        confirmed_commit=rec.get("confirmed_commit"),
    )


def _deck_to_json(deck: DeckLedger) -> dict[str, object]:
    return {
        "members": {key: _member_to_json(lm) for key, lm in deck.members.items()},
        "group_order": list(deck.group_order),
        "group_order_by_side": {
            lang: list(order) for lang, order in deck.group_order_by_side.items()
        },
        "member_order": [
            {"lang": lang, "group": group, "part": part, "handles": list(handles)}
            for (lang, group, part), handles in sorted(deck.member_order.items())
        ],
        "preamble_fps": {
            f"{lang}:{part}": fp for (lang, part), fp in sorted(deck.preamble_fps.items())
        },
    }


def _deck_from_json(rec: dict) -> DeckLedger:
    deck = DeckLedger()
    members = rec.get("members", {})
    if isinstance(members, dict):
        for key, entry_rec in members.items():
            if not isinstance(entry_rec, dict):
                continue
            lm = _member_from_json(key, entry_rec)
            if lm is not None:
                deck.members[key] = lm
    group_order = rec.get("group_order", [])
    if isinstance(group_order, list):
        deck.group_order = [str(g) for g in group_order]
    by_side = rec.get("group_order_by_side", {})
    if isinstance(by_side, dict):
        deck.group_order_by_side = {
            str(lang): [str(g) for g in order]
            for lang, order in by_side.items()
            if isinstance(order, list)
        }
    member_order = rec.get("member_order", [])
    if isinstance(member_order, list):
        for row in member_order:
            if not isinstance(row, dict) or not isinstance(row.get("handles"), list):
                continue
            try:
                key = (str(row["lang"]), str(row["group"]), str(row["part"]))
            except KeyError:
                continue
            deck.member_order[key] = [str(h) for h in row["handles"]]
    preambles = rec.get("preamble_fps", {})
    if isinstance(preambles, dict):
        for joined, fp in preambles.items():
            lang, sep, part = str(joined).partition(":")
            if sep:
                deck.preamble_fps[(lang, part)] = fp if isinstance(fp, str) else None
    return deck


def load(path: Path) -> TopicLedger:
    """Read a topic ledger; absent/malformed degrades to empty (fail-safe cold).

    Accepts schema 1 (a v1-only file: empty v3 store, v1 sections preserved)
    and schema 2 (the coexistence envelope). Anything else is treated as
    empty — the deck cold-starts, never crashes and never trusts.
    """
    if not path.is_file():
        return TopicLedger()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return TopicLedger()
    if not isinstance(data, dict) or data.get("schema") not in (1, SCHEMA_VERSION):
        return TopicLedger()
    ledger = TopicLedger(
        v1_payload={k: data[k] for k in _V1_KEYS if k in data},
    )
    decks = data.get("decks", {})
    if isinstance(decks, dict):
        for deck_key, rec in decks.items():
            if isinstance(rec, dict):
                ledger.decks[deck_key] = _deck_from_json(rec)
    return ledger


def _to_json(ledger: TopicLedger) -> str:
    payload: dict[str, object] = {
        "schema": SCHEMA_VERSION,
        "decks": {key: _deck_to_json(deck) for key, deck in sorted(ledger.decks.items())},
    }
    payload.update(ledger.v1_payload)
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def save(ledger: TopicLedger, path: Path) -> None:
    """Write the ledger atomically (canonical JSON), creating ``.clm/``."""
    from clm.infrastructure.utils.path_utils import atomic_write_bytes

    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, _to_json(ledger).encode("utf-8"))


# ---------------------------------------------------------------------------
# Building the differ's baseline from the ledger (§5 → §6.1)
# ---------------------------------------------------------------------------


def baseline_from_ledger(deck_ledger: DeckLedger) -> DeckBaseline:
    """The :class:`DeckBaseline` view of a recorded deck — ``complete=False``.

    A member missing here is **cold** (an ``unverified`` framed item), never
    "new". Entries whose ``hash_version`` predates the current fingerprint
    function are dropped to cold (§5 lazy migration): their hashes are
    incomparable, so re-verify instead of mis-trusting.
    """
    base = DeckBaseline(complete=False)
    for key, lm in deck_ledger.members.items():
        if lm.hash_version != LEDGER_HASH_VERSION:
            continue  # stale hashing form: cold, re-verify (#458)
        base.members[key] = lm.entry
    base.group_order = list(deck_ledger.group_order)
    base.group_order_by_side = {
        lang: list(deck_ledger.group_order_by_side.get(lang, [])) for lang in _SIDES
    }
    for (lang, group, part), handles in deck_ledger.member_order.items():
        for side in _SIDES:
            if lang == side:
                base.member_order[(side, group, part)] = list(handles)
    base.preamble_fps = dict(deck_ledger.preamble_fps)
    return base


# ---------------------------------------------------------------------------
# Recording (the write path — the verb-layer callers gate on verify)
# ---------------------------------------------------------------------------


def snapshot_deck(
    deck: BilingualDeck,
    *,
    provenance: str,
    commit: str | None = None,
) -> DeckLedger:
    """A full :class:`DeckLedger` snapshot of a parsed deck's current state."""
    base = baseline_from_deck(deck)
    return DeckLedger(
        members={
            key: LedgerMember(entry=entry, provenance=provenance, confirmed_commit=commit)
            for key, entry in base.members.items()
        },
        group_order=list(base.group_order),
        group_order_by_side={lang: list(base.group_order_by_side[lang]) for lang in _SIDES},
        member_order={key: list(handles) for key, handles in base.member_order.items()},
        preamble_fps=dict(base.preamble_fps),
    )


def record_deck_snapshot(
    ledger: TopicLedger,
    deck_key: str,
    deck: BilingualDeck,
    *,
    provenance: str,
    commit: str | None = None,
    member_keys: set[str] | None = None,
) -> tuple[int, dict[str, str]]:
    """Record ``deck``'s current state into ``ledger`` (in memory).

    Full record (``member_keys=None``): the deck section is replaced
    wholesale — stale keys (removed members, superseded ``pos:`` ordinals)
    are swept, and any §7.3 ``pos → id`` key migration happens implicitly:
    the member is re-recorded under its current (id) key. Returns the number
    of member entries written and the detected key migrations
    ``{old_key: new_key}`` (same fingerprints under a renamed key — the
    explicit, logged rename the design demands).

    Partial record: only the listed member keys are upserted (order/preamble
    pseudo-scopes go through the ``record_*_scope`` helpers); everything
    else — including possibly-stale twin entries — is left in place, which is
    fail-safe (a stale entry mismatches and re-checks, never silently
    trusts). A ``pos:`` key re-records its whole ``(group, kind)`` pool —
    positional ordinals renumber together, so a per-entry patch would leave
    aliased ordinals (see the scope-update rules below).
    """
    fresh = snapshot_deck(deck, provenance=provenance, commit=commit)
    old = ledger.decks.get(deck_key)
    migrations = _detect_key_migrations(old, fresh) if old is not None else {}
    if member_keys is None:
        ledger.decks[deck_key] = fresh
        return len(fresh.members), migrations
    target = old if old is not None else DeckLedger()
    recorded = 0
    for key in sorted(member_keys):
        if key.startswith("pos:"):
            group, kind, _ordinal = key.split(":", 1)[1].rsplit("/", 2)
            recorded += rerecord_pool(target, fresh, group, kind)
            continue
        lm = fresh.members.get(key)
        if lm is None:
            continue
        for old_key, new_key in migrations.items():
            if new_key == key:
                target.members.pop(old_key, None)
        target.members[key] = lm
        recorded += 1
    ledger.decks[deck_key] = target
    return recorded, {k: v for k, v in migrations.items() if v in member_keys}


def _detect_key_migrations(old: DeckLedger, fresh: DeckLedger) -> dict[str, str]:
    """Detect §7.3 ``pos → id`` renames between two snapshots.

    A vanished ``pos:`` entry whose per-side fingerprints reappear under a
    *new* ``id:`` key is the same member under its minted id. Detection is
    conservative (unique fingerprint match only) — it exists for the log and
    for partial records; a full record re-keys wholesale anyway.
    """
    gone = {
        key: lm
        for key, lm in old.members.items()
        if key.startswith("pos:") and key not in fresh.members
    }
    new_idd = {
        key: lm
        for key, lm in fresh.members.items()
        if key.startswith("id:") and key not in old.members
    }
    migrations: dict[str, str] = {}
    claimed: set[str] = set()
    for old_key, old_lm in sorted(gone.items()):
        matches = [
            new_key
            for new_key, new_lm in sorted(new_idd.items())
            if new_key not in claimed
            and (new_lm.entry.de_fp, new_lm.entry.en_fp) == (old_lm.entry.de_fp, old_lm.entry.en_fp)
        ]
        if len(matches) == 1:
            migrations[old_key] = matches[0]
            claimed.add(matches[0])
    return migrations


# ---------------------------------------------------------------------------
# Surgical scope updates (the per-item apply write path)
#
# Positional entries are pool-scoped: any change inside a (group, kind) pool
# renumbers its ordinals, so the pool is always re-recorded WHOLESALE from the
# post-apply snapshot — a per-entry patch could leave aliased ordinals. Order
# and preamble trust likewise updates per scope, and only for scopes an
# applied item actually verified — never wholesale (which would silently
# bless pending divergences elsewhere in the deck).
# ---------------------------------------------------------------------------


def rerecord_pool(target: DeckLedger, fresh: DeckLedger, group: str, kind: str) -> int:
    """Replace every ``pos:<group>/<kind>/*`` entry with the fresh pool state."""
    prefix = f"pos:{group}/{kind}/"
    for key in [k for k in target.members if k.startswith(prefix)]:
        del target.members[key]
    copied = 0
    for key, lm in fresh.members.items():
        if key.startswith(prefix):
            target.members[key] = lm
            copied += 1
    return copied


def record_order_scope(target: DeckLedger, fresh: DeckLedger, group: str, part: str) -> None:
    """Adopt the fresh id-keyed member order for ``(group, part)``, both sides."""
    for lang in _SIDES:
        key = (lang, group, part)
        if key in fresh.member_order:
            target.member_order[key] = list(fresh.member_order[key])
        else:
            target.member_order.pop(key, None)


def record_group_order(target: DeckLedger, fresh: DeckLedger) -> None:
    """Adopt the fresh group order (both the merged and the per-side views)."""
    target.group_order = list(fresh.group_order)
    target.group_order_by_side = {
        lang: list(order) for lang, order in fresh.group_order_by_side.items()
    }


def record_preamble_scope(target: DeckLedger, fresh: DeckLedger, part: str) -> None:
    """Adopt the fresh preamble fingerprints for ``part`` (both sides)."""
    for lang in _SIDES:
        key = (lang, part)
        if key in fresh.preamble_fps:
            target.preamble_fps[key] = fresh.preamble_fps[key]
        else:
            target.preamble_fps.pop(key, None)


def rename_group_scopes(target: DeckLedger, old_group: str, new_group: str) -> None:
    """Re-key every scope referencing a renamed group (§7.3 group rename).

    Covers the ``pos:`` member keys (their group token), the member-order
    scopes, and the group-order lists. The anchor's own ``id:`` entry is
    re-keyed by the caller through the member migration path.
    """
    for key in [k for k in target.members if k.startswith(f"pos:{old_group}/")]:
        lm = target.members.pop(key)
        suffix = key[len(f"pos:{old_group}/") :]
        new_key = f"pos:{new_group}/{suffix}"
        entry = MemberBaseline(
            key=new_key,
            langness=lm.entry.langness,
            layout=lm.entry.layout,
            kind=lm.entry.kind,
            role=lm.entry.role,
            owner=lm.entry.owner,
            de_fp=lm.entry.de_fp,
            en_fp=lm.entry.en_fp,
            de_body_fp=lm.entry.de_body_fp,
            en_body_fp=lm.entry.en_body_fp,
            de_tags=lm.entry.de_tags,
            en_tags=lm.entry.en_tags,
            de_sig=lm.entry.de_sig,
            en_sig=lm.entry.en_sig,
        )
        target.members[new_key] = LedgerMember(
            entry=entry,
            provenance=lm.provenance,
            state=lm.state,
            hash_version=lm.hash_version,
            confirmed_commit=lm.confirmed_commit,
        )
    for lang, group, part in [k for k in target.member_order if k[1] == old_group]:
        target.member_order[(lang, new_group, part)] = target.member_order.pop((lang, group, part))
    target.group_order = [new_group if g == old_group else g for g in target.group_order]
    target.group_order_by_side = {
        lang: [new_group if g == old_group else g for g in order]
        for lang, order in target.group_order_by_side.items()
    }
