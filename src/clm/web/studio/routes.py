"""Studio API routes (mounted only when ``clm serve`` is given a course spec).

REST under ``/api/studio``. Every call requires the bearer token (§3.2). Deck
ids are slash-bearing relative paths, so they travel as query/body params
rather than URL path segments (a greedy path converter would swallow them).

Optimistic-concurrency failures surface as **409** (``deck_version`` or
``cell_hash`` no longer current); the response carries the fresh guard so the
phone can re-fetch and retry. A write to a watermark-**locked** language is
**423** (P3a). ``/deck/sync`` starts a streamed sync-to-other-language (P3b).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from clm.web.studio import sync_runner
from clm.web.studio.auth import token_matches
from clm.web.studio.models import (
    DeckTree,
    DeckView,
    DeleteCellRequest,
    EditBodyRequest,
    EditResult,
    EditTagsRequest,
    InsertCellRequest,
    MoveCellRequest,
    RenderCellRequest,
    RenderCellResult,
    SearchResults,
    SyncRequest,
    SyncStartResult,
)
from clm.web.studio.service import (
    CellNotFoundError,
    DeckNotFoundError,
    InvalidDeckIdError,
    InvalidStructuralOpError,
    LanguageLockedError,
    StaleWriteError,
    StudioService,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/studio")


def require_token(request: Request) -> None:
    """FastAPI dependency: reject requests without a valid bearer token."""
    expected = getattr(request.app.state, "studio_token", None)
    if not expected or not token_matches(request, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing Studio token")


def get_service(request: Request) -> StudioService:
    """Resolve the per-instance StudioService from app state."""
    service = getattr(request.app.state, "studio_service", None)
    if service is None:
        raise HTTPException(status_code=404, detail="Studio not enabled (start with --spec)")
    return cast(StudioService, service)


def _handle_write(call: Callable[[], EditResult]) -> EditResult:
    """Run a write callable, translating service errors to HTTP responses."""
    try:
        return call()
    except StaleWriteError as e:
        raise HTTPException(
            status_code=409,
            detail={"error": "stale", "kind": e.kind, "current": e.current},
        ) from e
    except LanguageLockedError as e:
        raise HTTPException(
            status_code=423,
            detail={"error": "locked", "reason": e.reason},
        ) from e
    except CellNotFoundError as e:
        raise HTTPException(status_code=404, detail=f"Cell not found: {e}") from e
    except DeckNotFoundError as e:
        raise HTTPException(status_code=404, detail=f"Deck not found: {e}") from e
    except InvalidStructuralOpError as e:
        raise HTTPException(status_code=400, detail=f"Invalid structural op: {e}") from e
    except InvalidDeckIdError as e:
        raise HTTPException(status_code=400, detail=f"Invalid deck id: {e}") from e


@router.get("/decks", response_model=DeckTree, dependencies=[Depends(require_token)])
async def list_decks(request: Request) -> DeckTree:
    """Navigation tree: spec-resolved decks, recents, and the 'not in spec' bucket."""
    service = get_service(request)
    try:
        return service.list_decks()
    except Exception as e:  # pragma: no cover - surfaced to the client
        logger.error("Studio list_decks failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error listing decks: {e}") from e


@router.get("/search", response_model=SearchResults, dependencies=[Depends(require_token)])
async def search(
    request: Request,
    q: str = Query(..., min_length=1, description="Search query."),
    limit: int = Query(20, ge=1, le=100),
) -> SearchResults:
    """Full-text search over deck titles + cell text."""
    service = get_service(request)
    try:
        return service.search(q, max_results=limit)
    except Exception as e:  # pragma: no cover
        logger.error("Studio search failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Search error: {e}") from e


@router.get("/deck", response_model=DeckView, dependencies=[Depends(require_token)])
async def open_deck(
    request: Request,
    id: str = Query(..., description="Slides-dir-relative deck path."),
    lang: str | None = Query(None, description='Optional language filter ("de"/"en").'),
) -> DeckView:
    """Open a deck for viewing/editing (read-only render)."""
    service = get_service(request)
    try:
        return service.open_deck(id, lang=lang)
    except InvalidDeckIdError as e:
        raise HTTPException(status_code=400, detail=f"Invalid deck id: {e}") from e
    except DeckNotFoundError as e:
        raise HTTPException(status_code=404, detail=f"Deck not found: {e}") from e


@router.post("/deck/edit-body", response_model=EditResult, dependencies=[Depends(require_token)])
async def edit_body(request: Request, req: EditBodyRequest) -> EditResult:
    """Replace a cell body (optimistic concurrency)."""
    service = get_service(request)
    return _handle_write(
        lambda: service.edit_body(
            req.deck_id,
            req.slide_id,
            req.role,
            req.new_body,
            expected_deck_version=req.expected_deck_version,
            expected_cell_hash=req.expected_cell_hash,
        )
    )


@router.post("/deck/edit-tags", response_model=EditResult, dependencies=[Depends(require_token)])
async def edit_tags(request: Request, req: EditTagsRequest) -> EditResult:
    """Replace a cell's tags (optimistic concurrency)."""
    service = get_service(request)
    return _handle_write(
        lambda: service.edit_tags(
            req.deck_id,
            req.slide_id,
            req.role,
            req.new_tags,
            expected_deck_version=req.expected_deck_version,
            expected_cell_hash=req.expected_cell_hash,
        )
    )


