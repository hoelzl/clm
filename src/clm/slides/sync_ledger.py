"""The per-slide sync-consistency ledger — a committed trust overlay (issue #448, P1).

Design note: ``docs/claude/design/sync-consistency-ledger.md`` (§3 append-only trust,
§4 the model, §11.2 trust-overlay reframe, §11.4 confirm paths).

**What it is.** A committed, per-topic JSON sidecar recording, per
``(slide_id, role)``, the reflow-insensitive hash of *each half* at the moment the
pair was confirmed in sync, plus the commit and the oracle that confirmed it. A
slide is trusted-in-sync **only from its first recorded confirmation forward**
(append-only trust): there is no commit in history we may assume was in sync (the
single-language era's translations were hand-maintained), so trust is *recorded*,
never *inferred*.

**What it does (P1 MVP).** It is a *trust overlay*, not a baseline replacement
(§11.2): the position-based classifier still runs against its single-ref bundle
(watermark / git HEAD / ``--baseline``); the ledger then **suppresses
re-litigation** of any slide whose two current halves are byte-identical to a
recorded confirmation. So pointing ``sync`` at an old baseline no longer
re-proposes an ``edit`` for a slide that was reconciled and recorded last round —
its current ``(de_hash, en_hash)`` matches the ledger, so it is skipped. A slide
that *did* drift since its confirmation does not match, and falls through to the
bundle unchanged (the lag-behind-HEAD property is preserved). A slide with **no**
entry is the cold path — checked, never assumed.

**Scope (P1).** Covers **localized id'd** cells — ``(slide_id, role)`` pairs whose
two halves are *translations* (the cells the engine reconciles and that need trust)
— **and id-less localized narratives** (voiceover / notes without a ``slide_id``),
keyed by ``(owning_slide_id, role, occ)`` in a separate ``idless`` store (§11.2): the
classifier's own narrative identity (:func:`~clm.slides.sync_plan._index_narratives_by_anchor`),
so a recorded entry lines up with the narrative proposal the overlay suppresses.
This reaches the #364/#365 residue the bare ``(slide_id, role)`` key cannot.
Language-neutral cells are byte-identical across halves and governed structurally
(``verify``), so they need no translation-trust and are not recorded; id-less
localized **code** cells stay with the structural-pass direction mechanism (#269),
not the ledger overlay.

**Write gate (§4.3).** Every write is gated on a whole-deck structural ``verify``
(:func:`~clm.slides.sync_verify.structural_gate`): a structurally corrupt pair is
never recorded as in-sync, so the worst a bad write can do is record a
structurally-sound-but-semantically-wrong pairing — which ``confirmed_oracle``
lets a later run find and re-check.

**Storage.** ``<topic>/.clm/sync-ledger.json`` — committed (issue #453 narrows the
``.clm/`` gitignore so cassettes and this ledger are tracked), excluded from the
course file map and student output (``.clm`` ∈ ``SKIP_DIRS_FOR_COURSE``). Canonical
sorted JSON keeps a per-topic merge local and line-mergeable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from clm.infrastructure.llm.cache import WATERMARK_HASH_VERSION
from clm.notebooks.slide_parser import Cell, comment_token_for_path, parse_cells
from clm.slides.sync_writeback import construct_of, hash_cell, role_of

if TYPE_CHECKING:
    from clm.infrastructure.llm.cache import SyncWatermarkCache

    # Type-only: the injected semantic oracle (the concrete client lives in
    # ``sync_semantic`` and is constructed only by the model-bearing establish command,
    # so this import is never executed at runtime — the ledger module stays model-free).
    from clm.slides.sync_semantic import SemanticJudge

SCHEMA_VERSION = 1
#: Schemas this module can read. Schema 2 is the Phase-3 coexistence envelope
#: (#520): the same file additionally carries the v3 ``decks`` section, which
#: this module preserves verbatim via ``SyncLedger.extra`` — the v1 sections
#: keep their schema-1 shape inside it. Written by ``clm.slides.doc_ledger``.
_READABLE_SCHEMAS = (1, 2)
#: The committed ledger lives under the build-internal ``.clm/`` tree (issue #453),
#: alongside ``cassettes/`` — both are committed build inputs, neither is student output.
LEDGER_SUBDIR = ".clm"
LEDGER_FILENAME = "sync-ledger.json"

#: Provenance of a recorded confirmation (``confirmed_oracle``). ``structural`` =
#: passed the deterministic structural gate; ``assume`` = inherited from the watermark
#: by an explicit seed (no check); ``agent`` = an agent reconciled the cell via
#: ``accept`` and it passed a structural verify (P2, the per-item ``accept --record``
#: path); ``semantic:<model>`` = an LLM judged the translation correct (P2, the
#: agent/autopilot tier only). The field is advisory metadata, kept so a later run can
#: distrust a specific source (e.g. a since-deprecated model) without nuking the ledger.


@dataclass(frozen=True)
class LedgerEntry:
    """One confirmed in-sync ``(slide_id, role)`` record (the two halves' fingerprints)."""

    de_hash: str
    en_hash: str
    # The cell's content-anchor construct slug (or None for markdown). Recorded as
    # forward-looking provenance for the P3 id-migration carry (#366) — the overlay
    # keys on the hashes alone, so this is deliberately recorded-but-unread today.
    construct: str | None
    confirmed_commit: str | None
    confirmed_by: str  # "apply" | "bless" | "accept" | "autopilot" | "seed" | "establish"
    confirmed_oracle: str  # "structural" | "assume" | "agent" | "semantic:<model>"
    # The hash-function version (``cache.WATERMARK_HASH_VERSION``) the de/en hashes were
    # computed under. ``trusts`` requires it to equal the current version, so a hash-form
    # change (#458 threaded the comment token into markdown hashing) cleanly invalidates a
    # stale entry — it re-checks and re-records rather than trusting a hash a newer engine
    # would compute differently. Defaults to the current version (a pre-#458 entry, which
    # carries no field, is assumed current — its hashes are unchanged for ``#`` decks and
    # simply will not match for ``//`` decks, so it re-checks either way).
    hash_version: int = WATERMARK_HASH_VERSION


#: Id-less narrative key ``(owning_slide_id, role, occ)`` — the classifier's own
#: narrative identity (:func:`~clm.slides.sync_plan._index_narratives_by_anchor`): the
#: ``occ``-th ``role`` narrative under ``owning_slide_id`` in document order, so it pairs
#: the DE narrative with its EN twin. ``occ`` is stable under a *non-narrative* sibling
#: insert (unlike the predecessor anchor); inserting another same-role narrative under
#: the same slide DOES shift the trailing ``occ``\\s — but that is not a correctness
#: hazard, because the overlay suppresses only on an exact both-halves hash match, so a
#: shifted entry that no longer byte-matches simply is not trusted (fail-safe, never a
#: silent edit drop). ``owning_slide_id`` is ``None`` for a narrative under no slide.
#: Reaches the #364/#365 residue the bare ``(slide_id, role)`` key cannot.
IdlessKey = tuple[str | None, str, int]


@dataclass
class SyncLedger:
    """The in-memory per-topic ledger.

    ``entries`` keys id'd cells by ``(slide_id, role)``; ``idless`` keys id-less
    localized **narratives** (voiceover / notes) by ``(owning_slide_id, role, occ)``
    (§11.2) — translations that carry no ``slide_id`` and so cannot ride the id'd map.
    """

    schema: int = SCHEMA_VERSION
    entries: dict[tuple[str, str], LedgerEntry] = field(default_factory=dict)
    idless: dict[IdlessKey, LedgerEntry] = field(default_factory=dict)
    #: Unknown top-level sections (the v3 ``decks`` store, #520 Phase 3),
    #: preserved verbatim across a load→save round trip so a v2 record can
    #: never clobber the v3 engine's trust. Never read by this module.
    extra: dict[str, object] = field(default_factory=dict)

    def trusts(self, slide_id: str, role: str, de_hash: str, en_hash: str) -> bool:
        """True iff ``(slide_id, role)`` is recorded in-sync at *exactly* these hashes.

        The overlay's core question: are both current halves byte-identical to a
        confirmation? Only then is the slide trusted-in-sync (skip re-litigation).
        Any drift on either half misses, so the slide falls through to the bundle.
        """
        entry = self.entries.get((slide_id, role))
        return _entry_trusts(entry, de_hash, en_hash)

    def trusts_idless(self, key: IdlessKey, de_hash: str, en_hash: str) -> bool:
        """:meth:`trusts` for an id-less narrative keyed by ``(owning_slide_id, role, occ)``."""
        return _entry_trusts(self.idless.get(key), de_hash, en_hash)


def _entry_trusts(entry: LedgerEntry | None, de_hash: str, en_hash: str) -> bool:
    """Whether ``entry`` confirms exactly these hashes under the *current* hash version.

    A stale ``hash_version`` (an entry recorded before a hash-form change) is never
    trusted — it re-checks and re-records, rather than trusting a hash a newer engine
    would compute differently (#458).
    """
    return (
        entry is not None
        and entry.hash_version == WATERMARK_HASH_VERSION
        and entry.de_hash == de_hash
        and entry.en_hash == en_hash
    )


def ledger_path_for(de_path: Path) -> Path:
    """The committed ledger path for the topic owning ``de_path``."""
    return de_path.parent / LEDGER_SUBDIR / LEDGER_FILENAME


# ---------------------------------------------------------------------------
# Load / save — canonical sorted JSON (merge-local, line-mergeable)
# ---------------------------------------------------------------------------


def load(path: Path) -> SyncLedger:
    """Read a ledger from ``path``; an absent or unreadable file is an empty ledger.

    A malformed/old-schema file degrades to empty (so the engine cold-starts the
    whole topic — fail-safe, never a crash) rather than raising.
    """
    if not path.is_file():
        return SyncLedger()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return SyncLedger()
    if not isinstance(data, dict) or data.get("schema") not in _READABLE_SCHEMAS:
        return SyncLedger()
    entries: dict[tuple[str, str], LedgerEntry] = {}
    slides = data.get("slides", {})
    if isinstance(slides, dict):
        for slide_id, roles in slides.items():
            if not isinstance(roles, dict):
                continue
            for role, rec in roles.items():
                if not isinstance(rec, dict) or "de_hash" not in rec or "en_hash" not in rec:
                    continue
                entries[(slide_id, role)] = LedgerEntry(
                    de_hash=rec["de_hash"],
                    en_hash=rec["en_hash"],
                    construct=rec.get("construct"),
                    confirmed_commit=rec.get("confirmed_commit"),
                    confirmed_by=rec.get("confirmed_by", "apply"),
                    confirmed_oracle=rec.get("confirmed_oracle", "structural"),
                    hash_version=rec.get("hash_version", WATERMARK_HASH_VERSION),
                )
    idless: dict[IdlessKey, LedgerEntry] = {}
    raw_idless = data.get("idless", [])
    if isinstance(raw_idless, list):
        for rec in raw_idless:
            if (
                not isinstance(rec, dict)
                or "role" not in rec
                or "de_hash" not in rec
                or "en_hash" not in rec
            ):
                continue
            key: IdlessKey = (rec.get("owning_slide_id"), rec["role"], int(rec.get("occ", 0)))
            idless[key] = LedgerEntry(
                de_hash=rec["de_hash"],
                en_hash=rec["en_hash"],
                construct=rec.get("construct"),
                confirmed_commit=rec.get("confirmed_commit"),
                confirmed_by=rec.get("confirmed_by", "apply"),
                confirmed_oracle=rec.get("confirmed_oracle", "structural"),
                hash_version=rec.get("hash_version", WATERMARK_HASH_VERSION),
            )
    extra = {k: v for k, v in data.items() if k not in ("schema", "slides", "idless")}
    return SyncLedger(schema=SCHEMA_VERSION, entries=entries, idless=idless, extra=extra)


def _to_json(ledger: SyncLedger) -> str:
    """Serialize to canonical sorted JSON: nested ``slides[slide_id][role]`` + trailing newline.

    Sorted keys + per-field lines make a per-topic merge auto-resolve when two
    branches confirm *different* slides, and turn a genuine same-slide conflict into
    a reviewable line conflict (the design's drop-on-conflict → re-check rule).
    """
    slides: dict[str, dict[str, dict[str, str | int | None]]] = {}
    for (slide_id, role), e in ledger.entries.items():
        slides.setdefault(slide_id, {})[role] = {
            "de_hash": e.de_hash,
            "en_hash": e.en_hash,
            "construct": e.construct,
            "confirmed_commit": e.confirmed_commit,
            "confirmed_by": e.confirmed_by,
            "confirmed_oracle": e.confirmed_oracle,
            "hash_version": e.hash_version,
        }
    # Id-less narratives are a *list* (their key has no natural nesting): one object per
    # entry, sorted by the key so the file is canonical and a merge is line-local.
    idless = [
        {
            "owning_slide_id": owning,
            "role": role,
            "occ": occ,
            "de_hash": e.de_hash,
            "en_hash": e.en_hash,
            "construct": e.construct,
            "confirmed_commit": e.confirmed_commit,
            "confirmed_by": e.confirmed_by,
            "confirmed_oracle": e.confirmed_oracle,
            "hash_version": e.hash_version,
        }
        for (owning, role, occ), e in sorted(
            ledger.idless.items(), key=lambda kv: (str(kv[0][0]), kv[0][1], kv[0][2])
        )
    ]
    # A file carrying preserved v3 sections stays on the coexistence
    # envelope (schema 2); a plain v1 payload keeps its schema-1 shape.
    schema = 2 if ledger.extra else ledger.schema
    payload: dict[str, object] = {"schema": schema, "slides": slides}
    if idless:
        payload["idless"] = idless
    payload.update(ledger.extra)
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def save(ledger: SyncLedger, path: Path) -> None:
    """Write ``ledger`` to ``path`` (canonical JSON), creating ``.clm/`` atomically."""
    from clm.infrastructure.utils.path_utils import atomic_write_bytes

    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, _to_json(ledger).encode("utf-8"))


