"""The re-recording backlog: severity from the source diff (#965, #907 §4.2–4.3).

Reads every committed recordings ledger under a root
(:mod:`clm.recordings.ledger`), parses each recorded deck as it is **now** in
the working tree, and compares the ledger's per-member fingerprints with the
deck's current ones. No build, no video, no model — and **no re-record
judgment**: the classes below are the evidence in the tracker's own
vocabulary; the author decides, and :func:`acknowledge` banks the decision at
member granularity.

Severity classes, highest wins (design §4.2):

* ``structural`` — an id-bearing member was added or removed, the id-bearing
  member order changed, or a **code** member's fingerprint changed;
* ``visible`` — a presentation (markdown / j2) member's fingerprint changed;
* ``narration`` — only voiceover / companion members changed;
* ``notes`` — only trainer-notes members changed;
* ``none`` — every member matches.

A part whose fingerprints cannot be trusted (:func:`~clm.recordings.ledger.resolve_part_members`
→ ``unverifiable``) reports ``unverifiable`` instead of a class.

Acknowledgement (design §4.3): ``ack`` stores the current fingerprints of
every recorded language on the deck entry, keyed ``"<lang>:<member-key>"``.
A deck is ``acknowledged`` while the working tree still matches those
fingerprints, ``drifted-since-ack`` (with the severity of the drift *since
the ack*) once it moves further, and ``unacknowledged`` without an ack. An
ack recorded under an older ``hash_version`` is stale and reads as
``unacknowledged``.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from attrs import define, field

from clm.core.utils.path_utils import SUPPORTED_PROG_LANG_EXTENSIONS
from clm.recordings import ledger as rl

if TYPE_CHECKING:
    from clm.recordings.state import CourseRecordingState

#: ``course_id -> state`` for the secondary flag; ``None`` when a state file is absent.
StateLookup = Callable[[str], "CourseRecordingState | None"]

__all__ = [
    "SEVERITIES",
    "AckState",
    "DeckDiff",
    "DeckReport",
    "MemberInfo",
    "PartReport",
    "Report",
    "Severity",
    "acknowledge",
    "build_report",
    "deck_member_index",
    "diff_members",
    "find_deck_files",
    "find_ledgers",
    "report_to_dict",
]

Severity = Literal["none", "notes", "narration", "visible", "structural", "unverifiable"]

#: Ascending order; ``max`` over a deck's members / parts picks the row's class.
SEVERITIES: tuple[str, ...] = ("none", "notes", "narration", "visible", "structural")

AckState = Literal["unacknowledged", "acknowledged", "drifted-since-ack", "stale-ack"]


def _rank(severity: str) -> int:
    return SEVERITIES.index(severity) if severity in SEVERITIES else len(SEVERITIES)


def _max_severity(values: list[str]) -> str:
    if not values:
        return "none"
    if any(v == "unverifiable" for v in values) and all(
        v in ("unverifiable", "none") for v in values
    ):
        return "unverifiable"
    ranked = [v for v in values if v in SEVERITIES]
    return max(ranked, key=_rank) if ranked else "none"


# ---------------------------------------------------------------------------
# The deck as it is now
# ---------------------------------------------------------------------------


@define(frozen=True)
class MemberInfo:
    """One member of the working-tree deck: fingerprint plus what kind of thing it is."""

    fp: str
    kind: str  # markdown | code | j2
    role: str  # header | slide | subslide | voiceover | notes | code | aux
    layout: str  # inline | companion


def deck_member_index(deck_path: Path, lang: str) -> dict[str, MemberInfo] | None:
    """The recorded language's members of the deck on disk, with kinds.

    ``None`` when the deck is not a parseable split pair — the report then
    cannot classify (``unverifiable``), never guesses.
    """
    from clm.slides.doc_identity import content_fingerprint
    from clm.slides.doc_lenses import DocLensError, load_bundle

    if rl.is_unsplit_deck(deck_path):
        return _unsplit_member_index(deck_path, lang)
    try:
        bundle = load_bundle(deck_path)
    except (DocLensError, OSError, UnicodeDecodeError):
        return None
    deck = bundle.outcome.deck
    if deck is None:
        return None
    out: dict[str, MemberInfo] = {}
    for member in deck.members():
        side = member.side(lang)  # type: ignore[arg-type]
        if side is None:
            continue
        out[member.key.render()] = MemberInfo(
            fp=content_fingerprint(side),
            kind=member.kind,
            role=member.role,
            layout=member.layout,
        )
    return out


def _unsplit_member_index(deck_path: Path, lang: str) -> dict[str, MemberInfo] | None:
    """The single-file bilingual deck's members with kinds (see :func:`rl.unsplit_members`)."""
    from clm.core.slide_text.slide_parser import parse_cells
    from clm.core.utils.prog_lang_utils import comment_token_for_path

    try:
        text = deck_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    fingerprints = rl.unsplit_members(text, lang, comment_token_for_path(deck_path))
    out: dict[str, MemberInfo] = {}
    ordinals: dict[str, int] = {}
    for cell in parse_cells(text, comment_token_for_path(deck_path)):
        meta = cell.metadata
        if meta.lang not in (None, lang):
            continue
        kind = meta.cell_type if meta.cell_type in ("code", "j2") else "markdown"
        if meta.slide_id:
            key = f"id:{meta.slide_id}"
        else:
            ordinal = ordinals.get(kind, 0)
            ordinals[kind] = ordinal + 1
            key = f"cell:{kind}/{ordinal}"
        if "voiceover" in meta.tags:
            role = "voiceover"
        elif "notes" in meta.tags:
            role = "notes"
        elif "subslide" in meta.tags:
            role = "subslide"
        elif "slide" in meta.tags:
            role = "slide"
        elif kind == "j2":
            role = "header"
        else:
            role = "code" if kind == "code" else "aux"
        out[key] = MemberInfo(fp=fingerprints[key], kind=kind, role=role, layout="inline")
    return out


