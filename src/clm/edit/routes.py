"""Route handlers for the deck editor.

Browse the ``slides/`` tree, then edit a deck cell-by-cell. Every mutating
request constructs a fresh :class:`~clm.edit.deck_file.DeckFile` (re-parses
from disk) so concurrent edits from two phones never act on stale cell
positions — last write wins, and untouched cells stay byte-identical.

Endpoints:

- ``GET /`` — browse modules → topics → deck files.
- ``GET /deck`` — open a deck by ``?path=`` (list of cells).
- ``GET /deck/cell/{index}/edit`` — inline cell edit form (HTMX partial).
- ``POST /deck/cell/{index}`` — save a cell's header + body.
- ``POST /deck/cell/{index}/move`` — reorder a cell (``?dir=up|down``).
- ``POST /deck/cell/{index}/delete`` — remove a cell.
- ``POST /deck/cell`` — insert a new cell (``?after={index}``).
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response

from clm.core.topic_resolver import build_topic_map
from clm.edit.deck_file import DeckFile, DeckFileError
from clm.edit.qr import is_available as qr_is_available
from clm.edit.qr import svg_data_uri
from clm.infrastructure.utils.path_utils import is_slides_file

router = APIRouter()


# ----------------------------------------------------------------------
# Accessors
# ----------------------------------------------------------------------


def _slides_dir(request: Request) -> Path:
    return cast(Path, request.app.state.slides_dir)


def _templates(request: Request):
    return request.app.state.templates


# ----------------------------------------------------------------------
# Path security
# ----------------------------------------------------------------------


def _resolve_deck(request: Request, rel_path: str) -> Path:
    """Resolve a deck path relative to the slides dir, refusing escapes.

    The editor must only ever touch files inside ``slides/`` — a crafted
    ``?path=../../etc/passwd`` must 404, not read outside the tree.
    """
    slides_dir = _slides_dir(request)
    if not rel_path:
        raise HTTPException(status_code=400, detail="missing path")
    candidate = (slides_dir / rel_path).resolve()
    try:
        candidate.relative_to(slides_dir.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="path outside slides dir") from exc
    if not candidate.is_file() or not is_slides_file(candidate):
        raise HTTPException(status_code=404, detail="not a slide file")
    return candidate


def _relative_to_slides(request: Request, path: Path) -> str:
    """Path of ``path`` relative to the slides dir, using forward slashes."""
    rel = path.resolve().relative_to(_slides_dir(request).resolve())
    return rel.as_posix()


def _public_url(request: Request) -> str:
    """The URL a phone should open, derived from the request's Host header.

    When the desktop browser hits ``http://192.168.1.42:8080`` (the LAN IP),
    the ``Host`` header already carries that address — so a QR code encoding
    it is scannable by a phone on the same network. Falls back to the app's
    configured host:port if the header is missing.
    """
    host_header = request.headers.get("host")
    if host_header:
        return f"http://{host_header}"
    return f"http://{request.app.state.host}:{request.app.state.port}"


# ----------------------------------------------------------------------
# Browse
# ----------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
async def browse(request: Request):
    """List every module → topic → deck file found under ``slides/``."""
    slides_dir = _slides_dir(request)
    topic_map = build_topic_map(slides_dir)
    # Flatten into module → [{topic, path_type, decks}] keeping discovery order.
    TopicEntry = dict[str, str | list[str]]
    modules: dict[str, list[TopicEntry]] = {}
    for matches in topic_map.values():
        for m in matches:
            modules.setdefault(m.module, []).append(
                {
                    "topic_id": m.topic_id,
                    "path_type": m.path_type,
                    "decks": [_relative_to_slides(request, f) for f in m.slide_files],
                }
            )
    # Stable ordering: modules sorted, topics sorted within module.
    ModuleEntry = dict[str, str | list[TopicEntry]]
    module_list: list[ModuleEntry] = [
        {
            "name": name,
            "topics": sorted(topics, key=lambda t: str(t["topic_id"])),
        }
        for name, topics in sorted(modules.items())
    ]
    return _templates(request).TemplateResponse(
        request,
        "browse.html",
        {
            "request": request,
            "modules": module_list,
            "has_slides": bool(module_list),
            "qr_available": qr_is_available(),
        },
    )


# ----------------------------------------------------------------------
# Deck view
# ----------------------------------------------------------------------


@router.get("/deck", response_class=HTMLResponse)
async def deck_view(
    request: Request,
    path: str = Query(..., description="Deck path relative to slides/"),
):
    deck_path = _resolve_deck(request, path)
    try:
        deck = DeckFile.load(deck_path)
    except DeckFileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    infos = deck.cell_infos()
    rel = _relative_to_slides(request, deck_path)
    return _templates(request).TemplateResponse(
        request,
        "deck.html",
        {
            "request": request,
            "rel_path": rel,
            "name": deck_path.name,
            "cells": infos,
            "cell_count": len(infos),
        },
    )


# ----------------------------------------------------------------------
# Cell edit (form)
# ----------------------------------------------------------------------


@router.get("/deck/cell/{index}/edit", response_class=HTMLResponse)
async def cell_edit_form(
    request: Request,
    index: int,
    path: str = Query(...),
):
    deck_path = _resolve_deck(request, path)
    deck = DeckFile.load(deck_path)
    try:
        info = deck.cell_infos()[index]
    except IndexError as exc:
        raise HTTPException(status_code=404, detail="cell index out of range") from exc
    rel = _relative_to_slides(request, deck_path)
    return _templates(request).TemplateResponse(
        request,
        "cell_edit.html",
        {
            "request": request,
            "info": info,
            "rel_path": rel,
            # body carries the separator blank; show the editor the trimmed body.
            "body_value": info.body.rstrip("\n"),
        },
    )


# ----------------------------------------------------------------------
# Cell save
# ----------------------------------------------------------------------


@router.post("/deck/cell/{index}", response_class=HTMLResponse)
async def cell_save(
    request: Request,
    index: int,
    path: str = Query(...),
    header: str = Form(...),
    body: str = Form(""),
):
    deck_path = _resolve_deck(request, path)
    deck = DeckFile.load(deck_path)
    try:
        deck.update_cell_header(index, header.rstrip("\n"))
        deck.replace_cell_body(index, body)
        deck.flush()
    except DeckFileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    info = deck.cell_infos()[index]
    rel = _relative_to_slides(request, deck_path)
    return _templates(request).TemplateResponse(
        request,
        "partials/cell.html",
        {
            "request": request,
            "info": info,
            "rel_path": rel,
            "index": index,
        },
    )


# ----------------------------------------------------------------------
# Cell move
# ----------------------------------------------------------------------


@router.post("/deck/cell/{index}/move", response_class=HTMLResponse)
async def cell_move(
    request: Request,
    index: int,
    path: str = Query(...),
    dir: str = Query("up"),
):
    deck_path = _resolve_deck(request, path)
    deck = DeckFile.load(deck_path)
    direction = -1 if dir == "up" else 1
    try:
        new_index = deck.move_cell(index, direction)
        deck.flush()
    except DeckFileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # After a move the whole deck shifts, so re-render the full list.
    rel = _relative_to_slides(request, deck_path)
    return _templates(request).TemplateResponse(
        request,
        "partials/cell_list.html",
        {
            "request": request,
            "rel_path": rel,
            "cells": deck.cell_infos(),
            "moved_index": new_index,
        },
    )


# ----------------------------------------------------------------------
# Cell delete
# ----------------------------------------------------------------------


@router.post("/deck/cell/{index}/delete", response_class=HTMLResponse)
async def cell_delete(
    request: Request,
    index: int,
    path: str = Query(...),
):
    deck_path = _resolve_deck(request, path)
    deck = DeckFile.load(deck_path)
    try:
        deck.delete_cell(index)
        deck.flush()
    except DeckFileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    rel = _relative_to_slides(request, deck_path)
    return _templates(request).TemplateResponse(
        request,
        "partials/cell_list.html",
        {
            "request": request,
            "rel_path": rel,
            "cells": deck.cell_infos(),
            "moved_index": None,
        },
    )


# ----------------------------------------------------------------------
# Cell insert
# ----------------------------------------------------------------------


@router.post("/deck/cell", response_class=HTMLResponse)
async def cell_insert(
    request: Request,
    path: str = Query(...),
    after: int = Query(..., description="Insert after this index; -1 for head"),
    header: str = Form("# %% [markdown]"),
    body: str = Form(""),
):
    deck_path = _resolve_deck(request, path)
    deck = DeckFile.load(deck_path)
    insert_at = after + 1 if after >= 0 else 0
    try:
        deck.insert_cell(insert_at, header.rstrip("\n"), body)
        deck.flush()
    except DeckFileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    rel = _relative_to_slides(request, deck_path)
    return _templates(request).TemplateResponse(
        request,
        "partials/cell_list.html",
        {
            "request": request,
            "rel_path": rel,
            "cells": deck.cell_infos(),
            "moved_index": insert_at,
        },
    )


# ----------------------------------------------------------------------
# QR code — scannable link for opening the editor on a phone
# ----------------------------------------------------------------------


@router.get("/qr")
async def qr_svg(request: Request) -> Response:
    """Return the editor's public URL as a standalone SVG QR code.

    Embeddable directly via ``<img src="/qr">``. The URL is derived from the
    request's ``Host`` header, so a desktop hitting the LAN IP produces a QR
    code scannable by a phone on the same network. Returns HTTP 503 when
    segno (the ``[edit]`` extra) is not installed.
    """
    if not qr_is_available():
        raise HTTPException(
            status_code=503,
            detail="QR code generation requires segno — install with clm[edit]",
        )
    uri = svg_data_uri(_public_url(request))
    # ``svg_data_uri`` returns a ``data:`` URI; strip its prefix to get the
    # raw SVG markup for a standalone image response.
    prefix = "data:image/svg+xml;charset=utf-8,"
    svg = uri[len(prefix) :] if uri.startswith(prefix) else uri
    from urllib.parse import unquote

    return Response(content=unquote(svg), media_type="image/svg+xml")