# ---------------------------------------------------------------------------
# Building current per-(slide_id, role) fingerprints (no sync_plan dependency)
# ---------------------------------------------------------------------------


def _inlined_deck_text(path: Path) -> str:
    """A deck's text with its separated voiceover companion inlined (issue #501).

    The ledger records and reads its per-cell fingerprints over this projection —
    the SAME companion-inlined representation ``build_sync_plan``'s trust overlay
    compares against (``de_current`` is built from the projected text). Without it a
    separated deck's voiceover lives only in the companion, so ``record_pair`` /
    ``record_edit`` would fingerprint a voiceover-free deck, never record the
    narration, and the overlay's suppression would silently never fire for it. A
    plain deck (no companion) returns its text unchanged, so nothing else moves.
    """
    from clm.slides.voiceover_tools import inline_pair_text, resolve_companion

    text = path.read_text(encoding="utf-8")
    companion = resolve_companion(path)
    if companion is None:
        return text
    return inline_pair_text(
        text, companion.read_text(encoding="utf-8"), comment_token_for_path(path)
    ).inlined_text


def _localized_idd_hashes(
    cells: list[Cell], lang: str
) -> dict[tuple[str, str], tuple[str, str | None]]:
    """``{(slide_id, role): (content_hash, construct)}`` for ``lang``'s id'd localized cells.

    Mirrors :func:`~clm.slides.sync_plan.ordered_sync_cells` keying (same
    ``role_of`` / ``hash_cell`` / ``construct_of`` chokepoints) but stands alone so
    the ledger never imports ``sync_plan``. Neutral cells (``lang is None``) and
    id-less cells are excluded — the ledger records only the translation pairs it can
    key by ``(slide_id, role)``.
    """
    out: dict[tuple[str, str], tuple[str, str | None]] = {}
    for cell in cells:
        meta = cell.metadata
        if meta.is_j2 or meta.lang != lang or not meta.slide_id:
            continue
        role = role_of(meta)
        if role is None:
            continue
        out[(meta.slide_id, role)] = (
            hash_cell(meta, cell.content),
            construct_of(meta, cell.content),
        )
    return out


