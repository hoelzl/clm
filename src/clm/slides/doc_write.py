"""Sync-free write surface for the v3 deck model (#546 Phase 1).

The emission and file-write half of the apply executor
(:mod:`clm.slides.doc_apply`), carved out so any consumer of the
:class:`~clm.slides.bilingual_doc.BilingualDeck` model — the sync executor
today, the ``clm harvest`` accept verb next — can mutate members in memory
and land the result on disk without importing the differ or the ledger.

The contract ("given a ``BilingualDeck`` + member edits, emit and atomically
write the ≤4 files"):

* :class:`DeckEmitter` holds a per-``(lang, part)`` cell-stream view of the
  parsed deck; unmutated cells re-emit their verbatim bytes (the lens
  guarantee lifted into the writer — emission is preamble + concatenated
  cell lines, exactly what :func:`~clm.slides.doc_lenses.project` produces).
* Callers snapshot :meth:`DeckEmitter.emit_all` before mutating, mutate
  through :meth:`~DeckEmitter.set_side` / :meth:`~DeckEmitter.stream_remove`
  / :meth:`~DeckEmitter.insert_mirrored`, emit again, and re-parse the
  mutated bundle (:func:`~clm.slides.doc_lenses.parse_bundle`) before
  anything touches disk — a writer must never write a bundle the lens
  cannot read back.
* :func:`write_changed_files` lands the changed emissions through
  :func:`~clm.infrastructure.utils.path_utils.atomic_write_all` (≤4 files
  per deck), minting a companion path for a file the bundle did not have.

This module must not import from the v2 sync core (``sync_plan`` /
``sync_apply`` / ``sync_code``) nor from the v3 differ/ledger
(``sync_diff`` / ``doc_ledger``) — enforced by the import-cleanliness
tests (design §12.5).
"""

from __future__ import annotations

from pathlib import Path

from attrs import define, field

from clm.slides.bilingual_doc import BilingualDeck, Lang, Member, SideCell
from clm.slides.doc_lenses import LoadedBundle

__all__ = [
    "DeckEmitter",
    "DeckWriteError",
    "new_companion_path",
    "write_changed_files",
]

_SIDES: tuple[Lang, Lang] = ("de", "en")


class DeckWriteError(Exception):
    """A deck mutation/emission failure the caller must handle per item."""


def _other(lang: Lang) -> Lang:
    return "en" if lang == "de" else "de"


@define
class DeckEmitter:
    """A mutable per-``(lang, part)`` cell-stream view of a parsed deck."""

    deck: BilingualDeck

    streams: dict[tuple[Lang, str], list[Member]] = field(factory=dict)
    preambles: dict[tuple[Lang, str], tuple[str, ...] | None] = field(factory=dict)
    mutated: bool = False

    def __attrs_post_init__(self) -> None:
        order: dict[tuple[Lang, str], list[tuple[int, Member]]] = {}
        for member in self.deck.members():
            for lang in _SIDES:
                cell = member.side(lang)
                if cell is not None:
                    order.setdefault((lang, cell.part), []).append((cell.index, member))
        self.streams = {
            key: [m for _, m in sorted(entries, key=lambda e: e[0])]
            for key, entries in order.items()
        }
        for lang in _SIDES:
            self.streams.setdefault((lang, "deck"), [])
        self.preambles = {
            ("de", "deck"): self.deck.de_deck_preamble,
            ("en", "deck"): self.deck.en_deck_preamble,
            ("de", "companion"): self.deck.de_companion_preamble,
            ("en", "companion"): self.deck.en_companion_preamble,
        }

    # -- emission -----------------------------------------------------------

    def emit(self, lang: Lang, part: str) -> str | None:
        preamble = self.preambles.get((lang, part))
        members = self.streams.get((lang, part), [])
        cells = [c for m in members if (c := m.side(lang)) is not None and c.part == part]
        if preamble is None:
            if not cells:
                return None
            # A newly created file mirrors its twin's preamble (the jupytext
            # header block is language-neutral) rather than starting bare.
            twin_preamble = self.preambles.get((_other(lang), part))
            preamble = twin_preamble if twin_preamble is not None else ()
        lines = list(preamble)
        for cell in cells:
            lines.extend(cell.lines)
        return "\n".join(lines)

    def emit_all(self) -> dict[tuple[Lang, str], str | None]:
        return {
            (lang, part): self.emit(lang, part) for lang in _SIDES for part in ("deck", "companion")
        }

    # -- stream plumbing ------------------------------------------------------

    def set_side(self, member: Member, lang: Lang, cell: SideCell | None) -> None:
        if lang == "de":
            member.de = cell
        else:
            member.en = cell
        self.mutated = True

    def stream_remove(self, lang: Lang, part: str, member: Member) -> None:
        stream = self.streams.get((lang, part), [])
        for i, m in enumerate(stream):
            if m is member:
                del stream[i]
                return

    def insert_mirrored(
        self, member: Member, source: Lang, target: Lang, part: str, new_cell: SideCell
    ) -> None:
        """Insert ``member``'s new ``target`` cell after the mirrored predecessor.

        Walk backwards from the member's position in the *source* stream to
        the nearest member that also has a cell in the target stream; insert
        right after it (or at the top of the file when none precedes it).
        """
        source_stream = self.streams.get((source, part), [])
        target_stream = self.streams.setdefault((target, part), [])
        try:
            pos = next(i for i, m in enumerate(source_stream) if m is member)
        except StopIteration:
            raise DeckWriteError("the source cell is not in its own stream (writer bug)") from None
        insert_at = 0
        for prev in reversed(source_stream[:pos]):
            for j, m in enumerate(target_stream):
                if m is prev:
                    insert_at = j + 1
                    break
            if insert_at:
                break
        target_stream.insert(insert_at, member)
        self.set_side(member, target, new_cell)


def new_companion_path(bundle: LoadedBundle, lang: Lang) -> Path:
    """Where a newly created companion file goes (standard subdir layout)."""
    from clm.slides.voiceover_tools import COMPANION_SUBDIR, companion_name

    deck_path = bundle.de_path if lang == "de" else bundle.en_path
    return deck_path.parent / COMPANION_SUBDIR / companion_name(deck_path)


def write_changed_files(
    bundle: LoadedBundle,
    finals: dict[tuple[Lang, str], str | None],
    changed: set[tuple[Lang, str]],
) -> list[Path]:
    """Atomically land the ``changed`` emissions of a bundle on disk.

    A ``None`` emission is skipped — a writer never deletes a file; layout
    removals empty it instead. A changed file the bundle has no path for
    (a freshly minted companion) goes to :func:`new_companion_path`.
    Raises :class:`OSError` on write failure; nothing is partially written
    (the ``atomic_write_all`` boundary ``split``/``unify`` also use).
    """
    from clm.infrastructure.utils.path_utils import atomic_write_all

    writes: list[tuple[Path, str]] = []
    paths: dict[tuple[Lang, str], Path | None] = {
        ("de", "deck"): bundle.de_path,
        ("en", "deck"): bundle.en_path,
        ("de", "companion"): bundle.de_companion_path,
        ("en", "companion"): bundle.en_companion_path,
    }
    for file_key in sorted(changed, key=str):
        text = finals[file_key]
        path = paths.get(file_key)
        if text is None:
            continue  # never delete a file; layout removals empty it instead
        if path is None:
            path = new_companion_path(bundle, file_key[0])
        writes.append((path, text))
    for path, _text in writes:
        path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_all(writes)
    return [p for p, _ in writes]
