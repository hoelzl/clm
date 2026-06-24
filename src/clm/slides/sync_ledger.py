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
two halves are *translations* (the cells the engine reconciles and that need
trust). Language-neutral cells are byte-identical across halves and governed
structurally (``verify``), so they need no translation-trust and are not recorded.
Id-less localized narratives (the #364/#365 residue) are **not** recorded yet —
they always fall through to the cold path (the fail-safe direction: more checking,
never silent mis-sync); a later slice keys them by ``(owning_slide_id, role,
anchor)`` per §11.2.

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

from clm.notebooks.slide_parser import Cell, comment_token_for_path, parse_cells
from clm.slides.sync_writeback import construct_of, hash_cell, role_of

SCHEMA_VERSION = 1
#: The committed ledger lives under the build-internal ``.clm/`` tree (issue #453),
#: alongside ``cassettes/`` — both are committed build inputs, neither is student output.
LEDGER_SUBDIR = ".clm"
LEDGER_FILENAME = "sync-ledger.json"

#: Provenance of a recorded confirmation (``confirmed_oracle``). ``structural`` =
#: passed the deterministic structural gate; ``assume`` = inherited from the watermark
#: by an explicit seed (no check); ``semantic:<model>`` = an LLM judged the translation
#: (P2, agent tier only). The field is advisory metadata, kept so a later run can
#: distrust a specific source without nuking the whole ledger.


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
    confirmed_by: str  # "apply" | "bless" | "accept" | "autopilot"
    confirmed_oracle: str  # "structural" | "assume" | "semantic:<model>"


@dataclass
class SyncLedger:
    """The in-memory per-topic ledger, keyed by ``(slide_id, role)``."""

    schema: int = SCHEMA_VERSION
    entries: dict[tuple[str, str], LedgerEntry] = field(default_factory=dict)

    def trusts(self, slide_id: str, role: str, de_hash: str, en_hash: str) -> bool:
        """True iff ``(slide_id, role)`` is recorded in-sync at *exactly* these hashes.

        The overlay's core question: are both current halves byte-identical to a
        confirmation? Only then is the slide trusted-in-sync (skip re-litigation).
        Any drift on either half misses, so the slide falls through to the bundle.
        """
        entry = self.entries.get((slide_id, role))
        return entry is not None and entry.de_hash == de_hash and entry.en_hash == en_hash


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
    if not isinstance(data, dict) or data.get("schema") != SCHEMA_VERSION:
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
                )
    return SyncLedger(schema=SCHEMA_VERSION, entries=entries)


def _to_json(ledger: SyncLedger) -> str:
    """Serialize to canonical sorted JSON: nested ``slides[slide_id][role]`` + trailing newline.

    Sorted keys + per-field lines make a per-topic merge auto-resolve when two
    branches confirm *different* slides, and turn a genuine same-slide conflict into
    a reviewable line conflict (the design's drop-on-conflict → re-check rule).
    """
    slides: dict[str, dict[str, dict[str, str | None]]] = {}
    for (slide_id, role), e in ledger.entries.items():
        slides.setdefault(slide_id, {})[role] = {
            "de_hash": e.de_hash,
            "en_hash": e.en_hash,
            "construct": e.construct,
            "confirmed_commit": e.confirmed_commit,
            "confirmed_by": e.confirmed_by,
            "confirmed_oracle": e.confirmed_oracle,
        }
    payload = {"schema": ledger.schema, "slides": slides}
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def save(ledger: SyncLedger, path: Path) -> None:
    """Write ``ledger`` to ``path`` (canonical JSON), creating ``.clm/`` atomically."""
    from clm.infrastructure.utils.path_utils import atomic_write_bytes

    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, _to_json(ledger).encode("utf-8"))


# ---------------------------------------------------------------------------
# Building current per-(slide_id, role) fingerprints (no sync_plan dependency)
# ---------------------------------------------------------------------------


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
    one-sided cell has no twin to certify in-sync.
    """
    de_cells = parse_cells(de_path.read_text(encoding="utf-8"), comment_token_for_path(de_path))
    en_cells = parse_cells(en_path.read_text(encoding="utf-8"), comment_token_for_path(en_path))
    de_map = _localized_idd_hashes(de_cells, "de")
    en_map = _localized_idd_hashes(en_cells, "en")
    pairs: dict[tuple[str, str], tuple[str, str, str | None]] = {}
    for key in de_map.keys() & en_map.keys():
        de_hash, construct = de_map[key]
        en_hash, _ = en_map[key]
        pairs[key] = (de_hash, en_hash, construct)
    return pairs


# ---------------------------------------------------------------------------
# Recording a confirmation (the write path — gated on structural verify)
# ---------------------------------------------------------------------------


@dataclass
class RecordResult:
    """Outcome of :func:`record_pair`."""

    path: Path
    recorded: int = 0  # number of (slide_id, role) entries written this call
    refused: bool = False  # the structural gate failed — nothing was written
    reasons: list[str] = field(default_factory=list)  # structural violation messages on refusal


def record_pair(
    de_path: Path,
    en_path: Path,
    *,
    confirmed_by: str,
    confirmed_oracle: str = "structural",
    commit: str | None = None,
) -> RecordResult:
    """Record the pair's localized slides as confirmed in-sync — gated on ``verify``.

    Loads the existing topic ledger, updates the ``(slide_id, role)`` entries for the
    slides in *this* pair (preserving entries for other decks in the same topic), and
    writes it back. ``commit`` defaults to the repo HEAD at ``de_path`` (best-effort —
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

    if commit is None:
        from clm.core.git_info import get_git_info

        info = get_git_info(de_path.parent).get("commit")
        commit = info if isinstance(info, str) else None

    pairs = current_pairs(de_path, en_path)
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
    save(ledger, path)
    return RecordResult(path=path, recorded=len(pairs))
