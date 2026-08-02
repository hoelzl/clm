"""The v3 member-keyed sync ledger — the committed trust store (#520 Phase 3).

Design: ``docs/claude/design/sync-total-identity-document-model.md`` §5. The
per-topic committed file ``<topic>/.clm/sync-ledger.json`` becomes the **only**
trust store of the v3 engine: per :class:`~clm.slides.bilingual_doc.MemberKey`,
it records the member's verified state — langness, layout, per-side content
fingerprints, tags, provenance, and the hash-function version — plus the
per-deck order context (id-keyed member order, group order, preamble
fingerprints) the differ needs to judge ``order`` outcomes.

**v1 sections (deleted at the Phase 4 cutover).** Through Phase 3 the file
carried a coexistence envelope holding both the v1 engine's ``(slide_id,
role)``-keyed ``slides`` / ``idless`` sections and the v3 ``decks`` section.
With the v2 engine removed, ``load`` still accepts schema-1/2 files but the
v1 sections are **dropped on the next save** — the ``decks`` store is the
whole file.

**Trust semantics (§5).**

* A member with **no entry is cold** — the differ reports it ``unverified``
  with a framed verification task, never a silent assumption. This is what
  :attr:`~clm.slides.doc_identity.DeckBaseline.complete` ``= False`` encodes.
* **Stale = fingerprint mismatch** — fail-safe by construction: a drifted
  member produces a re-check item.
* ``hash_version`` gates every entry: an entry recorded under an older
  fingerprint function is dropped to cold at load (re-verify, never trust a
  hash a newer engine would compute differently — the #458 lesson).
* Ledger merge conflicts are true positives; canonical sorted JSON keeps a
  per-topic merge local and line-mergeable (drop-to-``unverified`` on a
  genuine same-member conflict).

This module is pure storage + snapshot plumbing. The structural verify gate
on the write path lives at the verb layer (the CLI ``record`` / ``apply``
runners).
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from attrs import define, evolve, field, frozen

from clm.slides.bilingual_doc import BilingualDeck, Lang
from clm.slides.doc_identity import DeckBaseline, MemberBaseline, baseline_from_deck

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
    "deck_section_fingerprint",
    "ledger_path_for",
    "load",
    "preserve_unchanged_member",
    "record_deck_snapshot",
    "save",
]

#: The envelope schema. Schema 1 files (v1-only, pre-cutover) load as an
#: empty store; schema 2 is the current form. Legacy v1 ``slides`` /
#: ``idless`` sections in either are ignored and dropped on the next save.
SCHEMA_VERSION = 2

#: Version of the v3 fingerprint functions (:func:`~clm.slides.doc_identity.content_fingerprint`
#: and friends). Bump when the hashing form changes; entries recorded under an
#: older version drop to cold at load (§5's lazy migration rule, #458).
LEDGER_HASH_VERSION = 1

#: Same committed location the v1 ledger established (issue #448 / #453).
LEDGER_SUBDIR = ".clm"
LEDGER_FILENAME = "sync-ledger.json"

_SIDES: tuple[Lang, Lang] = ("de", "en")


@frozen
class LedgerMember:
    """One member's recorded §5 entry: the engine view plus trust metadata.

    ``entry`` is the exact :class:`~clm.slides.doc_identity.MemberBaseline` the
    differ compares against — fingerprints per side, tags, owner-free
    signatures. ``provenance`` records *who* asserted the verification, kept so
    a later run can selectively distrust a source without nuking the ledger. It
    is free-form; what the engine writes today is ``record`` (the ``sync
    record`` default), ``agent`` / ``semantic:<model>`` (asked for through
    ``--provenance``), ``apply`` (the executor), and ``harvest:<fingerprint>``
    (``clm harvest``) — there is no ``accept`` stamp, despite the verb. On the
    **sync verbs**, whether a stamp overwrites an existing one is decided by the
    caller's intent and never by the value (:func:`preserve_unchanged_member`);
    harvest builds its entries directly and always overwrites, which is
    harmless because a harvest write is always a real content change.
    ``state`` is always ``verified`` today (an unverified member is simply
    absent).
    """

    entry: MemberBaseline
    provenance: str
    state: str = "verified"
    hash_version: int = LEDGER_HASH_VERSION
    #: The repo ``HEAD`` when this entry was last written **with a real change**
    #: (``git rev-parse HEAD`` at record time). Three things follow, and all three
    #: have bitten:
    #:
    #: 1. It does **not** contain the recorded state. ``record`` runs before you
    #:    commit, so the state it certifies is in the working tree, not in this
    #:    commit. Anything wanting to re-derive content from git — the
    #:    ``git cat-file`` idea in ``sync-consistency-ledger.md`` §11.3, designed
    #:    but never built — needs a commit that *contains* the state, which this
    #:    is not.
    #: 2. A re-record that changes nothing else leaves it alone
    #:    (:func:`preserve_unchanged_member`, issue #555), so it can name a commit
    #:    well in the past. That is deliberate: bumping it would make a repo-wide
    #:    ``record`` sweep dirty every ledger in the repo.
    #: 3. No verdict reads it — it is provenance for a human reading a ledger
    #:    diff, so a stale value is cosmetic, not a trust problem. It is *not*
    #:    unread, though: :func:`deck_section_fingerprint` digests the whole
    #:    canonical section, so a changed stamp changes the schema-4
    #:    ``report_id`` and invalidates decision documents written against the
    #:    old report. That is a second reason point 2 matters — churning this
    #:    field would churn report freshness with it.
    confirmed_commit: str | None = None


@define
class DeckLedger:
    """The recorded state of one deck bundle inside its topic ledger.

    Mirrors :class:`~clm.slides.doc_identity.DeckBaseline` (members + order
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
    """The whole per-topic file: the deck sections."""

    decks: dict[str, DeckLedger] = field(factory=dict)
    #: Canonical JSON of each deck section **as it was read from disk**, keyed by
    #: deck key. :func:`save` diffs the current sections against this to tell the
    #: sections *this* run changed from the ones it merely loaded, which is what
    #: makes a concurrent write to a sibling deck survive (M8). Empty for a ledger
    #: that was never loaded — then every section counts as this run's work.
    #: Excluded from equality and repr: it is provenance about the read, not part
    #: of the recorded state, and two ledgers with the same decks are the same
    #: ledger however each was obtained.
    load_snapshot: dict[str, str] = field(factory=dict, eq=False, repr=False)


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


def deck_section_fingerprint(ledger: TopicLedger, deck_key: str) -> str:
    """A stable digest of one deck's ledger section — the trust half of the
    schema-4 ``report_id`` (:mod:`clm.slides.sync_wire`).

    Hashes the section's *canonical* JSON, so it is exactly as sensitive as
    the committed artifact: any recorded fingerprint, order scope, owner ref
    or provenance stamp that moved changes the digest, and a re-save that
    changes no content does not. An absent section (never-recorded deck) is
    its own value — cold is a state a decision can be written against, and a
    document answering a cold report must not survive the deck being
    recorded out from under it.
    """
    deck = ledger.decks.get(deck_key)
    if deck is None:
        return "cold"
    payload = json.dumps(_deck_to_json(deck), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


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

    Accepts schema 1 (a pre-cutover v1-only file: empty store) and schema 2.
    Legacy v1 ``slides`` / ``idless`` sections are ignored — they disappear on
    the next save. Anything else is treated as empty — the deck cold-starts,
    never crashes and never trusts.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return TopicLedger()
    return _from_bytes(raw)


def _from_bytes(raw: bytes) -> TopicLedger:
    """Parse a ledger payload; anything unreadable degrades to empty (fail-safe cold)."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return TopicLedger()
    if not isinstance(data, dict) or data.get("schema") not in (1, SCHEMA_VERSION):
        return TopicLedger()
    ledger = TopicLedger()
    decks = data.get("decks", {})
    if isinstance(decks, dict):
        for deck_key, rec in decks.items():
            if isinstance(rec, dict):
                ledger.decks[deck_key] = _deck_from_json(rec)
    ledger.load_snapshot = _section_json(ledger)
    return ledger


def _section_json(ledger: TopicLedger) -> dict[str, str]:
    """Canonical JSON per deck section — the unit of concurrent-change detection."""
    return {
        key: json.dumps(_deck_to_json(deck), sort_keys=True, ensure_ascii=False)
        for key, deck in ledger.decks.items()
    }


def _to_json(ledger: TopicLedger) -> str:
    payload: dict[str, object] = {
        "schema": SCHEMA_VERSION,
        "decks": {key: _deck_to_json(deck) for key, deck in sorted(ledger.decks.items())},
    }
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def prune_dangling_refs(deck_ledger: DeckLedger, deck_key: str = "?") -> int:
    """Drop internal references that resolve to no member entry (issue #718).

    The trust store must not carry claims it cannot back: a ``member_order``
    handle or an ``owner`` reference naming a nonexistent member key is
    silently ignored by every consumer (the differ intersects stale handles
    away), so a dangling reference is pure erosion — order trust evaporates
    with no signal. Pruning is behavior-neutral for the differ and makes the
    committed artifact honest. Dangling handles are removed from their scope
    lists; a dangling ``owner`` degrades to ``None`` (the differ then frames
    a mechanical ``record_owner`` against the current owner — self-healing —
    while the entry's fingerprint trust, which is not in doubt, is kept).

    Returns the number of pruned references; a nonzero count is logged, since
    post-#718 nothing should create them.
    """
    pruned = 0
    keys = set(deck_ledger.members)
    for scope_key, handles in list(deck_ledger.member_order.items()):
        kept = [h for h in handles if h in keys]
        if len(kept) != len(handles):
            pruned += len(handles) - len(kept)
            deck_ledger.member_order[scope_key] = kept
    for key, lm in list(deck_ledger.members.items()):
        owner = lm.entry.owner
        if owner is not None and owner not in keys:
            deck_ledger.members[key] = evolve(lm, entry=evolve(lm.entry, owner=None))
            pruned += 1
    if pruned:
        logging.getLogger(__name__).warning(
            "sync ledger deck %s: pruned %d dangling reference(s) "
            "(stale member_order handles / owner refs — see issue #718)",
            deck_key,
            pruned,
        )
    return pruned


def _merge_with_disk(ledger: TopicLedger, on_disk: TopicLedger) -> TopicLedger:
    """Three-way merge of deck sections: ours where we changed them, disk otherwise.

    A topic ledger is one file holding independent per-deck sections, and every
    verb reads the whole file, mutates one section, and writes the whole file back
    (M8). Two ``sync apply`` runs on *different* decks of the same topic — the
    normal shape of a parallel sweep — therefore raced: the second writer's
    in-memory copy of the first's deck was the pre-run one, so its save silently
    reverted the first's work. Neither run saw an error; the trust store just
    quietly lost an entry.

    The base for the merge is :attr:`TopicLedger.load_snapshot`, so "did we change
    this section?" is answered by comparison rather than by bookkeeping the verbs
    would have to remember to do:

    * we changed it → ours wins (last writer wins *per deck*, the right granularity);
    * we did not → keep whatever is on disk now, which may be a sibling run's newer
      work — this is the case that used to be lost;
    * on disk but not in memory at all → keep it (a section created after our load);
    * changed on **both** sides → ours wins, and it is logged: this is a genuine
      concurrent write to one deck, which only locking could order, and it should
      never happen for the per-deck sweeps this exists to support.

    Merging is not a lock. It shrinks the lost-update window from the whole verb
    (load → parse → diff → apply → save, seconds) to the gap between this re-read
    and ``os.replace`` (sub-millisecond), and it is portable, which file locking on
    Windows is not. The residual window is documented rather than papered over.

    Two consequences worth knowing:

    * A **corrupt, truncated, or newer-schema** file parses to an *empty* ledger
      (:func:`_from_bytes` is fail-safe by design). The ``key in on_disk.decks``
      clause is what stops "we did not change it, so keep disk" from meaning
      "keep nothing" there — without it, a run that modified nothing would wipe
      every section it held. That is why it is tested directly.
    * Whole-**section** deletion is not expressible: ``merged`` starts from the
      disk sections and the loop only visits sections we hold, so dropping a key
      from ``ledger.decks`` before saving leaves it on disk. No caller does this
      (a removed deck's section is simply stale, and a stale section is
      fail-safe — its members mismatch and re-check). Member removal *within* a
      section works normally, because a full record replaces the section.
    """
    base = ledger.load_snapshot
    ours = _section_json(ledger)
    disk = _section_json(on_disk)
    merged = TopicLedger(decks=dict(on_disk.decks))
    for key, deck in ledger.decks.items():
        if ours[key] == base.get(key) and key in on_disk.decks:
            continue  # untouched by this run — do not clobber a sibling's newer work
        if ours[key] != base.get(key) and key in disk and disk[key] != base.get(key):
            logging.getLogger(__name__).warning(
                "sync ledger deck %s: changed by this run AND by another writer since "
                "it was read — this run's version wins. Concurrent writes to one deck "
                "cannot be ordered without a lock; run per-deck sweeps instead.",
                key,
            )
        merged.decks[key] = deck
    return merged


def save(ledger: TopicLedger, path: Path) -> bool:
    """Write the ledger atomically (canonical JSON), creating ``.clm/``.

    Skips the write entirely — and returns ``False`` — when the canonical
    serialization is byte-identical to the file already on disk: a repo-wide
    ``record`` sweep must be write-free on clean pairs (issue #555).

    Deck sections another writer changed while this run was working are merged
    back in first (:func:`_merge_with_disk`) — a topic ledger holds independent
    per-deck sections, and whole-file load-mutate-save otherwise let concurrent
    sweeps of sibling decks silently revert each other (M8).

    Every deck section is swept for dangling internal references
    (:func:`prune_dangling_refs`) — ledgers damaged by the pre-#718
    ``record_group_rename`` heal on their next save.
    """
    from clm.infrastructure.utils.path_utils import atomic_write_bytes

    # Re-read as late as possible: everything between the verb's load and this
    # point is time in which a sibling run may have committed its own section.
    current: bytes | None = None
    if path.is_file():
        try:
            current = path.read_bytes()
        except OSError as exc:
            # The merge is skipped, so this write clobbers whatever is there —
            # the exact M8 shape this function exists to prevent. It stays
            # best-effort (a ledger write must not fail a verb) but never silent:
            # this repo has a documented history of Windows AV/indexer read races.
            logging.getLogger(__name__).warning(
                "sync ledger %s could not be re-read before saving (%s); writing "
                "without merging — a concurrent run's changes to this file may be lost",
                path,
                exc,
            )
    if current is not None:
        merged = _merge_with_disk(ledger, _from_bytes(current))
    else:
        merged = ledger

    # After the merge, so #718's healing covers every section we are about to
    # write — including one carried over from disk — rather than only the
    # sections this run happened to load. Pruning is idempotent and
    # behavior-neutral, so healing a sibling's section cannot corrupt it.
    for deck_key, deck_ledger in merged.decks.items():
        prune_dangling_refs(deck_ledger, deck_key)

    payload = _to_json(merged).encode("utf-8")
    wrote = current != payload
    if wrote:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(path, payload)

    # The file now matches ``merged``, so re-base "did this run change it?" — a
    # second save must compare against what is on disk now, not against the
    # original load. Without this, a section edited and then reverted reads as
    # untouched, the revert is dropped in favour of this run's own earlier write,
    # and the caller is told nothing changed.
    #
    # The base is ``ledger``, NOT ``merged``. For a section this run did not
    # change, ``merged`` holds the *disk* copy — a sibling's newer work — while
    # ``ledger`` still holds ours. Basing on ``merged`` would make our stale copy
    # read as a change on the next save and write it back over the sibling, and
    # the both-changed warning would not fire (disk would equal the base). That is
    # M8 again, one save later.
    ledger.load_snapshot = _section_json(ledger)
    return wrote


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


def preserve_unchanged_member(
    prev: LedgerMember | None,
    new: LedgerMember,
    *,
    deliberate_provenance: bool = False,
) -> LedgerMember:
    """Keep ``prev`` when re-recording it would change nothing that means anything.

    Two fields are excluded from "would change":

    ``confirmed_commit`` — an entry whose recorded state is otherwise identical
    keeps its original stamp, so a repo-wide ``record`` sweep of clean pairs
    rewrites nothing (issue #555). See :class:`LedgerMember` for what the field
    means.

    ``provenance``, unless ``deliberate_provenance`` — the verbs stamp a
    provenance on every write (``record`` from the CLI default, ``apply`` from
    the executor, ``harvest:<fp>`` from harvest), and the normal loop alternates
    them, so comparing the stamp made every touched member rewrite on every pass
    with nothing about it changed. That is M13: 883-line ledger diffs for 60
    changed cells, enough noise to make the trust store unreviewable.

    **Intent comes from the caller, never from the string.** An earlier draft
    enumerated "automatic" provenance values, which was wrong twice over: it
    asserted a value the engine does not yet write, and — because ``record`` is
    both ``--provenance``'s default *and* a value a human can type — it silently
    swallowed a deliberate re-verification. ``clm slides sync record`` on a
    member stamped ``semantic:<model>`` reported ``recorded`` while discarding
    the reset, defeating the field's stated purpose (distrusting one source
    later). The verb knows whether the user asked; the ledger cannot infer it.

    So: an automatic stamp never overwrites what is already recorded, in either
    direction — a later ``apply`` does not demote an ``agent`` entry, because
    with the content identical there is nothing to re-establish. A deliberate
    stamp always records fresh, including ``--provenance record`` typed by hand
    to reset a stale semantic attribution.

    Any real field change — content fingerprints, trust state, a hash-version
    bump — still records fresh regardless.
    """
    if prev is None:
        return new
    candidate = evolve(new, confirmed_commit=prev.confirmed_commit)
    if not deliberate_provenance:
        candidate = evolve(candidate, provenance=prev.provenance)
    return prev if candidate == prev else new


def record_deck_snapshot(
    ledger: TopicLedger,
    deck_key: str,
    deck: BilingualDeck,
    *,
    provenance: str,
    commit: str | None = None,
    member_keys: set[str] | None = None,
    deliberate_provenance: bool = False,
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
        if old is not None:
            fresh.members = {
                key: preserve_unchanged_member(
                    old.members.get(key), lm, deliberate_provenance=deliberate_provenance
                )
                for key, lm in fresh.members.items()
            }
        ledger.decks[deck_key] = fresh
        return len(fresh.members), migrations
    target = old if old is not None else DeckLedger()
    recorded = 0
    for key in sorted(member_keys):
        if key.startswith("pos:"):
            group, kind, _ordinal = key.split(":", 1)[1].rsplit("/", 2)
            recorded += rerecord_pool(
                target, fresh, group, kind, deliberate_provenance=deliberate_provenance
            )
            continue
        lm = fresh.members.get(key)
        if lm is None:
            continue
        for old_key, new_key in migrations.items():
            if new_key == key:
                target.members.pop(old_key, None)
        target.members[key] = preserve_unchanged_member(
            target.members.get(key), lm, deliberate_provenance=deliberate_provenance
        )
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


def rerecord_pool(
    target: DeckLedger,
    fresh: DeckLedger,
    group: str,
    kind: str,
    *,
    deliberate_provenance: bool = False,
) -> int:
    """Replace every ``pos:<group>/<kind>/*`` entry with the fresh pool state."""
    prefix = f"pos:{group}/{kind}/"
    old_pool = {
        key: target.members.pop(key) for key in [k for k in target.members if k.startswith(prefix)]
    }
    copied = 0
    for key, lm in fresh.members.items():
        if key.startswith(prefix):
            target.members[key] = preserve_unchanged_member(
                old_pool.get(key), lm, deliberate_provenance=deliberate_provenance
            )
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


def seed_order_scopes(target: DeckLedger, fresh: DeckLedger) -> int:
    """Adopt order trust from a fully-resolved pass, where the sides agree.

    Issue #654 (adversarial-review C3): order scopes were seeded only by a
    full ``record``, ``split``, or ``translate-bootstrap`` — the verb loop
    (report → confirm → apply) never seeded them, so a confirm-seeded deck
    stayed permanently order-blind while presenting as verified. An apply
    pass that ends with **zero unresolved items** may bank order trust for
    every scope whose two sides currently agree — compared over the handles
    both sides carry (a one-sided member has no cross-side order to
    disagree about). A scope whose sides disagree is left untouched: order
    trust is seeded from agreement, never blessed over a divergence.

    Returns the number of scopes adopted.
    """
    seeded = 0
    de_groups = fresh.group_order_by_side.get("de", [])
    en_groups = fresh.group_order_by_side.get("en", [])
    common_groups = set(de_groups) & set(en_groups)
    if [g for g in de_groups if g in common_groups] == [g for g in en_groups if g in common_groups]:
        record_group_order(target, fresh)
        seeded += 1
    for group, part in sorted({(g, p) for (_lang, g, p) in fresh.member_order}):
        de_seq = fresh.member_order.get(("de", group, part), [])
        en_seq = fresh.member_order.get(("en", group, part), [])
        common = set(de_seq) & set(en_seq)
        if [h for h in de_seq if h in common] == [h for h in en_seq if h in common]:
            record_order_scope(target, fresh, group, part)
            seeded += 1
    return seeded


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
    scopes **and the ``id:`` handle values inside their lists**, the
    group-order lists, and ``owner`` references (every member of a group —
    not only companions — carries ``owner = the anchor's key``). The
    anchor's own ``id:`` entry is re-keyed by the caller through the member
    migration path. Issue #718: the owner/handle rewrites were originally
    left to :func:`clm.slides.rename_id.migrate_ledger_key`, so the apply
    executor's ``record_group_rename`` (which called this function bare)
    committed ledgers with dangling ``id:<old>`` references.
    """
    old_key = f"id:{old_group}"
    new_key_id = f"id:{new_group}"
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
            owner=new_key_id if lm.entry.owner == old_key else lm.entry.owner,
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
    # Owner references outside the group's pos: keys (id'd members owned by
    # the renamed anchor — companions, subslides).
    for key, lm in list(target.members.items()):
        if lm.entry.owner == old_key:
            target.members[key] = evolve(lm, entry=evolve(lm.entry, owner=new_key_id))
    for lang, group, part in [k for k in target.member_order if k[1] == old_group]:
        target.member_order[(lang, new_group, part)] = target.member_order.pop((lang, group, part))
    # Handle VALUES inside every member-order list (the anchor's own handle
    # appears in its group's scopes; a moved member's in others).
    for scope_key, handles in list(target.member_order.items()):
        if old_key in handles:
            target.member_order[scope_key] = [new_key_id if h == old_key else h for h in handles]
    target.group_order = [new_group if g == old_group else g for g in target.group_order]
    target.group_order_by_side = {
        lang: [new_group if g == old_group else g for g in order]
        for lang, order in target.group_order_by_side.items()
    }