# ---------------------------------------------------------------------------
# The member diff
# ---------------------------------------------------------------------------


@define
class DeckDiff:
    """Ledger members versus the working tree for one recorded part."""

    severity: str
    changed: int
    total: int
    #: ``{member_key: class}`` for every member that differs
    members: dict[str, str] = field(factory=dict)
    added: list[str] = field(factory=list)
    removed: list[str] = field(factory=list)
    reordered: bool = False


def _class_of(info: MemberInfo) -> str:
    if info.kind == "code":
        return "structural"
    if info.layout == "companion" or info.role == "voiceover":
        return "narration"
    if info.role == "notes":
        return "notes"
    return "visible"


def _class_from_key(key: str) -> str:
    """Class for a member only one side has, from its handle alone.

    ``pos:<group>/<kind>/<n>`` reveals the kind-class; an ``id:`` member that
    vanished or appeared is structural by rule.
    """
    scheme, _, value = key.partition(":")
    if scheme == "pos":
        parts = value.rsplit("/", 2)
        if len(parts) == 3 and parts[1] == "code":
            return "structural"
        return "visible"
    if scheme == "cell":
        return "structural" if value.startswith("code/") else "visible"
    return "structural"


def diff_members(
    base: dict[str, str], head: dict[str, MemberInfo], base_order: list[str] | None = None
) -> DeckDiff:
    """Classify the drift between recorded fingerprints and the current deck.

    *base_order* is the recorded id-member order (``LedgerPart.order``);
    without it the "member order changed" rule is skipped — ``base`` itself
    comes from sorted JSON and carries no order.
    """
    base_keys = list(base)
    head_keys = list(head)
    members: dict[str, str] = {}

    added = [k for k in head_keys if k not in base]
    removed = [k for k in base_keys if k not in head]
    for key in added:
        members[key] = _class_from_key(key) if key.startswith("id:") else _class_of(head[key])
    for key in removed:
        members[key] = _class_from_key(key)
    for key in base_keys:
        info = head.get(key)
        if info is not None and info.fp != base[key]:
            members[key] = _class_of(info)

    reordered = False
    if base_order:
        recorded_order = [k for k in base_order if k in head]
        current_order = [k for k in head_keys if k in recorded_order]
        reordered = recorded_order != current_order

    classes = list(members.values())
    if reordered:
        classes.append("structural")
    return DeckDiff(
        severity=_max_severity(classes),
        changed=len(members),
        total=len(set(base_keys) | set(head_keys)),
        members=members,
        added=added,
        removed=removed,
        reordered=reordered,
    )


