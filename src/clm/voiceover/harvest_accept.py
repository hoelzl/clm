"""``clm harvest accept`` — validate an answer and write it (#546 Phase 3/4).

The only write path of the harvest toolkit. An answer (the bullet-list
document a ``harvest task`` framed, validator ``harvest-bullets``) is
validated — schema shape, single-cell body guards, per-member
baseline-fingerprint freshness against what ``task`` framed — and then
lands through the v3 model: id-keyed member edits emitted and written
atomically via the Phase-1 write surface (:mod:`clm.slides.doc_write`).
Nothing is written on any validation failure; the mutated bundle is
re-parsed before anything touches disk (the lens gate).

Slides routinely carry several narrative cells (one per code cell), so an
answer is a list of per-member ``updates``: each names an existing
narrative member of the slide, or creates a new one (``"member": null``,
optionally placed ``"after"`` an existing narrative member; default at the
end of the slide group).

``--record`` banks each written member into the sync consistency ledger
with provenance ``harvest:<video-fingerprint>`` under the §6
one-sided-trust semantics (proposal §6, the load-bearing invariant):

* **bilingual update** → the member's fresh both-side snapshot is recorded
  (the pair is clean; next ``slides sync report`` reads ``in_sync``);
* **one-sided update, member ends one-sided** → the fresh one-sided
  snapshot is recorded — that entry is precisely what makes the next sync
  report frame ``translate_new`` for the twin (an unrecorded new member
  would only surface as an unframed ``verify_cold``);
* **one-sided update, twin body exists** → the written side's ledger
  fingerprint is **not** advanced (the existing entry is kept, or the
  pre-write state is recorded): the harvested side reads as "edited off
  base" and the next sync report frames ``translate_edit`` toward the twin.
  Advancing both fingerprints would read ``in_sync`` and silently bless the
  stale twin — the §6 forbidden state.

The ledger save is additionally gated on the structural verify
(:func:`clm.slides.sync_verify.structural_gate`), mirroring the sync verbs.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from attrs import define, evolve, field

from clm.slides.bilingual_doc import (
    BilingualDeck,
    Lang,
    Member,
    MemberKey,
    Part,
    SideCell,
)
from clm.slides.doc_identity import (
    body_fingerprint,
    content_fingerprint,
    pair_signature,
)
from clm.slides.doc_lenses import LoadedBundle, parse_bundle
from clm.slides.doc_write import DeckEmitter, write_changed_files
from clm.slides.raw_cells import is_cell_boundary

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "AcceptOutcome",
    "AcceptRejected",
    "Answer",
    "Update",
    "accept_answer",
    "parse_answer",
]

_SIDES: tuple[Lang, Lang] = ("de", "en")
_NARRATIVE_ROLES = ("voiceover", "notes")
_BULLET_PREFIX = re.compile(r"^-\s+")


class AcceptRejected(Exception):
    """The answer was rejected; the message names the reason. Nothing was written."""


@define
class Update:
    """One per-member edit: an existing member's new bullets, or a new cell."""

    member: str | None  # id:… handle, or None = create
    bullets: dict[str, list[str]]  # side -> ordered bullet strings
    after: str | None = None  # create only: place after this narrative member


@define
class Answer:
    """The validated bullet-list answer document."""

    item: str
    kind: str
    baseline_fingerprints: dict[str, dict[str, str | None]]
    updates: list[Update]
    dropped: list[str]
    video_fingerprint: str | None


@define
class AcceptOutcome:
    item: str
    applied: bool = False
    members: list[dict[str, Any]] = field(factory=list)  # {"member","created"}
    written_paths: list[Path] = field(factory=list)
    recorded: bool = False
    record_refused: list[str] = field(factory=list)
    dry_run: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "tool": "harvest",
            "verb": "accept",
            "item": self.item,
            "applied": self.applied,
            "members": self.members,
            "written": [str(p) for p in self.written_paths],
            "recorded": self.recorded,
            "record_refused": self.record_refused,
            "dry_run": self.dry_run,
        }


# ---------------------------------------------------------------------------
# Answer validation (validator "harvest-bullets")
# ---------------------------------------------------------------------------


