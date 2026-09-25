"""The committed per-topic recordings ledger — source-anchored provenance (#1004).

Design: ``docs/claude/design/recordings-rerecord-backlog.md`` §4.1 (issue
#907 step 1). The machine-local state file
(:mod:`clm.recordings.state`) keeps the *workflow* fields of a recording —
raw file, takes, processing status. What a recording **showed** is course
knowledge that every editing agent needs, so it lives in a committed file
beside the deck: ``<topic>/.clm/recordings-ledger.json``, the same
directory and gitignore convention as the sync ledger
(:mod:`clm.slides.doc_ledger`).

Per deck (keyed by the language-free stem, :func:`deck_key_for`), one entry
per recorded part:

* ``anchor`` — ``{kind, commit, dirty}``. ``kind`` records the anchor's
  confidence: ``commit`` (clean tree at record time), ``commit-dirty`` (the
  commit under-describes what was shown), ``time`` (commit chosen by
  ``recorded_at`` when seeding), ``mtime`` (a legacy video inventory),
  ``identified`` (``clm harvest identify-rev``), ``unanchored`` (no commit
  could be captured — the members are the only evidence).
* ``members`` — the recorded language's per-member content fingerprints
  (:func:`clm.slides.doc_identity.content_fingerprint`, keyed by the
  member's rendered :class:`~clm.slides.bilingual_doc.MemberKey`). Stored
  **always**, so a report never depends on git history being present; an
  empty map means the fingerprints could not be computed at record time
  (the deck was not a parseable split pair) and reads as ``unverifiable``.
* ``hash_version`` — the sync ledger's :data:`~clm.slides.doc_ledger.LEDGER_HASH_VERSION`
  (the same fingerprint function). The rule is the sync ledger's: an entry
  under an older version is **never trusted** — it is recomputed from
  ``anchor.commit`` when one exists (:func:`resolve_part_members`), else
  reported ``unverifiable``.

The optional deck-level ``ack`` block is reserved for the ``ack`` verb
(#965): the current fingerprints plus a note, at member granularity.

The ledger is written by the dashboard at record time
(:meth:`clm.recordings.workflow.session.RecordingSession._record_ledger_entry`)
and by the verbs that seed or acknowledge entries. Every course repo
ignores ``.clm/*`` with explicit exceptions, so :func:`ignored_ledger_warning`
is load-bearing: a ledger that silently never gets committed is the
"``.clm/`` gitignore hides ledgers" landmine of the sync ledger's cutover.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from clm.slides.doc_ledger import LEDGER_HASH_VERSION, LEDGER_SUBDIR, deck_key_for

if TYPE_CHECKING:
    from clm.slides.bilingual_doc import BilingualDeck, Lang

__all__ = [
    "HASH_VERSION",
    "LEDGER_FILENAME",
    "SCHEMA_VERSION",
    "AnchorKind",
    "DeckAck",
    "DeckEntry",
    "LedgerError",
    "LedgerPart",
    "RecordingAnchor",
    "RecordingsLedger",
    "anchor_for",
    "deck_key_for",
    "deck_members",
    "deck_members_at_ref",
    "id_order",
    "ignored_ledger_warning",
    "is_git_ignored",
    "ledger_path_for",
    "load",
    "part_identity",
    "record_part",
    "rename_deck_key",
    "resolve_part_members",
    "resolve_part_order",
    "save",
    "split_halves",
]

#: The envelope schema of ``recordings-ledger.json``.
SCHEMA_VERSION = 1

#: The fingerprint function's version — shared with the sync ledger because
#: the recordings ledger stores the very same fingerprints.
HASH_VERSION = LEDGER_HASH_VERSION

LEDGER_FILENAME = "recordings-ledger.json"

AnchorKind = Literal["commit", "commit-dirty", "time", "mtime", "identified", "unanchored"]

#: Member-fingerprint provenance as :func:`resolve_part_members` reports it:
#: ``recorded`` (stored under the current hash version), ``recomputed`` (from a
#: clean anchor commit), ``approximate`` (from a *dirty* anchor commit, which
#: under-describes what was shown), ``unverifiable`` (nothing trustworthy).
MembersStatus = Literal["recorded", "recomputed", "approximate", "unverifiable"]

#: The two sides a recording can show.
RECORDABLE_LANGS: tuple[str, ...] = ("de", "en")


class LedgerError(Exception):
    """A recordings ledger that exists but cannot be read.

    Distinct from the sync ledger's fail-safe-cold degrade: a malformed
    recordings ledger must never be silently overwritten by the next record,
    because its entries are evidence nothing else holds.
    """


class RecordingAnchor(BaseModel):
    """Where in the course history a recording was made, and how sure we are."""

    kind: AnchorKind
    commit: str | None = None
    dirty: bool = False


class LedgerPart(BaseModel):
    """One recorded part of a deck."""

    part: int
    recorded_at: str
    course_id: str
    lang: str
    anchor: RecordingAnchor
    members: dict[str, str] = Field(default_factory=dict)
    #: id-bearing member keys in recorded document order. ``members`` is a
    #: sorted JSON object on disk, so the order the "member order changed"
    #: rule needs must be stored explicitly; empty for entries written before
    #: it existed (the rule is then skipped, never guessed).
    order: list[str] = Field(default_factory=list)
    hash_version: int = HASH_VERSION


class DeckAck(BaseModel):
    """ "Seen, decided not to re-record at these fingerprints" (#965).

    ``members`` and ``order`` are keyed ``"<lang>:<member-key>"`` — one ack
    covers every language the deck entry records.
    """

    at: str
    note: str | None = None
    members: dict[str, str] = Field(default_factory=dict)
    order: list[str] = Field(default_factory=list)
    hash_version: int = HASH_VERSION


class DeckEntry(BaseModel):
    """Every recorded part of one deck plus its optional acknowledgement."""

    parts: list[LedgerPart] = Field(default_factory=list)
    ack: DeckAck | None = None


class RecordingsLedger(BaseModel):
    """The whole ``<topic>/.clm/recordings-ledger.json`` file."""

    model_config = ConfigDict(populate_by_name=True)

    schema_version: int = Field(default=SCHEMA_VERSION, alias="schema")
    hash_version: int = HASH_VERSION
    decks: dict[str, DeckEntry] = Field(default_factory=dict)

    def deck(self, deck_key: str) -> DeckEntry:
        """The entry for *deck_key*, created empty when absent."""
        entry = self.decks.get(deck_key)
        if entry is None:
            entry = DeckEntry()
            self.decks[deck_key] = entry
        return entry


# ---------------------------------------------------------------------------
# Paths and keys
# ---------------------------------------------------------------------------


def ledger_path_for(deck_path: Path) -> Path:
    """The committed ledger path for the topic owning *deck_path*."""
    return deck_path.parent / LEDGER_SUBDIR / LEDGER_FILENAME


def split_halves(deck_path: Path) -> tuple[Path, Path] | None:
    """``(de_path, en_path)`` for a split deck half or stem, **by name only**.

    Unlike :func:`clm.core.slide_text.pairing.derive_split_pair` this does not
    require the twin to exist on disk — the ledger reads decks at historical
    refs where the working tree is irrelevant. ``None`` when *deck_path* is
    a voiceover companion or carries no program extension.
    """
    from clm.core.slide_text.pairing import split_lang_tag

    if deck_path.suffix == "" or deck_path.name.startswith("voiceover_"):
        return None
    ext = deck_path.suffix
    stem = deck_path.name[: -len(ext)]
    if split_lang_tag(deck_path) is not None:
        stem = stem[: -len(".de")]
    return (
        deck_path.with_name(f"{stem}.de{ext}"),
        deck_path.with_name(f"{stem}.en{ext}"),
    )


def id_order(members: dict[str, str]) -> list[str]:
    """The id-bearing keys of a freshly computed member map, in document order.

    Only meaningful on a map straight from :func:`deck_members` /
    :func:`deck_members_at_ref` (insertion order = document order), never on
    one loaded from the sorted JSON.
    """
    return [key for key in members if key.startswith("id:")]


def anchor_for(commit: str | None, dirty: bool) -> RecordingAnchor:
    """The record-time anchor: ``commit`` / ``commit-dirty`` / ``unanchored``."""
    if not commit:
        return RecordingAnchor(kind="unanchored", commit=None, dirty=False)
    return RecordingAnchor(kind="commit-dirty" if dirty else "commit", commit=commit, dirty=dirty)


# ---------------------------------------------------------------------------
# Member fingerprints
# ---------------------------------------------------------------------------


def _check_lang(lang: str) -> Lang:
    """The recorded language must name a side; anything else is a caller bug.

    ``Member.side`` maps every non-``"de"`` value to the EN side, so an
    unvalidated cookie value would store EN fingerprints under a bogus
    ``lang`` and a separate ledger slot.
    """
    if lang not in RECORDABLE_LANGS:
        raise ValueError(
            f"not a recordable language: {lang!r} (expected one of {RECORDABLE_LANGS})"
        )
    return lang  # type: ignore[return-value]


def _members_of(deck: BilingualDeck, lang: Lang) -> dict[str, str]:
    from clm.slides.doc_identity import content_fingerprint

    out: dict[str, str] = {}
    for member in deck.members():
        side = member.side(lang)
        if side is not None:
            out[member.key.render()] = content_fingerprint(side)
    return out


def deck_members(deck_path: Path, lang: str) -> dict[str, str]:
    """The recorded language's member fingerprints of the deck on disk.

    ``{}`` when the deck is not a split pair with an existing twin or the
    bundle fails the normalize precondition — the ledger then records an
    entry without evidence rather than blocking a recording, and the report
    treats it as ``unverifiable``. Raises ``ValueError`` for a *lang* that
    names no side.
    """
    from clm.slides.doc_lenses import DocLensError, load_bundle

    side = _check_lang(lang)
    try:
        bundle = load_bundle(deck_path)
    except (DocLensError, OSError, UnicodeDecodeError) as exc:
        logger.debug("No member fingerprints for {}: {}", deck_path, exc)
        return {}
    if bundle.outcome.deck is None:
        logger.debug("No member fingerprints for {}: bundle refused normalization", deck_path)
        return {}
    return _members_of(bundle.outcome.deck, side)


def deck_members_at_ref(deck_path: Path, ref: str, lang: str) -> dict[str, str] | None:
    """The member fingerprints of the deck as committed at git *ref*.

    ``None`` when git is unavailable, the deck (either half) is absent at the
    ref, or the historical bundle fails to parse — "not recoverable", never a
    guess.
    """
    from clm.core.utils.prog_lang_utils import comment_token_for_path
    from clm.slides.doc_lenses import parse_bundle
    from clm.slides.git_text import bundle_texts_at_ref

    side = _check_lang(lang)
    halves = split_halves(deck_path)
    if halves is None:
        return None
    de_path, en_path = halves
    de_text, en_text, de_comp, en_comp = bundle_texts_at_ref(de_path, en_path, ref)
    if de_text is None or en_text is None:
        return None
    outcome = parse_bundle(
        de_text, en_text, de_comp, en_comp, comment_token=comment_token_for_path(de_path)
    )
    if outcome.deck is None:
        return None
    return _members_of(outcome.deck, side)


def resolve_part_order(
    part: LedgerPart, members: dict[str, str], status: MembersStatus
) -> list[str] | None:
    """The recorded id-member order matching :func:`resolve_part_members`' result.

    Stored order for a ``recorded`` entry (``None`` when the entry predates
    the field), document order of the recomputed map otherwise.
    """
    if status == "recorded":
        return list(part.order) or None
    if status in ("recomputed", "approximate"):
        return id_order(members)
    return None


def resolve_part_members(
    part: LedgerPart, deck_path: Path
) -> tuple[dict[str, str] | None, MembersStatus]:
    """The trustworthy member fingerprints of *part* — the ``hash_version`` rule.

    * recorded under the current :data:`HASH_VERSION` and non-empty →
      ``(members, "recorded")``;
    * otherwise, with a commit anchor → recomputed from the deck at that
      commit: ``(members, "recomputed")`` for a clean anchor, ``(members,
      "approximate")`` for a ``commit-dirty`` one (the commit under-describes
      what was shown; the report must say so), ``None`` when the commit no
      longer resolves;
    * otherwise ``(None, "unverifiable")`` — never a hash a different
      fingerprint function would compute differently.
    """
    if part.hash_version == HASH_VERSION and part.members:
        return dict(part.members), "recorded"
    if part.anchor.commit:
        recomputed = deck_members_at_ref(deck_path, part.anchor.commit, part.lang)
        if recomputed:
            return recomputed, ("approximate" if part.anchor.dirty else "recomputed")
    return None, "unverifiable"


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def load(path: Path) -> RecordingsLedger:
    """Read a recordings ledger; an absent file is an empty ledger.

    Raises :class:`LedgerError` for a file that exists but is not a readable
    ledger — the caller decides whether to warn and skip; nothing here
    overwrites evidence.
    """
    if not path.is_file():
        return RecordingsLedger()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LedgerError(f"cannot read recordings ledger {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise LedgerError(f"recordings ledger {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise LedgerError(f"recordings ledger {path} is not a JSON object")
    schema = data.get("schema")
    if schema != SCHEMA_VERSION:
        raise LedgerError(
            f"recordings ledger {path} has schema {schema!r}; this clm reads schema {SCHEMA_VERSION}"
        )
    try:
        _backfill_hash_versions(data)
        return RecordingsLedger.model_validate(data)
    except (ValueError, TypeError, AttributeError) as exc:
        raise LedgerError(f"recordings ledger {path} is malformed: {exc}") from exc


def _backfill_hash_versions(data: dict) -> None:
    """Give every part/ack without its own ``hash_version`` the envelope's.

    An older writer that only stamped the envelope must not have its entries
    promoted to the model default (the *current* version) by validation.
    Shape errors propagate to :func:`load`, which reports them as
    :class:`LedgerError`.
    """
    raw_version = data.get("hash_version", 0)
    if isinstance(raw_version, bool) or not isinstance(raw_version, int):
        raise ValueError(f"hash_version must be an integer, got {raw_version!r}")
    decks = data.get("decks") or {}
    if not isinstance(decks, dict):
        raise TypeError("decks must be an object")
    for raw_deck in decks.values():
        if not isinstance(raw_deck, dict):
            raise TypeError("each deck entry must be an object")
        parts = raw_deck.get("parts") or []
        if not isinstance(parts, list):
            raise TypeError("parts must be a list")
        for raw_part in parts:
            if isinstance(raw_part, dict):
                raw_part.setdefault("hash_version", raw_version)
        ack = raw_deck.get("ack")
        if isinstance(ack, dict):
            ack.setdefault("hash_version", raw_version)


def _to_json(ledger: RecordingsLedger) -> bytes:
    """Canonical serialization: sorted keys, two-space indent, trailing newline."""
    for deck in ledger.decks.values():
        deck.parts.sort(key=lambda p: (p.part, p.lang, p.course_id, p.recorded_at))
    payload = ledger.model_dump(by_alias=True, mode="json")
    payload["schema"] = SCHEMA_VERSION
    payload["hash_version"] = HASH_VERSION
    return (json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )


def save(ledger: RecordingsLedger, path: Path) -> bool:
    """Write *ledger* atomically as canonical JSON, creating ``.clm/``.

    Returns ``False`` without touching the file when the canonical bytes are
    already on disk, so a no-op sweep is write-free.
    """
    from clm.infrastructure.utils.path_utils import atomic_write_bytes

    payload = _to_json(ledger)
    current: bytes | None = None
    if path.is_file():
        try:
            current = path.read_bytes()
        except OSError:
            current = None
    if current == payload:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, payload)
    return True


def part_identity(part: LedgerPart) -> tuple[str, int, str]:
    """The upsert key of a part entry: ``(course_id, part, lang)``.

    One course id is one cohort's recording run; a second cohort re-recording
    the same deck part must not erase the first cohort's provenance — its
    videos still ship to that cohort.
    """
    return part.course_id, part.part, part.lang


def record_part(ledger_path: Path, deck_key: str, part: LedgerPart) -> bool:
    """Load–upsert–save one part entry: the record-time write path.

    An existing entry with the same :func:`part_identity` is replaced (a
    retake supersedes the take it replaced — the state file keeps the take
    history); entries for other parts, languages and course ids are
    untouched.
    """
    ledger = load(ledger_path)
    entry = ledger.deck(deck_key)
    key = part_identity(part)
    entry.parts = [p for p in entry.parts if part_identity(p) != key]
    entry.parts.append(part)
    return save(ledger, ledger_path)


def rename_deck_key(path: Path, old_key: str, new_key: str) -> bool:
    """Re-key one deck's entry ``old_key`` → ``new_key`` in the ledger at *path*.

    The hook ``clm slides rename`` runs beside the sync ledger's re-key
    (#1007): a renamed deck keeps its recorded parts and its ack instead of
    reporting ``orphaned``. Returns ``False`` when the ledger or the entry
    is absent; raises ``ValueError`` when *new_key* already has an entry
    (re-keying onto it would merge two decks' recordings) or the ledger
    cannot be read.
    """
    if not path.is_file():
        return False
    try:
        ledger = load(path)
    except LedgerError as exc:
        raise ValueError(str(exc)) from None
    entry = ledger.decks.pop(old_key, None)
    if entry is None:
        return False
    if new_key in ledger.decks:
        raise ValueError(f"recordings ledger {path} already holds an entry for {new_key!r}")
    ledger.decks[new_key] = entry
    save(ledger, path)
    return True


# ---------------------------------------------------------------------------
# The gitignore landmine
# ---------------------------------------------------------------------------


def is_git_ignored(path: Path) -> bool | None:
    """Whether git would ignore *path* (``git check-ignore``).

    ``None`` when git is unavailable or *path* is not inside a repository.
    A file that is already tracked is not reported as ignored, so a
    committed ledger never warns.
    """
    try:
        completed = subprocess.run(
            ["git", "check-ignore", "-q", "--", path.name],
            cwd=str(path.parent),
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, OSError):
        return None
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    return None


def ignored_ledger_warning(path: Path) -> str | None:
    """The warning to surface when the ledger at *path* is gitignored, else ``None``.

    Course repos ignore ``.clm/*`` with explicit exceptions; the fix is one
    more exception line.
    """
    if is_git_ignored(path):
        return (
            f"recordings ledger {path} is ignored by git and will not be committed; "
            f"add `!**/{LEDGER_SUBDIR}/{LEDGER_FILENAME}` to the course repo's .gitignore"
        )
    return None