# ---------------------------------------------------------------------------
# Locating ledgers and decks
# ---------------------------------------------------------------------------


def find_ledgers(root: Path) -> list[Path]:
    """Every ``.clm/recordings-ledger.json`` at or under *root* (pruned like the course scan)."""
    from clm.core.utils.path_utils import is_ignored_dir_for_course, is_private_dir_name

    root = root.resolve()
    if root.is_file():
        candidate = rl.ledger_path_for(root)
        return [candidate] if candidate.is_file() else []
    if (root / rl.LEDGER_SUBDIR / rl.LEDGER_FILENAME).is_file() and root.name != rl.LEDGER_SUBDIR:
        return [root / rl.LEDGER_SUBDIR / rl.LEDGER_FILENAME]
    found: list[Path] = []
    for path in sorted(root.rglob(rl.LEDGER_FILENAME)):
        if path.parent.name != rl.LEDGER_SUBDIR:
            continue
        rel = path.parent.parent.relative_to(root)
        if is_ignored_dir_for_course(rel) or any(is_private_dir_name(p) for p in rel.parts):
            continue
        found.append(path)
    return found


def find_deck_files(topic_dir: Path, deck_key: str) -> dict[str, Path]:
    """``{lang: half}`` for the split deck *deck_key* in *topic_dir* (empty = orphaned)."""
    out: dict[str, Path] = {}
    for ext in sorted(SUPPORTED_PROG_LANG_EXTENSIONS):
        for lang in rl.RECORDABLE_LANGS:
            candidate = topic_dir / f"{deck_key}.{lang}{ext}"
            if candidate.is_file() and lang not in out:
                out[lang] = candidate
        if out:
            break
        unsplit = topic_dir / f"{deck_key}{ext}"
        if unsplit.is_file():
            # One file serves both languages.
            return dict.fromkeys(rl.RECORDABLE_LANGS, unsplit)
    return out