def _parse_bullets(bullets: Any, where: str) -> dict[str, list[str]]:
    if not isinstance(bullets, dict) or not bullets or set(bullets) - {"de", "en"}:
        raise AcceptRejected(f"{where}.bullets must map at least one of de/en to a bullet list")
    parsed: dict[str, list[str]] = {}
    for side, entries in bullets.items():
        if not isinstance(entries, list) or not entries:
            raise AcceptRejected(f"{where}.bullets.{side} must be a non-empty list of strings")
        cleaned: list[str] = []
        for i, bullet in enumerate(entries):
            if not isinstance(bullet, str) or not bullet.strip():
                raise AcceptRejected(f"{where}.bullets.{side}[{i}] must be a non-empty string")
            if "\n" in bullet:
                raise AcceptRejected(
                    f"{where}.bullets.{side}[{i}] contains a newline — one bullet per "
                    "string, markdown inline formatting only"
                )
            cleaned.append(_BULLET_PREFIX.sub("", bullet.strip()))
        parsed[side] = cleaned
    return parsed


def parse_answer(payload: Any) -> Answer:
    """Validate the answer document shape; raise :class:`AcceptRejected` with
    a precise reason on the first violation."""
    if not isinstance(payload, dict):
        raise AcceptRejected("the answer must be a JSON object (see the task's answer_schema)")
    item = payload.get("item")
    if not isinstance(item, str) or not item.startswith("id:"):
        raise AcceptRejected("'item' must be the slide handle string, e.g. \"id:intro\"")
    kind = payload.get("kind")
    if kind not in ("curate", "translate"):
        raise AcceptRejected('\'kind\' must be "curate" or "translate"')
    tokens = payload.get("baseline_fingerprints")
    if not isinstance(tokens, dict):
        raise AcceptRejected(
            "'baseline_fingerprints' must be the per-member object echoed from the task"
        )
    fingerprints: dict[str, dict[str, str | None]] = {}
    for key, sides in tokens.items():
        if not isinstance(sides, dict) or set(sides) - {"de", "en"}:
            raise AcceptRejected(f"baseline_fingerprints[{key!r}] must be a {{de, en}} object")
        for side, value in sides.items():
            if value is not None and not isinstance(value, str):
                raise AcceptRejected(
                    f"baseline_fingerprints[{key!r}].{side} must be a string or null"
                )
        fingerprints[key] = {s: sides.get(s) for s in _SIDES}
    raw_updates = payload.get("updates")
    if not isinstance(raw_updates, list) or not raw_updates:
        raise AcceptRejected("'updates' must be a non-empty list of per-member entries")
    updates: list[Update] = []
    seen_members: set[str] = set()
    for i, entry in enumerate(raw_updates):
        where = f"updates[{i}]"
        if not isinstance(entry, dict):
            raise AcceptRejected(f"{where} must be an object with 'member' and 'bullets'")
        member = entry.get("member")
        if member is not None and (not isinstance(member, str) or not member.startswith("id:")):
            raise AcceptRejected(f"{where}.member must be an id:… handle or null (= create)")
        if member is not None:
            if member in seen_members:
                raise AcceptRejected(f"{where}: duplicate update for member {member}")
            seen_members.add(member)
        after = entry.get("after")
        if after is not None:
            if member is not None:
                raise AcceptRejected(f"{where}: 'after' is only valid when member is null")
            if not isinstance(after, str) or not after.startswith("id:"):
                raise AcceptRejected(f"{where}.after must be an existing member's id:… handle")
        updates.append(
            Update(member=member, bullets=_parse_bullets(entry.get("bullets"), where), after=after)
        )
    dropped = payload.get("dropped")
    if not isinstance(dropped, list) or any(not isinstance(d, str) for d in dropped):
        raise AcceptRejected("'dropped' must be a list of strings (the audit trail; may be empty)")
    video_fingerprint = payload.get("video_fingerprint")
    if video_fingerprint is not None and not isinstance(video_fingerprint, str):
        raise AcceptRejected("'video_fingerprint' must be a string when present")
    return Answer(
        item=item,
        kind=kind,
        baseline_fingerprints=fingerprints,
        updates=updates,
        dropped=list(dropped),
        video_fingerprint=video_fingerprint,
    )


def _render_body(bullets: list[str], comment_token: str) -> str:
    """Bullets → the cell body (the deck's `#\\n# - …` narrative style)."""
    lines = [comment_token]
    lines.extend(f"{comment_token} - {b}" for b in bullets)
    return "\n".join(lines)


def _guard_body(body: str, comment_token: str) -> None:
    for line in body.split("\n"):
        if is_cell_boundary(line, comment_token):
            raise AcceptRejected(
                f"a rendered bullet line would form a '{comment_token} %%' cell "
                "boundary and re-split on read-back — remove the delimiter from the bullet"
            )


def _replace_body(cell: SideCell, body: str) -> tuple[str, ...]:
    """The cell's lines with a new body, preserving its trailing separator
    (the :mod:`clm.slides.doc_apply` rule)."""
    old_body = cell.lines[1:]
    trailing = 0
    for line in reversed(old_body):
        if line == "":
            trailing += 1
        else:
            break
    new_body = body.split("\n")
    while new_body and new_body[-1] == "":
        new_body.pop()
    return (cell.lines[0], *new_body, *([""] * trailing))