def current_pairs(
    de_path: Path, en_path: Path
) -> dict[tuple[str, str], tuple[str, str, str | None]]:
    """``{(slide_id, role): (de_hash, en_hash, construct)}`` for the pair's localized id'd cells.

    Only keys present (id'd, localized) in **both** halves are returned — a
    one-sided cell has no twin to certify in-sync. Fingerprints the companion-inlined
    projection (issue #501) so a separated deck's voiceover narratives — id'd once
    ``extract`` has canonicalized them — are recorded like any other localized cell.
    """
    de_cells = parse_cells(_inlined_deck_text(de_path), comment_token_for_path(de_path))
    en_cells = parse_cells(_inlined_deck_text(en_path), comment_token_for_path(en_path))
    de_map = _localized_idd_hashes(de_cells, "de")
    en_map = _localized_idd_hashes(en_cells, "en")
    pairs: dict[tuple[str, str], tuple[str, str, str | None]] = {}
    for key in de_map.keys() & en_map.keys():
        de_hash, construct = de_map[key]
        en_hash, _ = en_map[key]
        pairs[key] = (de_hash, en_hash, construct)
    return pairs


def _idless_narrative_hashes(path: Path, lang: str) -> dict[IdlessKey, str]:
    """``{(owning_slide_id, role, occ): content_hash}`` for ``lang``'s id-less narratives.

    Keys exactly as the classifier does — :func:`~clm.slides.sync_plan._index_narratives_by_anchor`
    over the narrative-identity-stamped cells — so a recorded entry lines up with the
    narrative proposal the overlay must suppress. ``occ`` is per ``(owning_slide_id,
    role)``, so the empty ``skip_owning`` set here is harmless: a dup-slide narrative is
    indexed too, but it never bears a proposal, and it cannot shift another slide's occ.
    Imported from ``sync_plan`` lazily (the function is only on the write path) to keep
    the ledger module free of a load-time ``sync_plan`` dependency.
    """
    from clm.slides.raw_cells import split_cells
    from clm.slides.sync_plan import (
        NARRATIVE_ROLES,
        _index_narratives_by_anchor,
        narrative_identity_map,
        ordered_sync_cells,
    )

    text = _inlined_deck_text(path)  # issue #501: fingerprint over the companion-inlined projection
    token = comment_token_for_path(path)
    identity = narrative_identity_map(split_cells(text, token)[1])
    cells = ordered_sync_cells(parse_cells(text, token), lang, identity)
    narratives = [c for c in cells if c.role in NARRATIVE_ROLES and c.slide_id is None]
    return {
        key: c.content_hash for key, c in _index_narratives_by_anchor(narratives, set()).items()
    }