def _commits_since(anchor: str, paths: list[Path]) -> list[str] | None:
    """Commits after *anchor* that touched any of *paths* (newest first), or ``None``.

    Pathspecs are given relative to the topic directory the command runs
    in, so a companion under ``voiceover/`` is matched (a bare file name
    would silently miss it).
    """
    if not paths:
        return None
    cwd = paths[0].parent
    specs: list[str] = []
    for p in paths:
        try:
            specs.append(p.resolve().relative_to(cwd.resolve()).as_posix())
        except ValueError:
            specs.append(p.resolve().as_posix())
    try:
        completed = subprocess.run(
            ["git", "log", "--format=%H", f"{anchor}..HEAD", "--", *specs],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except (FileNotFoundError, OSError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.split()


def _bundle_paths(deck_files: dict[str, Path]) -> list[Path]:
    from clm.core.voiceover_companions import companion_locations

    paths: list[Path] = []
    for half in deck_files.values():
        paths.append(half)
        paths.extend(companion_locations(half))
    return paths


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


@define
class PartReport:
    course_id: str
    part: int
    lang: str
    recorded_at: str
    anchor: dict[str, Any]
    members_status: str
    severity: str
    changed: int
    total: int
    changed_members: dict[str, str] = field(factory=dict)
    commits_since_anchor: list[str] | None = None
    built_output_changed: bool | None = None


@define
class DeckReport:
    deck: str
    topic_dir: str
    status: Literal["recorded", "orphaned", "unrecorded"]
    severity: str
    ack_state: AckState
    deck_files: dict[str, str] = field(factory=dict)
    parts: list[PartReport] = field(factory=list)
    ack_at: str | None = None
    ack_note: str | None = None
    severity_since_ack: str | None = None

    @property
    def needs_attention(self) -> bool:
        if self.status == "orphaned":
            return True
        if self.status == "unrecorded":
            return False
        if self.ack_state == "acknowledged":
            return False
        if self.ack_state == "drifted-since-ack":
            return self.severity_since_ack not in (None, "none")
        return self.severity != "none"


@define
class Report:
    root: str
    decks: list[DeckReport] = field(factory=list)
    ledger_errors: list[str] = field(factory=list)

    @property
    def is_clean(self) -> bool:
        return not self.ledger_errors and not any(d.needs_attention for d in self.decks)


def _ack_map(ack: rl.DeckAck, lang: str) -> tuple[dict[str, str], list[str]]:
    prefix = f"{lang}:"
    members = {k[len(prefix) :]: v for k, v in ack.members.items() if k.startswith(prefix)}
    order = [k[len(prefix) :] for k in ack.order if k.startswith(prefix)]
    return members, order


def _state_part_for(
    state: CourseRecordingState | None, part: rl.LedgerPart
) -> tuple[str | None, str | None]:
    """``(topic_id, slide_digest)`` of the local-state part that shares the ledger part's stamp."""
    if state is None:
        return None, None
    for lecture in state.lectures:
        for sp in lecture.parts:
            if sp.part == part.part and sp.recorded_at == part.recorded_at:
                return sp.topic_id, sp.slide_digest
    return None, None


def _built_output_changed(
    manifest: dict[str, Any] | None,
    state: CourseRecordingState | None,
    part: rl.LedgerPart,
) -> bool | None:
    if manifest is None:
        return None
    from clm.core.provenance_manifest import manifest_topic_digest

    topic_id, recorded = _state_part_for(state, part)
    if not topic_id or not recorded:
        return None
    current = manifest_topic_digest(manifest, topic_id)
    if current is None:
        return None
    return current != recorded


def _safe_state(lookup: StateLookup | None, course_id: str) -> CourseRecordingState | None:
    """A local state file is a hint for the secondary flag, never a reason to fail."""
    if lookup is None:
        return None
    try:
        return lookup(course_id)
    except (OSError, ValueError):
        return None


def _report_deck(
    ledger_path: Path,
    deck_key: str,
    entry: rl.DeckEntry,
    *,
    root: Path,
    manifest: dict[str, Any] | None,
    state_lookup: StateLookup | None,
    state_cache: dict[str, CourseRecordingState | None],
) -> DeckReport:
    topic_dir = ledger_path.parent.parent
    deck_files = find_deck_files(topic_dir, deck_key)
    rel_topic = _rel(topic_dir, root)
    if not deck_files:
        return DeckReport(
            deck=deck_key,
            topic_dir=rel_topic,
            status="orphaned",
            severity="unverifiable",
            ack_state="unacknowledged",
            parts=[
                PartReport(
                    course_id=p.course_id,
                    part=p.part,
                    lang=p.lang,
                    recorded_at=p.recorded_at,
                    anchor=p.anchor.model_dump(),
                    members_status="unverifiable",
                    severity="unverifiable",
                    changed=0,
                    total=len(p.members),
                )
                for p in entry.parts
            ],
        )

    any_half = next(iter(deck_files.values()))
    bundle_paths = _bundle_paths(deck_files)
    head_by_lang: dict[str, dict[str, MemberInfo] | None] = {}
    commits_by_anchor: dict[str, list[str] | None] = {}
    parts: list[PartReport] = []
    for part in sorted(entry.parts, key=rl.part_identity):
        status: rl.MembersStatus
        if part.lang not in rl.RECORDABLE_LANGS:
            # A hand-edited or foreign entry: report it, never let it abort the run.
            head, base, status = None, None, "unverifiable"
        else:
            if part.lang not in head_by_lang:
                head_by_lang[part.lang] = deck_member_index(any_half, part.lang)
            head = head_by_lang[part.lang]
            base, status = rl.resolve_part_members(part, any_half)
        if base is None or head is None:
            severity, changed, total, changed_members = "unverifiable", 0, len(part.members), {}
        else:
            diff = diff_members(base, head, rl.resolve_part_order(part, base, status))
            severity, changed, total = diff.severity, diff.changed, diff.total
            changed_members = diff.members
        commits: list[str] | None = None
        if part.anchor.commit:
            if part.anchor.commit not in commits_by_anchor:
                commits_by_anchor[part.anchor.commit] = _commits_since(
                    part.anchor.commit, bundle_paths
                )
            commits = commits_by_anchor[part.anchor.commit]
        if manifest is not None and part.course_id not in state_cache:
            state_cache[part.course_id] = _safe_state(state_lookup, part.course_id)
        parts.append(
            PartReport(
                course_id=part.course_id,
                part=part.part,
                lang=part.lang,
                recorded_at=part.recorded_at,
                anchor=part.anchor.model_dump(),
                members_status=status,
                severity=severity,
                changed=changed,
                total=total,
                changed_members=changed_members,
                commits_since_anchor=commits,
                built_output_changed=_built_output_changed(
                    manifest, state_cache.get(part.course_id), part
                ),
            )
        )

    ack_state: AckState = "unacknowledged"
    severity_since_ack: str | None = None
    ack = entry.ack
    if ack is not None:
        if ack.hash_version != rl.HASH_VERSION:
            ack_state = "stale-ack"
        elif any(not _ack_map(ack, lang)[0] for lang in head_by_lang):
            # A language recorded after the ack was written is not covered by
            # it: the deck is unacknowledged again (re-run `ack`), not drifted.
            ack_state = "unacknowledged"
        else:
            since: list[str] = []
            for lang, head in head_by_lang.items():
                if head is None:
                    since.append("unverifiable")
                    continue
                ack_members, ack_order = _ack_map(ack, lang)
                since.append(diff_members(ack_members, head, ack_order).severity)
            severity_since_ack = _max_severity(since) if since else "none"
            ack_state = "acknowledged" if severity_since_ack == "none" else "drifted-since-ack"

    return DeckReport(
        deck=deck_key,
        topic_dir=rel_topic,
        status="recorded",
        severity=_max_severity([p.severity for p in parts]),
        ack_state=ack_state,
        deck_files={lang: _rel(path, root) for lang, path in deck_files.items()},
        parts=parts,
        ack_at=ack.at if ack else None,
        ack_note=ack.note if ack else None,
        severity_since_ack=severity_since_ack,
    )


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _unrecorded_decks(root: Path, recorded: set[tuple[Path, str]]) -> list[DeckReport]:
    from clm.core.slide_text.pairing import find_split_slide_files_recursive, iter_split_pairs

    pairs, _solo = iter_split_pairs(find_split_slide_files_recursive(root))
    out: list[DeckReport] = []
    for de_path, en_path in pairs:
        key = (de_path.parent.resolve(), rl.deck_key_for(de_path))
        if key in recorded:
            continue
        out.append(
            DeckReport(
                deck=key[1],
                topic_dir=_rel(de_path.parent, root),
                status="unrecorded",
                severity="none",
                ack_state="unacknowledged",
                deck_files={"de": _rel(de_path, root), "en": _rel(en_path, root)},
            )
        )
    return out


def build_report(
    root: Path,
    *,
    include_unrecorded: bool = False,
    manifest: dict[str, Any] | None = None,
    state_lookup: StateLookup | None = None,
) -> Report:
    """The backlog for every ledger under *root* (a course root, topic dir or deck file).

    *state_lookup* (``course_id -> CourseRecordingState | None``) and
    *manifest* together enable the secondary ``built_output_changed`` flag;
    both are optional and never prerequisites.
    """
    root = root.resolve()
    report = Report(root=root.as_posix())
    recorded: set[tuple[Path, str]] = set()
    state_cache: dict[str, CourseRecordingState | None] = {}
    only_deck: str | None = rl.deck_key_for(root) if root.is_file() else None
    scan_root = root.parent if root.is_file() else root

    for ledger_path in find_ledgers(root):
        try:
            ledger = rl.load(ledger_path)
        except rl.LedgerError as exc:
            report.ledger_errors.append(str(exc))
            continue
        for deck_key in sorted(ledger.decks):
            if only_deck is not None and deck_key != only_deck:
                continue
            entry = ledger.decks[deck_key]
            if not entry.parts:
                continue
            recorded.add((ledger_path.parent.parent.resolve(), deck_key))
            report.decks.append(
                _report_deck(
                    ledger_path,
                    deck_key,
                    entry,
                    root=scan_root,
                    manifest=manifest,
                    state_lookup=state_lookup,
                    state_cache=state_cache,
                )
            )

    if include_unrecorded and not root.is_file():
        report.decks.extend(_unrecorded_decks(root, recorded))
    report.decks.sort(key=lambda d: (d.topic_dir, d.deck))
    return report


def report_to_dict(report: Report) -> dict[str, Any]:
    """The ``--json`` envelope (identity head first, per ``clm info agent-tasks``)."""
    from attrs import asdict

    counts: dict[str, int] = {}
    for deck in report.decks:
        bucket = deck.status if deck.status != "recorded" else deck.severity
        counts[bucket] = counts.get(bucket, 0) + 1
    return {
        "schema": 1,
        "tool": "recordings",
        "verb": "report",
        "root": report.root,
        "is_clean": report.is_clean,
        "needs_attention": [f"{d.topic_dir}/{d.deck}" for d in report.decks if d.needs_attention],
        "counts": counts,
        "ledger_errors": list(report.ledger_errors),
        "decks": [{**asdict(d), "needs_attention": d.needs_attention} for d in report.decks],
    }


# ---------------------------------------------------------------------------
# Acknowledgement
# ---------------------------------------------------------------------------


@define
class AckResult:
    ledger_path: Path
    deck: str
    langs: list[str]
    member_count: int
    written: bool


def acknowledge(deck_path: Path, *, note: str | None = None) -> AckResult:
    """Bank "seen, decided not to re-record" at the deck's current fingerprints.

    Stores the working-tree fingerprints of every language the deck's ledger
    entry records, keyed ``"<lang>:<member-key>"``, plus *note* and the
    timestamp. Raises ``LookupError`` when the deck has no recorded part (an
    ack without a recording acknowledges nothing) and ``ValueError`` when the
    deck cannot be fingerprinted.
    """
    from datetime import datetime

    deck_path = deck_path.resolve()
    ledger_path = rl.ledger_path_for(deck_path)
    deck_key = rl.deck_key_for(deck_path)
    ledger = rl.load(ledger_path)
    entry = ledger.decks.get(deck_key)
    if entry is None or not entry.parts:
        raise LookupError(f"no recorded part for deck {deck_key!r} in {ledger_path}")
    langs = sorted({p.lang for p in entry.parts})
    members: dict[str, str] = {}
    order: list[str] = []
    for lang in langs:
        current = rl.deck_members(deck_path, lang)
        if not current:
            raise ValueError(f"cannot fingerprint {deck_path} for lang={lang!r}")
        members.update({f"{lang}:{k}": v for k, v in current.items()})
        order.extend(f"{lang}:{k}" for k in rl.id_order(current))
    entry.ack = rl.DeckAck(
        at=datetime.now().isoformat(timespec="seconds"),
        note=note,
        members=members,
        order=order,
        hash_version=rl.HASH_VERSION,
    )
    written = rl.save(ledger, ledger_path)
    return AckResult(
        ledger_path=ledger_path,
        deck=deck_key,
        langs=langs,
        member_count=len(members),
        written=written,
    )