# ---------------------------------------------------------------------------
# Locating and mutating the voiceover members
# ---------------------------------------------------------------------------


def _narrative_members(deck: BilingualDeck, slide_id: str) -> list[Member] | None:
    for group in deck.groups:
        if group.anchor_id == slide_id:
            return [m for m in group.members if m.role in _NARRATIVE_ROLES]
    return None


def _check_freshness(answer: Answer, members: list[Member]) -> None:
    """The echoed per-member tokens must match the LIVE deck exactly —
    including the member set itself (a narrative cell added or removed
    since the task was framed is staleness, not a merge opportunity)."""
    current: dict[str, dict[str, str | None]] = {}
    for member in members:
        current[member.key.render()] = {
            side: content_fingerprint(cell) if (cell := member.side(side)) is not None else None
            for side in _SIDES
        }
    if current != answer.baseline_fingerprints:
        raise AcceptRejected(
            "the slide's narrative cells changed since the task was framed "
            "(baseline_fingerprints mismatch) — re-run `harvest task` and re-judge"
        )


def _deck_slide_ids(deck: BilingualDeck) -> set[str]:
    ids: set[str] = set()
    for member in deck.members():
        for side in _SIDES:
            cell = member.side(side)
            if cell is not None and cell.slide_id is not None:
                ids.add(cell.slide_id.rstrip("!"))
    return ids


def _mint_vo_id(taken: set[str], owner: str) -> str:
    candidate = f"{owner}-vo"
    n = 2
    while candidate in taken:
        candidate = f"{owner}-vo{n}"
        n += 1
    taken.add(candidate)
    return candidate


def _narrative_conventions(deck: BilingualDeck) -> tuple[str, str]:
    """(role, layout) for a new vo member: follow the deck's majority
    convention; default ("voiceover", "companion")."""
    roles: dict[str, int] = {}
    layouts: dict[str, int] = {}
    for member in deck.members():
        if member.role in _NARRATIVE_ROLES:
            roles[member.role] = roles.get(member.role, 0) + 1
            layouts[member.layout] = layouts.get(member.layout, 0) + 1
    role = max(roles, key=roles.__getitem__) if roles else "voiceover"
    layout = max(layouts, key=layouts.__getitem__) if layouts else "companion"
    return role, layout


def _new_side_cell(
    *,
    side: Lang,
    body: str,
    role: str,
    part: Part,
    vo_id: str,
    owner: str,
    comment_token: str,
) -> SideCell:
    header = (
        f'{comment_token} %% [markdown] lang="{side}" tags=["{role}"] '
        f'slide_id="{vo_id}" for_slide="{owner}"'
    )
    lines = (header, *body.split("\n"), "")
    return SideCell(
        lines=lines,
        index=0,  # streams are already built; emission follows stream order
        line_number=0,
        part=part,
        lang_attr=side,
        tags=(role,),
        slide_id=vo_id,
        for_slide=owner,
        vo_anchor=None,
        cell_type="markdown",
    )


def _insert_new_member(
    emitter: DeckEmitter,
    deck: BilingualDeck,
    member: Member,
    owner: str,
    part: Part,
    after: Member | None,
) -> None:
    """Place the new member's cells per side: right after ``after`` when it
    is present in that side's stream; otherwise after the owner group's last
    cell (deck part) / appended (companion part)."""
    for side in _SIDES:
        cell = member.side(side)
        if cell is None:
            continue
        stream = emitter.streams.setdefault((side, part), [])
        insert_at: int | None = None
        if after is not None:
            for i, m in enumerate(stream):
                if m is after:
                    insert_at = i + 1
                    break
        if insert_at is None:
            if part == "companion":
                insert_at = len(stream)
            else:
                insert_at = 0
                for group in deck.groups:
                    if group.anchor_id != owner:
                        continue
                    group_members = [
                        m
                        for m in group.all_members()
                        if (c := m.side(side)) is not None and c.part == part
                    ]
                    positions = [
                        i for i, m in enumerate(stream) if any(m is gm for gm in group_members)
                    ]
                    insert_at = (max(positions) + 1) if positions else 0
        stream.insert(insert_at, member)
        emitter.mutated = True


# ---------------------------------------------------------------------------
# The §6 ledger record
# ---------------------------------------------------------------------------