def current_idless_pairs(de_path: Path, en_path: Path) -> dict[IdlessKey, tuple[str, str]]:
    """``{(owning_slide_id, role, occ): (de_hash, en_hash)}`` for the pair's id-less narratives.

    Only keys present in **both** halves are returned — a one-sided narrative (the deck
    grew/lost one) has no twin to certify in-sync and stays on the cold path.
    """
    de_map = _idless_narrative_hashes(de_path, "de")
    en_map = _idless_narrative_hashes(en_path, "en")
    return {key: (de_map[key], en_map[key]) for key in de_map.keys() & en_map.keys()}


# ---------------------------------------------------------------------------
# Recording a confirmation (the write path — gated on structural verify)
# ---------------------------------------------------------------------------


@dataclass
class RecordResult:
    """Outcome of :func:`record_pair` / :func:`record_edit`."""

    path: Path
    recorded: int = 0  # entries written this call (id'd + id-less narratives)
    refused: bool = False  # the structural gate failed — nothing was written
    reasons: list[str] = field(default_factory=list)  # structural violation messages on refusal


def _resolve_commit(commit: str | None, de_path: Path) -> str | None:
    """The commit to stamp on a record — the caller's, else the repo HEAD at ``de_path``.

    Git provenance is best-effort: a record must never fail because git is unavailable,
    so an unresolvable HEAD yields ``None`` rather than raising.
    """
    if commit is not None:
        return commit
    from clm.core.git_info import get_git_info

    info = get_git_info(de_path.parent).get("commit")
    return info if isinstance(info, str) else None


def record_pair(
    de_path: Path,
    en_path: Path,
    *,
    confirmed_by: str,
    confirmed_oracle: str = "structural",
    commit: str | None = None,
) -> RecordResult:
    """Record the pair's localized slides + id-less narratives in-sync — gated on ``verify``.

    Loads the existing topic ledger, updates the ``(slide_id, role)`` entries (id'd
    cells) and the ``(owning_slide_id, role, occ)`` ``idless`` entries (narratives) for
    *this* pair (preserving entries for other decks in the same topic), and writes it
    back. ``commit`` defaults to the repo HEAD at ``de_path`` (best-effort —
    git provenance must never fail a record). Returns a refusal (writing nothing) when
    the pair fails the whole-deck structural gate.

    Stale entries for slides removed from the deck are left in place (harmless — no
    current cell matches them, so they never wrongly suppress); a future ``prune``
    sweeps them.

    Only the final file write is atomic (:func:`atomic_write_bytes`); the
    load→update→save is not locked, so two concurrent records into one topic's ledger
    could lose an update (read-modify-write race). For an authoring CLI that is
    effectively never hit; left unlocked for P1.
    """
    from clm.slides.sync_verify import structural_gate

    de_text = de_path.read_text(encoding="utf-8")
    en_text = en_path.read_text(encoding="utf-8")
    violations = structural_gate(de_text, en_text, comment_token_for_path(de_path))
    path = ledger_path_for(de_path)
    if violations:
        return RecordResult(path=path, refused=True, reasons=[v.message for v in violations])

    commit = _resolve_commit(commit, de_path)

    pairs = current_pairs(de_path, en_path)
    idless = current_idless_pairs(de_path, en_path)
    ledger = load(path)
    for (slide_id, role), (de_hash, en_hash, construct) in pairs.items():
        ledger.entries[(slide_id, role)] = LedgerEntry(
            de_hash=de_hash,
            en_hash=en_hash,
            construct=construct,
            confirmed_commit=commit,
            confirmed_by=confirmed_by,
            confirmed_oracle=confirmed_oracle,
        )
    for key, (de_hash, en_hash) in idless.items():
        ledger.idless[key] = LedgerEntry(
            de_hash=de_hash,
            en_hash=en_hash,
            construct=None,  # narratives are markdown — no construct
            confirmed_commit=commit,
            confirmed_by=confirmed_by,
            confirmed_oracle=confirmed_oracle,
        )
    save(ledger, path)
    return RecordResult(path=path, recorded=len(pairs) + len(idless))


