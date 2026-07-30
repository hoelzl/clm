"""Studio API routes (mounted only when ``clm serve`` is given a course spec).

REST under ``/api/studio``. Every call requires the bearer token (§3.2). Deck
ids are slash-bearing relative paths, so they travel as query/body params
rather than URL path segments (a greedy path converter would swallow them).

Optimistic-concurrency failures surface as **409** (``deck_version`` or
``cell_hash`` no longer current); the response carries the fresh guard so the
phone can re-fetch and retry. A write to a ledger-**locked** language is
**423** (P3a). ``/deck/sync`` starts a streamed sync-to-other-language (P3b).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.responses import Response

from clm.web.studio import sync_runner
from clm.web.studio.auth import token_matches
from clm.web.studio.logo import logo_file
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
            body_format=req.body_format,
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
            body_format=req.body_format,
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
    """Tier-2 (kernel-free) render of one ``is_j2`` cell (P4).

    Expands the cell's Jinja (header macros, ``{{ … }}``) server-side through the
    build's bundled macros, no kernel, and returns it as an HTML fragment that has
    been **sanitized here** (issue #697) — the header macros emit markup, so the
    client cannot escape its way to safety. Plain cells (or any failure, including
    a missing sanitizer) return ``rendered=False`` with ``html=None`` so the phone
    falls back to tier-1.

    The Jinja expansion runs in a **killable subprocess** under a wall-clock
    budget (issue #698): the body is client-supplied and CPU-unboundable
    in-process (nested ``range()`` loops), and the old worker-thread route
    let 40 slow renders occupy the shared threadpool that also serves the
    ``/studio/`` static shell. A timed-out render degrades to tier-1 like
    every other preview failure; no threadpool token is held while waiting.
    """
    service = get_service(request)
    try:
        rendered, error, html = await service.render_cell_async(
            req.deck_id, req.body, is_j2=req.is_j2, lang=req.lang
        )
    except InvalidDeckIdError as e:
        raise HTTPException(status_code=400, detail=f"Invalid deck id: {e}") from e
    return RenderCellResult(rendered=rendered, body=req.body, html=html, error=error)


@router.get("/asset/logo/{prog_lang}")
async def logo_asset(prog_lang: str) -> Response:
    """The bundled course logo, for the tier-2 preview's rewritten ``<img>`` (#706).

    Deliberately **not** token-gated: an ``<img src>`` fetch cannot carry the
    ``Authorization`` header, and there is nothing here to protect — the route
    serves only the packaged logo files, with ``prog_lang`` selecting a fixed
    mapping entry (never a path). The response carries its own restrictive CSP
    in case the SVG is ever opened as a *document* rather than an image
    subresource — and because the global security-headers middleware keeps a
    route's own CSP (route has the last word), that policy is self-contained
    (``default-src 'none'``), not just ``script-src 'none'``.
    """
    found = logo_file(prog_lang)
    if found is None:
        raise HTTPException(status_code=404, detail=f"No bundled logo for {prog_lang!r}")
    source, media_type = found
    return Response(
        content=source.read_bytes(),
        media_type=media_type,
        headers={
            # Packaged with clm; changes only across installs/upgrades.
            "Cache-Control": "max-age=3600",
            # The global CSP middleware lets a route's own policy *replace* the
            # app-wide one, so this must be self-contained — not just
            # "script-src 'none'" — or an SVG opened as a same-origin document
            # would lose default-src/object-src/base-uri with it.
            "Content-Security-Policy": (
                "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'"
            ),
        },
    )
