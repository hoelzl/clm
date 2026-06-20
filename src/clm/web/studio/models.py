"""Pydantic request/response models for the Studio API.

These cross the phone↔desktop boundary, so per the repo convention messages
are Pydantic (not ``attrs``). Field names are part of the wire contract the
frontend depends on.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CellView(BaseModel):
    """One cell as presented to the phone for browsing/editing.

    ``index`` is an ordinal for display only — it is **never** a write key
    (the keystone: index-keyed writes are unsafe against the two-editor race).
    Writes target ``(slide_id, role)`` and are guarded by ``content_hash``.
    """

    index: int = Field(description="Display ordinal; not a write key.")
    slide_id: str | None = Field(default=None, description="Hand-assigned slide id, if any.")
    role: str | None = Field(default=None, description="Sync role (slide/notes/code/…), if any.")
    cell_type: str = Field(description='"markdown" or "code".')
    lang: str | None = Field(default=None, description='"de"/"en", or None for shared code.')
    tags: list[str] = Field(default_factory=list)
    body: str = Field(description="Cell body text.")
    is_j2: bool = Field(default=False, description="Jinja header/macro cell.")
    content_hash: str = Field(description="Hash of the cell body (optimistic-concurrency guard).")
    anchor: str = Field(description="Content-derived stable identity (id:/construct:/hash:).")
    editable: bool = Field(
        description="True iff addressable by (slide_id, role) — only these are editable in P1."
    )


class LockState(BaseModel):
    """The bilingual lock state of an opened deck (P3 — design §3.5).

    Derived from the structural sync watermark, not invented session state: a
    language is editable iff the *other* half is clean relative to the last
    synced baseline. ``is_pair`` is False for a deck with no split twin on disk
    (a single-language deck is always editable — no lock applies).
    """

    is_pair: bool = Field(description="True iff a split DE/EN twin exists on disk.")
    lang: str | None = Field(default=None, description='This deck\'s language ("de"/"en").')
    other_lang: str | None = Field(default=None, description="The twin's language.")
    twin_deck_id: str | None = Field(
        default=None, description="Slides-dir-relative id of the twin (for the language toggle)."
    )
    editable: bool = Field(
        default=True, description="False when this language is locked (other half is dirty)."
    )
    locked_reason: str | None = Field(
        default=None, description="Human-readable reason this language is locked, if it is."
    )
    other_stale: bool = Field(
        default=False, description="True when this half is dirty so the twin needs a sync."
    )
    has_conflicts: bool = Field(
        default=False, description="Both halves changed since the watermark (locks both)."
    )
    baseline: str = Field(
        default="n/a", description='Baseline source: "watermark"/"git-head"/"none"/"n/a".'
    )


class DeckView(BaseModel):
    """A single deck opened for viewing/editing."""

    deck_id: str = Field(description="Slides-dir-relative POSIX path identifying the deck.")
    deck_version: str = Field(description="Hash of the whole file (optimistic-concurrency guard).")
    lang: str | None = Field(default=None, description="Language filter applied, if any.")
    cells: list[CellView]
    lock: LockState = Field(
        default_factory=lambda: LockState(is_pair=False),
        description="Bilingual lock state (P3).",
    )


class DeckSummary(BaseModel):
    """A deck entry in the navigation tree / recents / orphan bucket."""

    deck_id: str
    filename: str
    topic_id: str | None = None
    section: str | None = None
    status: str = Field(description='"present", "missing", or "orphan".')


class DeckTree(BaseModel):
    """Navigation payload: spec-resolved decks, recents, and orphans."""

    spec_path: str
    slides_dir: str
    decks: list[DeckSummary] = Field(description="Spec-resolved decks (present + missing).")
    orphans: list[DeckSummary] = Field(description='Decks "not in spec".')
    recents: list[str] = Field(default_factory=list, description="Recently opened deck ids.")


class SearchHit(BaseModel):
    """One search result."""

    score: float
    topic_id: str
    directory: str
    title_de: str = ""
    title_en: str = ""
    deck_ids: list[str] = Field(default_factory=list)


class SearchResults(BaseModel):
    query: str
    hits: list[SearchHit]


class EditBodyRequest(BaseModel):
    """An ``edit-body`` write, carrying the optimistic-concurrency expectations.

    ``deck_id`` + ``slide_id`` travel in the body (not the URL path) because a
    deck id is a slash-bearing relative path that a greedy path converter would
    swallow; the write key is still ``(slide_id, role)``, never an index.
    """

    deck_id: str = Field(description="Slides-dir-relative deck path.")
    slide_id: str = Field(description="Hand-assigned slide id of the target cell.")
    role: str = Field(description="Sync role of the target cell.")
    new_body: str = Field(description="Replacement cell body.")
    expected_deck_version: str = Field(description="deck_version the phone last saw.")
    expected_cell_hash: str = Field(description="content_hash the phone last saw for this cell.")


class EditTagsRequest(BaseModel):
    """An ``edit-tags`` write, carrying the optimistic-concurrency expectations."""

    deck_id: str
    slide_id: str
    role: str
    new_tags: list[str]
    expected_deck_version: str
    expected_cell_hash: str


class InsertCellRequest(BaseModel):
    """An ``insert`` write: add a new cell, minting (or inheriting) a slide_id.

    Position is given by an anchor: the new cell lands immediately **after** the
    ``(after_slide_id, after_role)`` cell, or — when ``after_slide_id`` is
    omitted — ahead of the deck's first sync cell (it becomes the first slide).

    ``slide_id`` is optional: omit it for a new slide (the server mints a unique
    slug from the body title), or pass the anchor slide's id to add a companion
    cell (e.g. ``notes``) that must share its slide's identity to group
    correctly. Only ``expected_deck_version`` guards the write — there is no
    prior cell hash for a cell that does not yet exist.
    """

    deck_id: str = Field(description="Slides-dir-relative deck path.")
    after_slide_id: str | None = Field(
        default=None, description="Anchor cell's slide_id; omit to insert at the deck start."
    )
    after_role: str | None = Field(default=None, description="Anchor cell's role.")
    cell_type: str = Field(default="markdown", description='"markdown" or "code".')
    role: str = Field(description="Sync role/tag for the new cell (slide/notes/voiceover/…).")
    body: str = Field(default="", description="Body of the new cell (raw, comment-prefixed).")
    slide_id: str | None = Field(
        default=None, description="Explicit slide id; omitted → minted from the body title."
    )
    lang: str | None = Field(
        default=None, description="Language; omitted → inherited from the anchor/deck."
    )
    expected_deck_version: str = Field(description="deck_version the phone last saw.")


class DeleteCellRequest(BaseModel):
    """A ``delete`` write, carrying the optimistic-concurrency expectations."""

    deck_id: str
    slide_id: str
    role: str
    expected_deck_version: str
    expected_cell_hash: str = Field(description="content_hash the phone last saw for this cell.")


class MoveCellRequest(BaseModel):
    """A ``move`` (reorder) write: swap a cell with its neighbour.

    The cell content does not change, so only ``expected_deck_version`` guards
    the write — a mismatch (any concurrent edit) yields 409 so the phone
    re-fetches before reordering a stale view.
    """

    deck_id: str
    slide_id: str
    role: str
    direction: str = Field(description='"up" or "down".')
    expected_deck_version: str


class EditResult(BaseModel):
    """Result of a successful write: the fresh guards for the next edit.

    ``slide_id`` is populated on an ``insert`` (the minted or inherited id the
    phone must adopt to address the new cell); it is ``None`` for in-place edits.
    """

    ok: bool = True
    deck_version: str
    cell_hash: str
    slide_id: str | None = None


class RenderCellRequest(BaseModel):
    """Tier-2 (no-exec) render request for one cell."""

    body: str
    is_j2: bool = False


class RenderCellResult(BaseModel):
    """Tier-2 render result.

    P0/P1 ship tier-1 (client-side markdown) as the working preview; this
    server endpoint is scaffolded and currently echoes the body with
    ``rendered=False`` for ``is_j2`` cells. Wiring the jupytext+Jinja no-exec
    expansion is a focused follow-up (still within the P0 design scope).
    """

    rendered: bool
    html: str | None = None
    body: str