def record_edit(
    de_path: Path,
    en_path: Path,
    *,
    slide_id: str | None,
    role: str,
    owning_slide_id: str | None = None,
    anchor_occ: int = 0,
    confirmed_by: str,
    confirmed_oracle: str = "agent",
    commit: str | None = None,
) -> RecordResult:
    """Record ONE just-accepted localized edit in-sync — the per-item confirm path (§11.4).

    The ``accept --record`` write-back: after ``clm slides sync accept`` reconciles a
    single drifted localized cell, bank *that one cell* as trusted-in-sync so a later
    ``report`` / ``apply --ledger`` skips it. It records **only** the accepted cell
    (design guard 1 — never the whole pair, whose other residue is still unresolved), so
    it deliberately does **not** reuse :func:`record_pair` (which records every current
    pair, and would wrongly trust the unreconciled siblings).

    Routes by the cell's identity — the three shapes an ``edit`` can take:

    * **id'd** (``slide_id`` given) → ``entries[(slide_id, role)]``, gated on a
      *per-slide* structural verify scoped to this ``slide_id`` (guard 2 — a corruption
      *elsewhere* in the deck must not block banking the one clean slide an agent just
      reconciled, unlike the whole-deck gate :func:`record_pair` uses). The gate is
      scoped to ``slide_id`` alone (not ``(slide_id, role)``): any real duplicate-id or
      asymmetry on this slide blocks the record regardless of role, which keeps the
      ledger decoupled from ``verify``'s internal role vocabulary and is strictly
      fail-safe (it can only refuse more, never record a corrupt cell).
    * **id-less narrative** (``role`` ∈ :data:`~clm.slides.sync_plan.NARRATIVE_ROLES`,
      no ``slide_id``) → ``idless[(owning_slide_id, role, anchor_occ)]``. Narratives carry
      no id invariant (no symmetry / dup-id check applies to them), so there is no
      structural scope to gate on; the ``accept`` validator already ran, and the
      both-halves hash gate keeps a later drift from being trusted.
    * **id-less localized code** (#365) → not in the ledger's scope (the structural-pass
      direction mechanism governs it, not the overlay); returns ``recorded=0`` (a no-op,
      not a refusal).

    Provenance defaults to ``confirmed_oracle=agent`` (guard 3): an agent asserted the
    reconciliation and it passed a structural verify — distinct from a model's
    ``semantic:<model>`` verdict, so a later run can selectively distrust agent-confirmed
    entries. Reads the current (post-accept) hashes from disk. A cell absent from either
    half (the lookup misses) is a refusal — there is no in-sync twin to certify.
    """
    from clm.slides.sync_plan import NARRATIVE_ROLES

    path = ledger_path_for(de_path)
    commit = _resolve_commit(commit, de_path)

    if slide_id is not None:
        from clm.slides.sync_verify import structural_gate

        violations = structural_gate(
            de_path.read_text(encoding="utf-8"),
            en_path.read_text(encoding="utf-8"),
            comment_token_for_path(de_path),
            slide_id=slide_id,
        )
        if violations:
            return RecordResult(path=path, refused=True, reasons=[v.message for v in violations])
        hit = current_pairs(de_path, en_path).get((slide_id, role))
        if hit is None:
            return RecordResult(
                path=path,
                refused=True,
                reasons=[f"({slide_id}, {role}) is not a localized cell present in both halves"],
            )
        de_hash, en_hash, construct = hit
        ledger = load(path)
        ledger.entries[(slide_id, role)] = LedgerEntry(
            de_hash=de_hash,
            en_hash=en_hash,
            construct=construct,
            confirmed_commit=commit,
            confirmed_by=confirmed_by,
            confirmed_oracle=confirmed_oracle,
        )
        save(ledger, path)
        return RecordResult(path=path, recorded=1)

    if role in NARRATIVE_ROLES:
        key: IdlessKey = (owning_slide_id, role, anchor_occ)
        narr = current_idless_pairs(de_path, en_path).get(key)
        if narr is None:
            return RecordResult(
                path=path,
                refused=True,
                reasons=[f"narrative {key} is not present in both halves"],
            )
        de_hash, en_hash = narr
        ledger = load(path)
        ledger.idless[key] = LedgerEntry(
            de_hash=de_hash,
            en_hash=en_hash,
            construct=None,  # narratives are markdown — no construct
            confirmed_commit=commit,
            confirmed_by=confirmed_by,
            confirmed_oracle=confirmed_oracle,
        )
        save(ledger, path)
        return RecordResult(path=path, recorded=1)

    # id-less localized code (#365): governed by the structural-pass direction mechanism,
    # not the ledger overlay — a fail-safe no-op (nothing to record, not a refusal).
    return RecordResult(path=path, recorded=0)


