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

P1 edits cells already addressable by ``(slide_id, role)``. P2 adds the
structural ops — ``insert_cell`` (minting or inheriting a slide_id),
``delete``, and ``move`` (reorder) — all routed through the same byte-exact
:class:`FileState` serializer so untouched cells never shift.
"""

from __future__ import annotations

import hashlib
import logging
import sys
import time
from pathlib import Path

from clm.notebooks.slide_parser import comment_token_for_path, parse_cells
from clm.slides.sync_writeback import (
    FileState,
    anchor_of,
    build_cell,
    cell_content_hash,
    role_of,
)
from clm.web.studio.models import (
    CellView,
    DeckSummary,
    DeckTree,
    DeckView,
    EditResult,
    LockState,
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


class InvalidStructuralOpError(StudioError):
    """A structural op (insert/delete/move) had invalid parameters (→ 400)."""


class LanguageLockedError(StudioError):
    """The deck's language is locked because the twin half is dirty (→ 423).

    ``reason`` is a human-readable explanation the phone surfaces; the resolution
    is to sync or discard the other half (on the desktop, until P3b lands those
    in-app).
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


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
        #: Deck ids (the DE half, canonical) with a sync subprocess in flight.
        self._sync_inflight: set[str] = set()

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
        """Parse a deck for viewing/editing (read-only render), with lock state."""
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
            lock=self.compute_lock(deck_id, path),
        )

    # ----------------------------------------------------- bilingual lock (P3)

    def compute_lock(self, deck_id: str, path: Path) -> LockState:
        """Derive the deck's bilingual lock from the structural sync watermark.

        A language is editable iff the *other* split half is **clean** relative to
        the last synced baseline (design §3.5): editing one half marks the other
        stale and locks it until a sync (or discard) makes both clean again. The
        plan build is **read-only and LLM-free** (``provider_available=False``).
        Returns an unlocked state for any deck with no split twin on disk.
        """
        from clm.slides.pairing import derive_split_twin, order_split_pair, split_lang_tag

        lang = split_lang_tag(path)
        twin = derive_split_twin(path)
        if lang is None or twin is None or not twin.exists():
            return LockState(is_pair=False, lang=lang, editable=True, baseline="n/a")
        ordered = order_split_pair(path, twin)
        if ordered is None:
            return LockState(is_pair=False, lang=lang, editable=True, baseline="n/a")
        de_path, en_path = ordered
        other_lang = "en" if lang == "de" else "de"

        from clm.infrastructure.llm.cache import (
            CACHE_DB_NAME,
            SyncWatermarkCache,
            resolve_cache_dir,
        )
        from clm.slides.sync_plan import build_sync_plan

        cache = SyncWatermarkCache(resolve_cache_dir() / CACHE_DB_NAME)
        try:
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
        finally:
            cache.close()

        # ``direction`` names the half that drifted (the source). de->en ⇒ DE is
        # dirty; en->de ⇒ EN is dirty. A conflict (both drifted) locks both.
        de_dirty = any(p.direction == "de->en" for p in plan.proposals)
        en_dirty = any(p.direction == "en->de" for p in plan.proposals)
        has_conflicts = plan.count("conflict") > 0
        this_dirty = de_dirty if lang == "de" else en_dirty
        other_dirty = en_dirty if lang == "de" else de_dirty

        editable = not other_dirty and not has_conflicts
        if has_conflicts:
            reason: str | None = (
                "Both languages changed since the last sync — resolve on the "
                "desktop with `clm slides sync`."
            )
        elif other_dirty:
            reason = (
                f"{other_lang.upper()} has unsynced edits and is the active source; "
                f"sync or discard it to edit {lang.upper()}."
            )
        else:
            reason = None

        return LockState(
            is_pair=True,
            lang=lang,
            other_lang=other_lang,
            twin_deck_id=self._rel(twin),
            editable=editable,
            locked_reason=reason,
            other_stale=this_dirty,
            has_conflicts=has_conflicts,
            baseline=plan.baseline_source,
        )

    def _enforce_lock(self, deck_id: str, path: Path) -> None:
        """Raise :class:`LanguageLockedError` (→ 423) if this language is locked."""
        lock = self.compute_lock(deck_id, path)
        if lock.is_pair and not lock.editable:
            raise LanguageLockedError(lock.locked_reason or "This language is locked.")

    # --------------------------------------------- sync-to-other-language (P3b)

    def resolve_sync_command(self, deck_id: str) -> tuple[list[str], str, str]:
        """Build the ``clm slides sync`` subprocess command for ``deck_id``'s pair.

        Returns ``(cmd, de_deck_id, en_deck_id)``. The command reconciles the
        split DE/EN pair (direction decided per cell) and **writes both halves +
        advances the watermark**, so the lock releases afterward. Raises
        :class:`InvalidStructuralOpError` for a deck with no split twin (nothing
        to sync). The subprocess inherits the serve cwd so it shares the
        watermark cache with :meth:`compute_lock` (see ``sync_runner``).
        """
        from clm.slides.pairing import derive_split_twin, order_split_pair, split_lang_tag

        path = self._resolve_deck_id(deck_id)
        if not path.exists():
            raise DeckNotFoundError(deck_id)
        twin = derive_split_twin(path)
        if split_lang_tag(path) is None or twin is None or not twin.exists():
            raise InvalidStructuralOpError("not a split DE/EN pair — nothing to sync")
        ordered = order_split_pair(path, twin)
        if ordered is None:
            raise InvalidStructuralOpError("not a valid split DE/EN pair")
        de_path, en_path = ordered
        cmd = [sys.executable, "-m", "clm", "slides", "sync", str(de_path), "--yes"]
        return cmd, self._rel(de_path), self._rel(en_path)

    def try_begin_sync(self, key: str) -> bool:
        """Claim the in-flight slot for ``key`` (the DE deck id). False if taken."""
        if key in self._sync_inflight:
            return False
        self._sync_inflight.add(key)
        return True

    def end_sync(self, key: str) -> None:
        """Release the in-flight slot for ``key``."""
        self._sync_inflight.discard(key)

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
        self._enforce_lock(deck_id, path)
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
        self._enforce_lock(deck_id, path)
        if not state.replace_cell_tags(slide_id, role, new_tags):
            raise CellNotFoundError(f"{slide_id!r}/{role!r}")
        return self._persist(deck_id, path, state, slide_id, role)

    # ------------------------------------------------------- structural ops (P2)

    def _load_for_structural(
        self, deck_id: str, expected_deck_version: str
    ) -> tuple[Path, FileState]:
        """Resolve + deck-version-guard a deck for a structural op (no cell guard).

        Insert/move change the cell set, so the only optimistic guard is the
        whole-file ``deck_version`` — any concurrent change yields a 409 so the
        phone re-fetches before mutating a stale view.
        """
        path = self._resolve_deck_id(deck_id)
        if not path.exists():
            raise DeckNotFoundError(deck_id)
        current_version = self._deck_version(path)
        if current_version != expected_deck_version:
            raise StaleWriteError("deck_version", current_version)
        return path, FileState.load(path)

    @staticmethod
    def _infer_lang(
        state: FileState, after_slide_id: str | None, after_role: str | None
    ) -> str | None:
        """Language for a new cell: the anchor's, else the deck's dominant lang."""
        if after_slide_id is not None and after_role is not None:
            anchor = state.find_cell(after_slide_id, after_role)
            if anchor is not None and anchor.metadata.lang is not None:
                return anchor.metadata.lang
        from collections import Counter

        langs = [c.metadata.lang for c in state.cells if c.metadata.lang is not None]
        if langs:
            return Counter(langs).most_common(1)[0][0]
        return None

    @staticmethod
    def _resolve_or_mint_slide_id(
        state: FileState, slide_id: str | None, role: str, body: str
    ) -> str:
        """Validate an explicit slide_id, or mint a unique one from the body title.

        An explicit id (used to attach e.g. ``notes`` to an existing slide, which
        must share the slide's identity to group correctly) must be a valid slug
        and must not already pair with ``role`` in this file — that would create a
        duplicate ``(slide_id, role)`` key, which is un-addressable. With no id,
        mint a kebab slug from the body title (the same extractor ``assign-ids``
        uses), suffixed to stay unique among existing ids.
        """
        from clm.slides.headingless import classify
        from clm.slides.slug import is_valid_slug, resolve_collision, slugify

        existing = {c.metadata.slide_id for c in state.cells if c.metadata.slide_id is not None}
        if slide_id is not None:
            if not is_valid_slug(slide_id):
                raise InvalidStructuralOpError(f"invalid slide_id: {slide_id!r}")
            for c in state.cells:
                if c.metadata.slide_id == slide_id and role_of(c.metadata) == role:
                    raise InvalidStructuralOpError(
                        f"duplicate (slide_id, role): {slide_id!r}/{role!r}"
                    )
            return slide_id
        base = slugify(getattr(classify(body), "text", "") or "") or "cell"
        return resolve_collision(base, existing)

    def insert_cell(
        self,
        deck_id: str,
        *,
        role: str,
        cell_type: str = "markdown",
        body: str = "",
        after_slide_id: str | None = None,
        after_role: str | None = None,
        slide_id: str | None = None,
        lang: str | None = None,
        expected_deck_version: str,
    ) -> EditResult:
        """Insert a new cell after an anchor (or at the deck start), minting its id.

        Returns an :class:`EditResult` whose ``slide_id`` carries the minted (or
        inherited) id the phone must adopt to address the new cell.
        """
        if cell_type not in ("markdown", "code"):
            raise InvalidStructuralOpError(f"invalid cell_type: {cell_type!r}")
        if not role:
            raise InvalidStructuralOpError("role is required")
        if cell_type == "code" and role != "code":
            raise InvalidStructuralOpError('a code cell must use role "code"')

        path, state = self._load_for_structural(deck_id, expected_deck_version)
        self._enforce_lock(deck_id, path)
        resolved_lang = lang or self._infer_lang(state, after_slide_id, after_role)
        new_id = self._resolve_or_mint_slide_id(state, slide_id, role, body)
        tags = [] if cell_type == "code" else [role]
        new_cell = build_cell(
            comment_token_for_path(path),
            cell_type=cell_type,
            lang=resolved_lang,
            tags=tags,
            slide_id=new_id,
            body=body,
        )
        if after_slide_id is None:
            state.insert_before_first_sync_cell(new_cell)
        elif not state.insert_after(after_slide_id, after_role or "", new_cell):
            raise CellNotFoundError(f"{after_slide_id!r}/{after_role!r}")

        result = self._persist(deck_id, path, state, new_id, role)
        result.slide_id = new_id
        return result

    def delete(
        self,
        deck_id: str,
        slide_id: str,
        role: str,
        *,
        expected_deck_version: str,
        expected_cell_hash: str,
    ) -> EditResult:
        """Remove the ``(slide_id, role)`` cell, guarded by optimistic concurrency."""
        path, state = self._load_guarded(
            deck_id, slide_id, role, expected_deck_version, expected_cell_hash
        )
        self._enforce_lock(deck_id, path)
        if not state.delete_cell(slide_id, role):
            raise CellNotFoundError(f"{slide_id!r}/{role!r}")
        # The cell is gone, so _persist's post-flush lookup yields an empty hash.
        return self._persist(deck_id, path, state, slide_id, role)

    def move(
        self,
        deck_id: str,
        slide_id: str,
        role: str,
        direction: str,
        *,
        expected_deck_version: str,
    ) -> EditResult:
        """Swap the ``(slide_id, role)`` cell with its neighbour (``"up"``/``"down"``)."""
        if direction not in ("up", "down"):
            raise InvalidStructuralOpError(f"invalid direction: {direction!r}")
        path, state = self._load_for_structural(deck_id, expected_deck_version)
        self._enforce_lock(deck_id, path)
        if state.find_cell(slide_id, role) is None:
            raise CellNotFoundError(f"{slide_id!r}/{role!r}")
        if not state.move_cell(slide_id, role, direction):
            raise InvalidStructuralOpError(f"cannot move {direction!r}: cell at boundary")
        return self._persist(deck_id, path, state, slide_id, role)
