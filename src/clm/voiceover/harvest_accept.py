"""``clm harvest accept`` — validate an answer and write it (#546 Phase 3).

The only write path of the harvest toolkit. An answer (the bullet-list
document a ``harvest task`` framed, `validator "harvest-bullets"`) is
validated — schema shape, single-cell body guards, baseline-fingerprint
freshness against what ``task`` framed — and then lands through the v3
model: an id-keyed member edit emitted and written atomically via the
Phase-1 write surface (:mod:`clm.slides.doc_write`). Nothing is written on
any validation failure; the mutated bundle is re-parsed before anything
touches disk (the lens gate).

``--record`` banks the write into the sync consistency ledger with
provenance ``harvest:<video-fingerprint>`` under the §6 one-sided-trust
semantics (proposal §6, the load-bearing invariant):

* **bilingual answer** → the member's fresh both-side snapshot is recorded
  (the pair is clean; next ``slides sync report`` reads ``in_sync``);
* **one-sided answer, member ends one-sided** → the fresh one-sided
  snapshot is recorded — that entry is precisely what makes the next sync
  report frame ``translate_new`` for the twin (an unrecorded new member
  would only surface as an unframed ``verify_cold``);
* **one-sided answer, twin body exists** → the written side's ledger
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
    "accept_answer",
    "parse_answer",
]

_SIDES: tuple[Lang, Lang] = ("de", "en")
_NARRATIVE_ROLES = ("voiceover", "notes")
_BULLET_PREFIX = re.compile(r"^-\s+")


class AcceptRejected(Exception):
    """The answer was rejected; the message names the reason. Nothing was written."""


@define
class Answer:
    """The validated bullet-list answer document."""

    item: str
    kind: str
    baseline_fingerprint: dict[str, str | None]
    bullets: dict[str, list[str]]  # side -> ordered bullet strings
    dropped: list[str]
    video_fingerprint: str | None


@define
class AcceptOutcome:
    item: str
    applied: bool = False
    created: bool = False
    member: str | None = None  # the vo member's rendered key
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
            "created": self.created,
            "member": self.member,
            "written": [str(p) for p in self.written_paths],
            "recorded": self.recorded,
            "record_refused": self.record_refused,
            "dry_run": self.dry_run,
        }


# ---------------------------------------------------------------------------
# Answer validation (validator "harvest-bullets")
# ---------------------------------------------------------------------------


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
    fingerprint = payload.get("baseline_fingerprint")
    if not isinstance(fingerprint, dict) or set(fingerprint) - {"de", "en"}:
        raise AcceptRejected(
            "'baseline_fingerprint' must be the {de, en} object echoed from the task"
        )
    for side, value in fingerprint.items():
        if value is not None and not isinstance(value, str):
            raise AcceptRejected(f"baseline_fingerprint.{side} must be a string or null")
    bullets = payload.get("bullets")
    if not isinstance(bullets, dict) or not bullets or set(bullets) - {"de", "en"}:
        raise AcceptRejected("'bullets' must map at least one of de/en to a bullet list")
    parsed_bullets: dict[str, list[str]] = {}
    for side, entries in bullets.items():
        if not isinstance(entries, list) or not entries:
            raise AcceptRejected(f"bullets.{side} must be a non-empty list of bullet strings")
        cleaned: list[str] = []
        for i, bullet in enumerate(entries):
            if not isinstance(bullet, str) or not bullet.strip():
                raise AcceptRejected(f"bullets.{side}[{i}] must be a non-empty string")
            if "\n" in bullet:
                raise AcceptRejected(
                    f"bullets.{side}[{i}] contains a newline — one bullet per string, "
                    "markdown inline formatting only"
                )
            cleaned.append(_BULLET_PREFIX.sub("", bullet.strip()))
        parsed_bullets[side] = cleaned
    dropped = payload.get("dropped")
    if not isinstance(dropped, list) or any(not isinstance(d, str) for d in dropped):
        raise AcceptRejected("'dropped' must be a list of strings (the audit trail; may be empty)")
    video_fingerprint = payload.get("video_fingerprint")
    if video_fingerprint is not None and not isinstance(video_fingerprint, str):
        raise AcceptRejected("'video_fingerprint' must be a string when present")
    return Answer(
        item=item,
        kind=kind,
        baseline_fingerprint={s: fingerprint.get(s) for s in _SIDES},
        bullets=parsed_bullets,
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
# Locating and mutating the voiceover member
# ---------------------------------------------------------------------------


def _narrative_members(deck: BilingualDeck, slide_id: str) -> list[Member] | None:
    for group in deck.groups:
        if group.anchor_id == slide_id:
            return [m for m in group.members if m.role in _NARRATIVE_ROLES]
    return None


def _check_freshness(answer: Answer, members: list[Member]) -> None:
    for side in _SIDES:
        cells = [c for m in members if (c := m.side(side)) is not None]
        current = content_fingerprint(cells[0]) if cells else None
        framed = answer.baseline_fingerprint.get(side)
        if current != framed:
            raise AcceptRejected(
                f"the {side} voiceover baseline changed since the task was framed "
                "(fingerprint mismatch) — re-run `harvest task` and re-judge"
            )


def _deck_slide_ids(deck: BilingualDeck) -> set[str]:
    ids: set[str] = set()
    for member in deck.members():
        for side in _SIDES:
            cell = member.side(side)
            if cell is not None and cell.slide_id is not None:
                ids.add(cell.slide_id.rstrip("!"))
    return ids


def _mint_vo_id(deck: BilingualDeck, owner: str) -> str:
    taken = _deck_slide_ids(deck)
    candidate = f"{owner}-vo"
    n = 2
    while candidate in taken:
        candidate = f"{owner}-vo{n}"
        n += 1
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
) -> None:
    """Place the new member's cells: companion cells append to the companion
    stream; inline cells go right after the owner group's last cell."""
    for side in _SIDES:
        cell = member.side(side)
        if cell is None:
            continue
        stream = emitter.streams.setdefault((side, part), [])
        if part == "companion":
            stream.append(member)
        else:
            anchor_pos = -1
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
                anchor_pos = max(positions) if positions else -1
            stream.insert(anchor_pos + 1, member)
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