def seed_from_watermark(
    de_path: Path,
    en_path: Path,
    cache: SyncWatermarkCache,
    *,
    confirmed_by: str = "seed",
) -> RecordResult:
    """Seed the ledger from the existing watermark, stamping ``confirmed_oracle=assume``.

    The opt-in bootstrap (design note §11.5): a legacy deck that already has a
    watermark but no ledger inherits its per-slide trust here instead of cold-starting
    every slide. For each localized ``(slide_id, role)`` recorded in the watermark's
    ``de`` / ``en`` partitions it writes a ledger entry carrying the **watermark's**
    recorded half-hashes (not the current file's), the watermark's ``synced_commit``,
    and ``confirmed_oracle="assume"`` — inherited trust, **not** a fresh check.

    **Safe against a stale watermark:** the entry stores the watermark hashes, so on
    the next ``report``/``apply --ledger`` a slide that has drifted since the watermark
    no longer matches the *current* halves and re-checks (the cold path) — never a
    silent mis-sync. The ``assume`` provenance keeps the inherited trust legible and
    revocable. A version-stale watermark (#429) reads as empty (``get_deck`` gates on
    ``hash_version``), so nothing is seeded — correct, since its hashes are
    incomparable.

    Gated on a whole-deck structural ``verify`` of the *current* pair (a corrupt
    working tree is refused, like ``bless``). **Fill-gaps only:** an existing entry
    (e.g. a real ``bless``/``apply`` confirmation) is never downgraded to ``assume`` —
    only ``(slide_id, role)`` keys absent from the ledger are seeded.
    """
    from clm.slides.sync_plan import MEMBERSHIP_ROLES
    from clm.slides.sync_verify import structural_gate

    de_text = de_path.read_text(encoding="utf-8")
    en_text = en_path.read_text(encoding="utf-8")
    violations = structural_gate(de_text, en_text, comment_token_for_path(de_path))
    path = ledger_path_for(de_path)
    if violations:
        return RecordResult(path=path, refused=True, reasons=[v.message for v in violations])

    def _idd(
        rows: list[tuple[int, str | None, str, str, str | None]],
    ) -> dict[tuple[str, str], tuple[str, str | None]]:
        # The real-role id'd cells only — drop the membership-widened synthetic rows
        # (#190 §5.3), matching what ``record_pair`` records.
        return {
            (sid, role): (chash, construct)
            for (_pos, sid, role, chash, construct) in rows
            if sid is not None and role not in MEMBERSHIP_ROLES
        }

    de_map = _idd(cache.get_deck(str(de_path), str(en_path), "de"))
    en_map = _idd(cache.get_deck(str(de_path), str(en_path), "en"))
    commit = cache.get_synced_commit(str(de_path), str(en_path))

    ledger = load(path)
    seeded = 0
    for key in de_map.keys() & en_map.keys():
        if key in ledger.entries:
            continue  # never downgrade a real confirmation to ``assume``
        de_hash, construct = de_map[key]
        en_hash, _ = en_map[key]
        ledger.entries[key] = LedgerEntry(
            de_hash=de_hash,
            en_hash=en_hash,
            construct=construct,
            confirmed_commit=commit,
            confirmed_by=confirmed_by,
            confirmed_oracle="assume",
        )
        seeded += 1
    if seeded:
        save(ledger, path)
    return RecordResult(path=path, recorded=seeded)


# ---------------------------------------------------------------------------
# The semantic rung (P2) — judge each un-trusted localized pair with an injected
# LLM oracle and bank the faithful ones (``confirmed_oracle=semantic:<model>``).
# The judge is INJECTED (a ``SemanticJudge`` duck type), so this module stays
# model-free; the concrete client lives in ``sync_semantic`` and is constructed only
# by the model-bearing ``baseline establish`` command (epic #440 decision B).
# ---------------------------------------------------------------------------


def _localized_idd_full(
    cells: list[Cell], lang: str
) -> dict[tuple[str, str], tuple[str, str, str | None]]:
    """``{(slide_id, role): (body, content_hash, construct)}`` for ``lang``'s id'd cells.

    The body-carrying sibling of :func:`_localized_idd_hashes` (same chokepoints), so the
    semantic judge can be shown each cell's text while the hash still keys the ledger.
    """
    out: dict[tuple[str, str], tuple[str, str, str | None]] = {}
    for cell in cells:
        meta = cell.metadata
        if meta.is_j2 or meta.lang != lang or not meta.slide_id:
            continue
        role = role_of(meta)
        if role is None:
            continue
        out[(meta.slide_id, role)] = (
            cell.content,
            hash_cell(meta, cell.content),
            construct_of(meta, cell.content),
        )
    return out


def current_idd_full(
    de_path: Path, en_path: Path
) -> dict[tuple[str, str], tuple[str, str, str, str, str | None]]:
    """``{(slide_id, role): (de_body, en_body, de_hash, en_hash, construct)}`` (both halves).

    Like :func:`current_pairs` but carries the cell bodies too — what the semantic judge
    needs. Only keys present (id'd, localized) in **both** halves are returned.
    """
    de_cells = parse_cells(de_path.read_text(encoding="utf-8"), comment_token_for_path(de_path))
    en_cells = parse_cells(en_path.read_text(encoding="utf-8"), comment_token_for_path(en_path))
    de_map = _localized_idd_full(de_cells, "de")
    en_map = _localized_idd_full(en_cells, "en")
    out: dict[tuple[str, str], tuple[str, str, str, str, str | None]] = {}
    for key in de_map.keys() & en_map.keys():
        de_body, de_hash, construct = de_map[key]
        en_body, en_hash, _ = en_map[key]
        out[key] = (de_body, en_body, de_hash, en_hash, construct)
    return out