@router.post("/deck/insert", response_model=EditResult, dependencies=[Depends(require_token)])
async def insert_cell(request: Request, req: InsertCellRequest) -> EditResult:
    """Insert a new cell, minting (or inheriting) its slide_id (optimistic concurrency)."""
    service = get_service(request)
    return _handle_write(
        lambda: service.insert_cell(
            req.deck_id,
            role=req.role,
            cell_type=req.cell_type,
            body=req.body,
            after_slide_id=req.after_slide_id,
            after_role=req.after_role,
            slide_id=req.slide_id,
            lang=req.lang,
            expected_deck_version=req.expected_deck_version,
        )
    )


@router.post("/deck/delete", response_model=EditResult, dependencies=[Depends(require_token)])
async def delete_cell(request: Request, req: DeleteCellRequest) -> EditResult:
    """Delete a cell (optimistic concurrency)."""
    service = get_service(request)
    return _handle_write(
        lambda: service.delete(
            req.deck_id,
            req.slide_id,
            req.role,
            expected_deck_version=req.expected_deck_version,
            expected_cell_hash=req.expected_cell_hash,
        )
    )


@router.post("/deck/move", response_model=EditResult, dependencies=[Depends(require_token)])
async def move_cell(request: Request, req: MoveCellRequest) -> EditResult:
    """Reorder a cell up/down by one (optimistic concurrency)."""
    service = get_service(request)
    return _handle_write(
        lambda: service.move(
            req.deck_id,
            req.slide_id,
            req.role,
            req.direction,
            expected_deck_version=req.expected_deck_version,
        )
    )


@router.post("/deck/sync", response_model=SyncStartResult, dependencies=[Depends(require_token)])
async def sync_deck(request: Request, req: SyncRequest) -> SyncStartResult:
    """Start a streamed sync-to-other-language for a split pair (P3b).

    Validates the pair, then runs ``clm slides sync`` as a background subprocess
    whose progress streams over the WS ``studio`` channel. Returns as soon as the
    run is launched; the phone watches WS for ``sync-progress`` / ``sync-done``.
    A second request while one is in flight for the same pair is a **409**.
    """
    service = get_service(request)
    try:
        _, de_id, _ = service.resolve_sync_command(req.deck_id)
    except DeckNotFoundError as e:
        raise HTTPException(status_code=404, detail=f"Deck not found: {e}") from e
    except InvalidDeckIdError as e:
        raise HTTPException(status_code=400, detail=f"Invalid deck id: {e}") from e
    except InvalidStructuralOpError as e:
        raise HTTPException(status_code=400, detail=f"Cannot sync: {e}") from e

    if not service.try_begin_sync(de_id):
        raise HTTPException(status_code=409, detail="A sync is already running for this deck.")

    async def _runner() -> None:
        try:
            await sync_runner.run_sync(service, req.deck_id)
        finally:
            service.end_sync(de_id)

    asyncio.create_task(_runner())
    return SyncStartResult(started=True, deck_id=req.deck_id)


@router.post(
    "/deck/render-cell",
    response_model=RenderCellResult,
    dependencies=[Depends(require_token)],
)
async def render_cell(request: Request, req: RenderCellRequest) -> RenderCellResult:
    """Tier-2 (no-exec) render of one ``is_j2`` cell (P4).

    Expands the cell's Jinja (header macros, ``{{ … }}``) server-side through the
    build's bundled macros, no kernel. Plain cells (or any failure) return the
    body unchanged with ``rendered=False`` so the phone falls back to tier-1.
    """
    service = get_service(request)
    try:
        rendered, error, text = service.render_cell(
            req.deck_id, req.body, is_j2=req.is_j2, lang=req.lang
        )
    except InvalidDeckIdError as e:
        raise HTTPException(status_code=400, detail=f"Invalid deck id: {e}") from e
    return RenderCellResult(rendered=rendered, body=text, error=error)