def _entry_from_member(member: Member):
    """One member's :class:`~clm.slides.doc_identity.MemberBaseline` snapshot
    (the per-member core of ``baseline_from_deck``)."""
    from clm.slides.doc_identity import MemberBaseline

    return MemberBaseline(
        key=member.key.render(),
        langness=member.langness,
        layout=member.layout,
        kind=member.kind,
        role=member.role,
        owner=member.owner.render() if member.owner else None,
        de_fp=content_fingerprint(member.de) if member.de else None,
        en_fp=content_fingerprint(member.en) if member.en else None,
        de_body_fp=body_fingerprint(member.de) if member.de else None,
        en_body_fp=body_fingerprint(member.en) if member.en else None,
        de_tags=member.de.tags if member.de else None,
        en_tags=member.en.tags if member.en else None,
        de_sig=pair_signature(member.de) if member.de else None,
        en_sig=pair_signature(member.en) if member.en else None,
    )


@define
class _WrittenMember:
    """What the record step needs about one landed update."""

    key: str
    final_member: Member
    pre_member: Member | None  # None = created
    bilingual: bool


def _record_members(
    bundle: LoadedBundle,
    written: list[_WrittenMember],
    *,
    video_fingerprint: str,
) -> list[str]:
    """Bank the writes under ``harvest:<fp>`` provenance (§6 semantics).

    Returns structural-gate violation messages; non-empty means the ledger
    save was withheld (the files stay as written — fail-safe, like apply).
    """
    from clm.slides import doc_ledger
    from clm.slides.sync_verify import structural_gate

    violations = structural_gate(
        bundle.de_path.read_text(encoding="utf-8"),
        bundle.en_path.read_text(encoding="utf-8"),
        bundle.comment_token,
    )
    if violations:
        return [v.message for v in violations]

    ledger_path = doc_ledger.ledger_path_for(bundle.de_path)
    ledger = doc_ledger.load(ledger_path)
    target = ledger.decks.setdefault(
        doc_ledger.deck_key_for(bundle.de_path), doc_ledger.DeckLedger()
    )
    provenance = f"harvest:{video_fingerprint}"

    for item in written:
        if item.bilingual or item.final_member.is_one_sided:
            # Fresh snapshot: a bilingual update records a clean pair
            # (in_sync next report); a one-sided member records the
            # one-sided entry that frames translate_new for the twin.
            entry = _entry_from_member(item.final_member)
        else:
            # One side written while a twin body exists: never advance the
            # written side's fingerprint — the mismatch IS the
            # translate_edit framing; advancing it would silently bless the
            # stale twin.
            existing = target.members.get(item.key)
            if existing is not None:
                entry = existing.entry
            elif item.pre_member is not None:
                entry = _entry_from_member(item.pre_member)
            else:  # pragma: no cover - created members are one-sided or bilingual
                return ["cannot synthesize a pre-write baseline for the member"]
        target.members[item.key] = doc_ledger.LedgerMember(entry=entry, provenance=provenance)

    doc_ledger.save(ledger, ledger_path)
    return []


# ---------------------------------------------------------------------------
# accept
# ---------------------------------------------------------------------------