def _idless_narrative_full(path: Path, lang: str) -> dict[IdlessKey, tuple[str, str]]:
    """``{(owning_slide_id, role, occ): (body, content_hash)}`` for ``lang``'s narratives.

    The body-carrying sibling of :func:`_idless_narrative_hashes`. A ``CurrentCell`` keeps
    only the hash, so the body is recovered from the raw cell stream via its ``raw_index``
    (the verbatim ``RawCell.body`` — what the judge is shown). A narrative whose raw cell
    can't be located is omitted (its hash-only twin would still cold-start, fail-safe).
    """
    from clm.slides.raw_cells import split_cells
    from clm.slides.sync_plan import (
        NARRATIVE_ROLES,
        _index_narratives_by_anchor,
        narrative_identity_map,
        ordered_sync_cells,
    )

    text = _inlined_deck_text(path)  # issue #501: fingerprint over the companion-inlined projection
    token = comment_token_for_path(path)
    raw_cells = split_cells(text, token)[1]
    identity = narrative_identity_map(raw_cells)
    cells = ordered_sync_cells(parse_cells(text, token), lang, identity)
    narratives = [c for c in cells if c.role in NARRATIVE_ROLES and c.slide_id is None]
    out: dict[IdlessKey, tuple[str, str]] = {}
    for key, c in _index_narratives_by_anchor(narratives, set()).items():
        if c.raw_index is None or c.raw_index >= len(raw_cells):
            continue
        out[key] = (raw_cells[c.raw_index].body, c.content_hash)
    return out


def current_idless_full(de_path: Path, en_path: Path) -> dict[IdlessKey, tuple[str, str, str, str]]:
    """``{(owning_slide_id, role, occ): (de_body, en_body, de_hash, en_hash)}`` (both halves)."""
    de_map = _idless_narrative_full(de_path, "de")
    en_map = _idless_narrative_full(en_path, "en")
    out: dict[IdlessKey, tuple[str, str, str, str]] = {}
    for key in de_map.keys() & en_map.keys():
        de_body, de_hash = de_map[key]
        en_body, en_hash = en_map[key]
        out[key] = (de_body, en_body, de_hash, en_hash)
    return out


@dataclass
class SemanticRejection:
    """One pair the semantic judge declined to bank (a genuine divergence, or a call failure)."""

    slide_id: str | None  # the slide_id (id'd) or owning_slide_id (narrative)
    role: str
    occ: int | None  # the narrative occurrence ordinal, or None for an id'd cell
    reason: str


@dataclass
class SemanticRecordResult:
    """Outcome of :func:`record_semantic` for one pair."""

    path: Path
    judged: int = 0  # pairs sent to the LLM that returned a verdict (correct or not)
    recorded: int = 0  # judged faithful → banked (oracle=semantic:<model>)
    skipped: int = 0  # already trusted at current hashes by a real oracle → not re-paid
    rejected: list[SemanticRejection] = field(default_factory=list)  # judged NOT faithful
    failed: list[SemanticRejection] = field(default_factory=list)  # the call raised (left cold)
    refused: bool = False  # the whole-deck structural gate failed — nothing judged/written
    reasons: list[str] = field(default_factory=list)


