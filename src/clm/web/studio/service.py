"""StudioService — the desktop-side engine behind the Studio API.

One instance is bound to a single course (a spec + its ``slides/`` dir) per
``clm serve`` instance. It exposes navigation (tree / recents / orphans),
search, deck open (read-only render), and the **concurrency core**: cell
edits routed through the byte-exact :class:`FileState` write-back engine,
guarded by optimistic ``deck_version`` + ``cell_hash`` checks.

Identity rules (the keystone — see design §3.6 / §9.1):

* Cells are addressed by ``(slide_id, role)`` — **never** by index. An
  insert/delete/reorder on the desktop side shifts indices; index-keyed
  writes would then silently clobber the wrong cell.
* Every write carries the ``deck_version`` (whole-file hash) and
  ``cell_hash`` (target body hash) the phone last saw. A mismatch raises a
  stale error (HTTP 409 at the route layer) instead of overwriting.
* All writes go through :class:`FileState` (one write path — design §9.2);
  untouched cells are left byte-for-byte unchanged.

P1 only edits cells already addressable by ``(slide_id, role)``; id-less
cells are read-only until structural ops / id-minting land in P2.
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

from clm.notebooks.slide_parser import parse_cells
from clm.slides.sync_writeback import (
    FileState,
    anchor_of,
    cell_content_hash,
    role_of,
)
from clm.web.studio.models import (
    CellView,
    DeckSummary,
    DeckTree,
    DeckView,
    EditResult,
    SearchHit,
    SearchResults,
)

logger = logging.getLogger(__name__)

#: How long (seconds) after the app writes a deck the watcher should treat a
#: filesystem change to it as our own write rather than an external edit.
SELF_WRITE_WINDOW_SECONDS = 2.0

#: Cap on how many recently-opened decks to remember.
_MAX_RECENTS = 20


class StudioError(Exception):
    """Base class for Studio service errors (mapped to HTTP at the route layer)."""


class InvalidDeckIdError(StudioError):
    """The deck id is malformed, escapes the slides dir, or is not a ``.py`` deck."""


class DeckNotFoundError(StudioError):
    """No deck file exists for the given id."""


class CellNotFoundError(StudioError):
    """No cell with the given ``(slide_id, role)`` exists in the deck."""


class StaleWriteError(StudioError):
    """An optimistic-concurrency guard failed; the caller must re-fetch.

    ``current`` carries the up-to-date value (deck_version or cell_hash) so the
    409 response can hand the phone the fresh guard.
    """

    def __init__(self, kind: str, current: str) -> None:
        super().__init__(f"stale {kind}; expected value no longer current")
        self.kind = kind
        self.current = current


class StudioService:
    """Course-scoped engine for the Studio API."""

    def __init__(self, spec_path: Path) -> None:
        from clm.core.course_paths import resolve_course_paths
        from clm.core.course_spec import CourseSpec

        self.spec_path = Path(spec_path).resolve()
        self.spec = CourseSpec.from_file(self.spec_path)
        course_root, _ = resolve_course_paths(self.spec_path)
        self.slides_dir = (course_root / "slides").resolve()
        self._recents: list[str] = []
        self._self_writes: dict[str, float] = {}

    # ------------------------------------------------------------------ paths

    def _rel(self, path: Path) -> str:
        """Slides-dir-relative POSIX deck id for ``path``."""
        return path.resolve().relative_to(self.slides_dir).as_posix()

    def _resolve_deck_id(self, deck_id: str) -> Path:
        """Resolve a deck id to an absolute path, rejecting traversal/non-decks."""
        if not deck_id or deck_id.endswith("/"):
            raise InvalidDeckIdError(deck_id)
        candidate = (self.slides_dir / deck_id).resolve()
        root = self.slides_dir
        if candidate != root and root not in candidate.parents:
            raise InvalidDeckIdError(deck_id)
        if candidate.suffix != ".py":
            raise InvalidDeckIdError(deck_id)
        return candidate

    def _deck_version(self, path: Path) -> str:
        """Whole-file hash used as the deck-level optimistic-concurrency guard."""
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]

    # --------------------------------------------------------- self-write hint

    def mark_self_write(self, deck_id: str) -> None:
        """Record that we are about to write ``deck_id`` (suppresses watcher echo)."""
        self._self_writes[deck_id] = time.monotonic() + SELF_WRITE_WINDOW_SECONDS

    def is_self_write(self, deck_id: str) -> bool:
        """Whether a recent filesystem change to ``deck_id`` was our own write."""
        deadline = self._self_writes.get(deck_id)
        return deadline is not None and time.monotonic() < deadline

    # -------------------------------------------------------------- navigation

    def _note_recent(self, deck_id: str) -> None:
        if deck_id in self._recents:
            self._recents.remove(deck_id)
        self._recents.insert(0, deck_id)
        del self._recents[_MAX_RECENTS:]

    def list_decks(self) -> DeckTree:
        """Spec-resolved deck tree + recents + the 'not in spec' bucket."""
        from clm.core.spec_decks import resolve_spec_decks
        from clm.core.spec_orphans import find_orphans
        from clm.core.topic_resolver import build_topic_map

        topic_map = build_topic_map(self.slides_dir)
        resolution = resolve_spec_decks(self.spec, self.slides_dir, topic_map=topic_map)

        decks: list[DeckSummary] = []
        for topic in resolution.topics:
            if topic.found:
                for slide in topic.slide_files:
                    decks.append(
                        DeckSummary(
                            deck_id=self._rel(slide),
                            filename=slide.name,
                            topic_id=topic.topic_id,
                            section=topic.section,
                            status="present",
                        )
                    )
            else:
                decks.append(
                    DeckSummary(
                        deck_id="",
                        filename=topic.topic_id,
                        topic_id=topic.topic_id,
                        section=topic.section,
                        status="missing",
                    )
                )

        orphan_report = find_orphans([self.spec_path], self.slides_dir, topic_map=topic_map)
        orphans = [
            DeckSummary(
                deck_id=self._rel(o.path),
                filename=o.path.name,
                topic_id=None,
                section=None,
                status="orphan",
            )
            for o in orphan_report.orphans
        ]

        return DeckTree(
            spec_path=str(self.spec_path),
            slides_dir=str(self.slides_dir),
            decks=decks,
            orphans=orphans,
            recents=list(self._recents),
        )

    def search(self, query: str, *, max_results: int = 20) -> SearchResults:
        """Full-text search over deck titles + cell text (reuses ``slides search``)."""
        from clm.slides.search import search_slides

        raw = search_slides(
            query,
            self.slides_dir,
            course_spec_path=self.spec_path,
            max_results=max_results,
        )
        hits: list[SearchHit] = []
        for r in raw:
            deck_ids: list[str] = []
            for slide in r.slides:
                try:
                    deck_ids.append(self._rel(Path(r.directory) / slide.file))
                except (ValueError, OSError):
                    continue
            hits.append(
                SearchHit(
                    score=r.score,
                    topic_id=r.topic_id,
                    directory=r.directory,
                    title_de=r.slides[0].title_de if r.slides else "",
                    title_en=r.slides[0].title_en if r.slides else "",
                    deck_ids=deck_ids,
                )
            )
        return SearchResults(query=query, hits=hits)

    # ----------------------------------------------------------------- open

    def _cell_views(self, text: str, lang: str | None) -> list[CellView]:
        from collections import Counter

        parsed = parse_cells(text)
        # A cell is only safely addressable when its (slide_id, role) key is
        # UNIQUE in the file — FileState.find_cell returns the first match and
        # ignores language, so a colliding key (e.g. a genuinely interleaved
        # de+en deck sharing a slide_id) would let a write hit the wrong half.
        # Such cells are read-only in P1; bilingual editing is P3.
        key_counts: Counter[tuple[str, str]] = Counter()
        for c in parsed:
            c_role = role_of(c.metadata)
            if c.slide_id is not None and c_role is not None:
                key_counts[(c.slide_id, c_role)] += 1
        views: list[CellView] = []
        for index, cell in enumerate(parsed):
            if lang is not None and cell.lang is not None and cell.lang != lang:
                continue
            role = role_of(cell.metadata)
            editable = (
                cell.slide_id is not None
                and role is not None
                and key_counts[(cell.slide_id, role)] == 1
            )
            views.append(
                CellView(
                    index=index,
                    slide_id=cell.slide_id,
                    role=role,
                    cell_type=cell.cell_type,
                    lang=cell.lang,
                    tags=list(cell.tags),
                    body=cell.content,
                    is_j2=cell.metadata.is_j2,
                    content_hash=cell_content_hash(cell.content),
                    anchor=anchor_of(cell.metadata, cell.content),
                    editable=editable,
                )
            )
        return views

    def open_deck(self, deck_id: str, *, lang: str | None = None) -> DeckView:
        """Parse a deck for viewing/editing (read-only render)."""
        path = self._resolve_deck_id(deck_id)
        if not path.exists():
            raise DeckNotFoundError(deck_id)
        text = path.read_text(encoding="utf-8")
        self._note_recent(deck_id)
        return DeckView(
            deck_id=deck_id,
            deck_version=self._deck_version(path),
            lang=lang,
            cells=self._cell_views(text, lang),
        )

    # ----------------------------------------------------- concurrency core

    def _load_guarded(
        self,
        deck_id: str,
        slide_id: str,
        role: str,
        expected_deck_version: str,
        expected_cell_hash: str,
    ) -> tuple[Path, FileState]:
        """Validate the optimistic guards and return the loaded file state.

        Raises:
            DeckNotFoundError / CellNotFoundError: missing target.
            StaleWriteError: deck_version or cell_hash no longer current (→ 409).
        """
        path = self._resolve_deck_id(deck_id)
        if not path.exists():
            raise DeckNotFoundError(deck_id)

        current_version = self._deck_version(path)
        if current_version != expected_deck_version:
            raise StaleWriteError("deck_version", current_version)

        state = FileState.load(path)
        cell = state.find_cell(slide_id, role)
        if cell is None:
            raise CellNotFoundError(f"{slide_id!r}/{role!r}")

        current_hash = cell_content_hash(cell.body)
        if current_hash != expected_cell_hash:
            raise StaleWriteError("cell_hash", current_hash)

        return path, state

    def _persist(
        self, deck_id: str, path: Path, state: FileState, slide_id: str, role: str
    ) -> EditResult:
        """Flush ``state`` to disk and return fresh guards recomputed from disk."""
        self.mark_self_write(deck_id)
        state.flush()
        fresh = FileState.load(path)
        fresh_cell = fresh.find_cell(slide_id, role)
        new_hash = cell_content_hash(fresh_cell.body) if fresh_cell is not None else ""
        return EditResult(deck_version=self._deck_version(path), cell_hash=new_hash)

    def edit_body(
        self,
        deck_id: str,
        slide_id: str,
        role: str,
        new_body: str,
        *,
        expected_deck_version: str,
        expected_cell_hash: str,
    ) -> EditResult:
        """Replace one cell's body, guarded by optimistic concurrency."""
        path, state = self._load_guarded(
            deck_id, slide_id, role, expected_deck_version, expected_cell_hash
        )
        if not state.replace_cell_body(slide_id, role, new_body):
            raise CellNotFoundError(f"{slide_id!r}/{role!r}")
        return self._persist(deck_id, path, state, slide_id, role)

    def edit_tags(
        self,
        deck_id: str,
        slide_id: str,
        role: str,
        new_tags: list[str],
        *,
        expected_deck_version: str,
        expected_cell_hash: str,
    ) -> EditResult:
        """Replace one cell's tags, guarded by optimistic concurrency."""
        path, state = self._load_guarded(
            deck_id, slide_id, role, expected_deck_version, expected_cell_hash
        )
        if not state.replace_cell_tags(slide_id, role, new_tags):
            raise CellNotFoundError(f"{slide_id!r}/{role!r}")
        return self._persist(deck_id, path, state, slide_id, role)