def _record_member(
    bundle: LoadedBundle,
    *,
    key: str,
    final_member: Member,
    pre_member: Member | None,
    bilingual: bool,
    video_fingerprint: str,
) -> list[str]:
    """Bank the write under ``harvest:<fp>`` provenance (§6 semantics).

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

    if bilingual or final_member.is_one_sided:
        # Fresh snapshot: a bilingual answer records a clean pair (in_sync
        # next report); a one-sided member records the one-sided entry that
        # frames translate_new for the twin.
        entry = _entry_from_member(final_member)
    else:
        # One side written while a twin body exists: never advance the
        # written side's fingerprint — the mismatch IS the translate_edit
        # framing; advancing it would silently bless the stale twin.
        existing = target.members.get(key)
        if existing is not None:
            entry = existing.entry
        elif pre_member is not None:
            entry = _entry_from_member(pre_member)
        else:  # pragma: no cover - created members are one-sided or bilingual
            return ["cannot synthesize a pre-write baseline for the member"]

    target.members[key] = doc_ledger.LedgerMember(entry=entry, provenance=provenance)
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
    failure. With ``record``, banks the touched member into the sync ledger
    under ``harvest:<video-fingerprint>`` provenance (§6 semantics).
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
    for side in _SIDES:
        if side in answer.bullets and len([m for m in members if m.side(side) is not None]) > 1:
            raise AcceptRejected(
                f"slide {answer.item} has more than one narrative cell on the {side} "
                "side — edit the files directly, then re-run report"
            )
    _check_freshness(answer, members)

    comment_token = bundle.comment_token
    bodies: dict[Lang, str] = {
        side: _render_body(answer.bullets[side], comment_token)
        for side in _SIDES
        if side in answer.bullets
    }
    for body in bodies.values():
        _guard_body(body, comment_token)

    emitter = DeckEmitter(deck=deck)
    originals = emitter.emit_all()

    target_member = members[0] if members else None
    pre_member = None
    created = False
    if target_member is not None:
        pre_member = evolve_copy(target_member)
        for side, body in bodies.items():
            cell = target_member.side(side)
            if cell is not None:
                emitter.set_side(target_member, side, evolve(cell, lines=_replace_body(cell, body)))
            else:
                source: Lang = "en" if side == "de" else "de"
                source_cell = target_member.side(source)
                if source_cell is None:  # pragma: no cover - members carry >=1 side
                    raise AcceptRejected(f"the member for {answer.item} carries no cells")
                from clm.slides.sync_writeback import swap_lang

                header = (
                    swap_lang(source_cell.header, side)
                    if source_cell.lang_attr
                    else source_cell.header
                )
                base = evolve(source_cell, lines=(header, *source_cell.lines[1:]))
                new_cell = evolve(base, lines=_replace_body(base, body), lang_attr=side)
                emitter.insert_mirrored(target_member, source, side, source_cell.part, new_cell)
        member_key = target_member.key
    else:
        created = True
        role, layout = _narrative_conventions(deck)
        part: Part = "companion" if layout == "companion" else "deck"
        vo_id = _mint_vo_id(deck, slide_id)
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
        _insert_new_member(emitter, deck, new_member, slide_id, part)

    finals = emitter.emit_all()
    changed = {file_key for file_key in finals if finals[file_key] != originals[file_key]}
    outcome = AcceptOutcome(
        item=answer.item, created=created, member=member_key.render(), dry_run=dry_run
    )
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
    final_member = next(
        (m for m in parse.deck.members() if m.key.render() == member_key.render()), None
    )
    if final_member is None:
        raise AcceptRejected(
            "the written member did not survive the re-parse (writer bug) — nothing was written"
        )

    if dry_run:
        outcome.applied = True
        return outcome

    outcome.written_paths = write_changed_files(bundle, finals, changed)
    outcome.applied = True

    if record:
        assert answer.video_fingerprint is not None
        outcome.record_refused = _record_member(
            bundle,
            key=member_key.render(),
            final_member=final_member,
            pre_member=pre_member,
            bilingual=len(answer.bullets) == 2,
            video_fingerprint=answer.video_fingerprint,
        )
        outcome.recorded = not outcome.record_refused
    return outcome


def evolve_copy(member: Member) -> Member:
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