def record_semantic(
    de_path: Path,
    en_path: Path,
    judge: SemanticJudge,
    *,
    model: str,
    scope_assume: bool = True,
    commit: str | None = None,
) -> SemanticRecordResult:
    """LLM-judge each un-trusted localized pair and bank the faithful ones (#448 P2).

    The ``semantic`` rung's recorder, driving ``clm slides sync baseline establish``. For
    each localized id'd cell **and** id-less narrative present in both halves, it asks the
    injected ``judge`` (a :class:`~clm.slides.sync_semantic.SemanticJudge`) whether the EN
    half faithfully renders the DE half, and on a *yes* banks it as
    ``confirmed_oracle="semantic:{model}"`` / ``confirmed_by="establish"`` — so a slide
    paid for once becomes a free ledger hit. A *no* is collected in ``rejected`` (a real
    divergence the author must reconcile) and **not** banked; a judge failure goes to
    ``failed`` and leaves the slide cold (re-run retries).

    **Cost discipline (§9.4).** A pair is judged iff it is *not already trusted at its
    current hashes by a real oracle* — i.e. judge the cold (no entry / entry at a
    different, drifted hash) and the ``assume``-seeded (``scope_assume``, the default),
    and **skip** a pair already confirmed at its current hashes by ``structural`` /
    ``agent`` / a prior ``semantic`` (counted in ``skipped``, never re-paid). This is the
    user-chosen "cold + re-judge assume seeds" scope.

    Gated on a whole-deck structural ``verify`` (like ``bless``): a structurally corrupt
    pair is refused, judging nothing. The judge is called **per slide**, each error
    caught so one transient failure neither aborts the pass nor banks anything; the ledger
    is saved once at the end (a hard crash loses only this run's verdicts — the previously
    committed ledger is intact, and a re-run re-judges).
    """
    from clm.slides.sync_verify import structural_gate

    de_text = de_path.read_text(encoding="utf-8")
    en_text = en_path.read_text(encoding="utf-8")
    path = ledger_path_for(de_path)
    violations = structural_gate(de_text, en_text, comment_token_for_path(de_path))
    if violations:
        return SemanticRecordResult(
            path=path, refused=True, reasons=[v.message for v in violations]
        )

    commit = _resolve_commit(commit, de_path)
    oracle = f"semantic:{model}"
    ledger = load(path)
    result = SemanticRecordResult(path=path)
    changed = False

    def _trusted(existing: LedgerEntry | None, de_hash: str, en_hash: str) -> bool:
        # Already trusted at the CURRENT hashes by a real (non-assume) oracle → skip.
        # `assume` at current hashes is re-judged when scope_assume (upgrade inherited
        # trust); a cold / drifted entry is never "trusted" here so it is judged.
        if existing is None or existing.de_hash != de_hash or existing.en_hash != en_hash:
            return False
        if existing.confirmed_oracle == "assume" and scope_assume:
            return False
        return True

    def _judge(
        *,
        de_body: str,
        en_body: str,
        role: str,
        sid: str | None,
        occ: int | None,
        de_hash: str,
        en_hash: str,
        construct: str | None,
    ) -> None:
        nonlocal changed
        try:
            verdict = judge.judge(de_body=de_body, en_body=en_body, role=role)
        except Exception as exc:  # noqa: BLE001 - a failed call leaves the slide cold, never banks
            result.failed.append(SemanticRejection(sid, role, occ, f"judge call failed: {exc}"))
            return
        result.judged += 1
        if not verdict.correct:
            result.rejected.append(
                SemanticRejection(
                    sid, role, occ, verdict.reason or "judged not a faithful translation"
                )
            )
            return
        entry = LedgerEntry(
            de_hash=de_hash,
            en_hash=en_hash,
            construct=construct,
            confirmed_commit=commit,
            confirmed_by="establish",
            confirmed_oracle=oracle,
        )
        if occ is None:
            ledger.entries[(sid, role)] = entry  # type: ignore[index]  # id'd: sid is not None
        else:
            ledger.idless[(sid, role, occ)] = entry
        result.recorded += 1
        changed = True

    for (sid, role), (de_body, en_body, de_hash, en_hash, construct) in current_idd_full(
        de_path, en_path
    ).items():
        if _trusted(ledger.entries.get((sid, role)), de_hash, en_hash):
            result.skipped += 1
            continue
        _judge(
            de_body=de_body,
            en_body=en_body,
            role=role,
            sid=sid,
            occ=None,
            de_hash=de_hash,
            en_hash=en_hash,
            construct=construct,
        )

    for (owning, role, occ), (de_body, en_body, de_hash, en_hash) in current_idless_full(
        de_path, en_path
    ).items():
        if _trusted(ledger.idless.get((owning, role, occ)), de_hash, en_hash):
            result.skipped += 1
            continue
        _judge(
            de_body=de_body,
            en_body=en_body,
            role=role,
            sid=owning,
            occ=occ,
            de_hash=de_hash,
            en_hash=en_hash,
            construct=None,
        )

    if changed:
        save(ledger, path)
    return result


# ---------------------------------------------------------------------------
# Id-migration carry (P3) — re-key ledger entries across a slide_id rename so a
# confirmed slide follows its id instead of orphaning to the cold path (#366/#454).
# ---------------------------------------------------------------------------


@dataclass
class CarryResult:
    """Outcome of :func:`carry_id_migrations`."""

    path: Path
    carried: int = 0  # entries (id'd + id-less narratives) re-keyed old -> new
    dropped: int = 0  # stale old entries removed because the new key already existed


def carry_id_migrations(de_path: Path, id_migrations: dict[str, str]) -> CarryResult:
    """Re-key ledger entries across an ``old_id -> new_id`` slide_id rename (#448 P3).

    Consumes :attr:`~clm.slides.sync_apply.ApplyResult.id_migrations` (#454 — the
    non-None -> non-None renames a ``realign`` / deterministic id-migration performed):
    a slide that already carries a ledger entry under ``old_id`` has that entry **follow
    it to ``new_id``** (key rewritten, ``LedgerEntry`` preserved verbatim — hashes and
    ``confirmed_*`` provenance intact) instead of orphaning to the cold path (design §6,
    the sharpest risk). Both the id'd ``(old_id, role)`` entries and the id-less narrative
    ``(old_id, role, occ)`` entries *owned by* ``old_id`` are carried.

    **Fail-safe by construction.** The carried entry keeps its *old* hashes, so the
    overlay's exact both-halves gate still decides suppression correctly at ``new_id``:
    a pure relabel (content unchanged) matches and stays trusted; a rename that *also*
    changed the cell body no longer matches and re-checks (cold path) — never a silent
    wrong-suppression. If ``new_id`` already has an entry (the destination is already
    authoritative) the stale ``old_id`` entry is **dropped**, not clobbered over it.

    A no-op when there is no ledger file or no migrations (it never *creates* a ledger —
    carry only moves existing trust). Saves only if something changed.
    """
    path = ledger_path_for(de_path)
    if not id_migrations or not path.is_file():
        return CarryResult(path=path)

    ledger = load(path)
    carried = 0
    dropped = 0
    for old_id, new_id in id_migrations.items():
        if old_id == new_id:
            continue
        for role in [r for (sid, r) in list(ledger.entries) if sid == old_id]:
            entry = ledger.entries.pop((old_id, role))
            if (new_id, role) in ledger.entries:
                dropped += 1  # new key already authoritative — drop the stale old, don't clobber
            else:
                ledger.entries[(new_id, role)] = entry
                carried += 1
        for key in [k for k in list(ledger.idless) if k[0] == old_id]:
            entry = ledger.idless.pop(key)
            new_key: IdlessKey = (new_id, key[1], key[2])
            if new_key in ledger.idless:
                dropped += 1
            else:
                ledger.idless[new_key] = entry
                carried += 1

    if carried or dropped:
        save(ledger, path)
    return CarryResult(path=path, carried=carried, dropped=dropped)