def accept_answer(
    bundle: LoadedBundle,
    answer: Answer,
    *,
    record: bool = False,
    dry_run: bool = False,
) -> AcceptOutcome:
    """Validate ``answer`` against the live bundle and land it atomically.

    Raises :class:`AcceptRejected` (nothing written) on any validation
    failure. With ``record``, banks every touched member into the sync
    ledger under ``harvest:<video-fingerprint>`` provenance (§6 semantics).
    """
    deck = bundle.outcome.deck
    if deck is None:
        raise AcceptRejected("the deck bundle is not normalized — run `clm slides normalize` first")
    if record and answer.video_fingerprint is None:
        raise AcceptRejected(
            "--record needs the answer's 'video_fingerprint' (echo it from the task document)"
        )
    slide_id = answer.item.split(":", 1)[1]
    members = _narrative_members(deck, slide_id)
    if members is None:
        raise AcceptRejected(f"no slide {answer.item} in the deck")
    by_key = {m.key.render(): m for m in members}
    for update in answer.updates:
        if update.member is not None and update.member not in by_key:
            raise AcceptRejected(
                f"{update.member} is not a narrative member of slide {answer.item} "
                f"(known: {', '.join(sorted(by_key)) or 'none'})"
            )
        if update.after is not None and update.after not in by_key:
            raise AcceptRejected(
                f"'after' target {update.after} is not a narrative member of slide {answer.item}"
            )
    _check_freshness(answer, members)

    comment_token = bundle.comment_token
    emitter = DeckEmitter(deck=deck)
    originals = emitter.emit_all()

    role, layout = _narrative_conventions(deck)
    part: Part = "companion" if layout == "companion" else "deck"
    taken = _deck_slide_ids(deck)
    written: list[_WrittenMember] = []
    outcome = AcceptOutcome(item=answer.item, dry_run=dry_run)

    for update in answer.updates:
        bodies: dict[Lang, str] = {
            side: _render_body(update.bullets[side], comment_token)
            for side in _SIDES
            if side in update.bullets
        }
        for body in bodies.values():
            _guard_body(body, comment_token)
        bilingual = len(bodies) == 2

        if update.member is not None:
            target_member = by_key[update.member]
            pre_member = _detached_copy(target_member)
            for side, body in bodies.items():
                cell = target_member.side(side)
                if cell is not None:
                    emitter.set_side(
                        target_member, side, evolve(cell, lines=_replace_body(cell, body))
                    )
                else:
                    source: Lang = "en" if side == "de" else "de"
                    source_cell = target_member.side(source)
                    if source_cell is None:  # pragma: no cover - members carry >=1 side
                        raise AcceptRejected(f"the member {update.member} carries no cells")
                    from clm.slides.sync_writeback import swap_lang

                    header = (
                        swap_lang(source_cell.header, side)
                        if source_cell.lang_attr
                        else source_cell.header
                    )
                    base = evolve(source_cell, lines=(header, *source_cell.lines[1:]))
                    new_cell = evolve(base, lines=_replace_body(base, body), lang_attr=side)
                    emitter.insert_mirrored(target_member, source, side, source_cell.part, new_cell)
            written.append(
                _WrittenMember(
                    key=target_member.key.render(),
                    final_member=target_member,  # re-resolved after the re-parse
                    pre_member=pre_member,
                    bilingual=bilingual,
                )
            )
            outcome.members.append({"member": target_member.key.render(), "created": False})
        else:
            vo_id = _mint_vo_id(taken, slide_id)
            member_key = MemberKey.for_id(vo_id)
            new_member = Member(
                key=member_key,
                kind="markdown",
                role=role,
                langness="localized",
                layout=layout,
                owner=MemberKey.for_id(slide_id),
                de=None,
                en=None,
            )
            for side, body in bodies.items():
                emitter.set_side(
                    new_member,
                    side,
                    _new_side_cell(
                        side=side,
                        body=body,
                        role=role,
                        part=part,
                        vo_id=vo_id,
                        owner=slide_id,
                        comment_token=comment_token,
                    ),
                )
            after = by_key[update.after] if update.after is not None else None
            _insert_new_member(emitter, deck, new_member, slide_id, part, after)
            written.append(
                _WrittenMember(
                    key=member_key.render(),
                    final_member=new_member,
                    pre_member=None,
                    bilingual=bilingual,
                )
            )
            outcome.members.append({"member": member_key.render(), "created": True})

    finals = emitter.emit_all()
    changed = {file_key for file_key in finals if finals[file_key] != originals[file_key]}
    if not changed:
        outcome.applied = True  # a byte-identical answer is a valid no-op
        return outcome

    parse = parse_bundle(
        finals[("de", "deck")] or "",
        finals[("en", "deck")] or "",
        finals[("de", "companion")],
        finals[("en", "companion")],
        comment_token=comment_token,
    )
    if parse.refusal is not None or parse.deck is None:
        reasons = (
            "; ".join(f"[{r.code}] {r.detail}" for r in parse.refusal.reasons)
            if parse.refusal
            else "no deck"
        )
        raise AcceptRejected(
            f"the mutated bundle failed the re-parse gate ({reasons}) — nothing was written"
        )
    final_by_key = {m.key.render(): m for m in parse.deck.members()}
    for item in written:
        final = final_by_key.get(item.key)
        if final is None:
            raise AcceptRejected(
                f"the written member {item.key} did not survive the re-parse "
                "(writer bug) — nothing was written"
            )
        item.final_member = final

    if dry_run:
        outcome.applied = True
        return outcome

    outcome.written_paths = write_changed_files(bundle, finals, changed)
    outcome.applied = True

    if record:
        assert answer.video_fingerprint is not None
        outcome.record_refused = _record_members(
            bundle, written, video_fingerprint=answer.video_fingerprint
        )
        outcome.recorded = not outcome.record_refused
    return outcome


def _detached_copy(member: Member) -> Member:
    """A detached snapshot of a member's pre-write state (cells are frozen,
    the member shell is mutable — copy the shell)."""
    return Member(
        key=member.key,
        kind=member.kind,
        role=member.role,
        langness=member.langness,
        layout=member.layout,
        owner=member.owner,
        de=member.de,
        en=member.en,
    )
