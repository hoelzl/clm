"""Route handlers for the recordings dashboard.

Provides:
- ``GET /`` — Dashboard page (Jinja2 template)
- ``GET /lectures`` — Lecture list from course spec (HTMX partial)
- ``POST /arm`` — Arm a topic for recording
- ``POST /disarm`` — Disarm the current topic
- ``GET /status`` — JSON session status snapshot
- ``GET /events`` — SSE stream for real-time updates
- ``GET /pairs`` — Pending pairs list (HTMX partial)
- ``POST /watcher/start`` — Start the file watcher
- ``POST /watcher/stop`` — Stop the file watcher
"""

from __future__ import annotations

import asyncio
from typing import cast

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from loguru import logger

from clm.recordings.workflow.session import RecordingSession, SessionSnapshot
from clm.recordings.workflow.watcher import RecordingsWatcher

router = APIRouter()


def _get_session(request: Request) -> RecordingSession:
    return cast(RecordingSession, request.app.state.session)


def _get_watcher(request: Request) -> RecordingsWatcher:
    return cast(RecordingsWatcher, request.app.state.watcher)


def _get_templates(request: Request):
    return request.app.state.templates


# ------------------------------------------------------------------
# Pages
# ------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Render the main dashboard page."""
    templates = _get_templates(request)
    session = _get_session(request)
    watcher = _get_watcher(request)
    snap = session.snapshot()
    pairs = _get_pending_pairs(request)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "snapshot": snap,
            "pairs": pairs,
            "watcher_running": watcher.running,
            "watcher_mode": watcher.mode,
        },
    )


@router.get("/lectures", response_class=HTMLResponse)
async def lectures(request: Request):
    """Render the lecture list from the course spec file.

    Returns an HTMX partial — a list of sections with arm buttons.
    """
    templates = _get_templates(request)
    spec_file = request.app.state.spec_file
    session = _get_session(request)
    snap = session.snapshot()

    sections: list[dict] = []
    error: str | None = None

    if spec_file is not None:
        try:
            from pathlib import Path

            from clm.core.course_spec import CourseSpec

            spec = CourseSpec.from_file(Path(spec_file))
            for section in spec.sections:
                topics = []
                for topic in section.topics:
                    topics.append(
                        {
                            "id": topic.id,
                            "name": topic.id.split("/")[-1] if "/" in topic.id else topic.id,
                        }
                    )
                sections.append(
                    {
                        "name": section.name.en or section.name.de,
                        "topics": topics,
                    }
                )
        except Exception as exc:
            error = str(exc)
            logger.warning("Could not load course spec: {}", exc)
    else:
        error = "No course spec file configured. Pass --spec-file to clm recordings serve."

    return templates.TemplateResponse(
        request,
        "lectures.html",
        {
            "sections": sections,
            "snapshot": snap,
            "error": error,
        },
    )


# ------------------------------------------------------------------
# Actions
# ------------------------------------------------------------------


@router.post("/arm", response_class=HTMLResponse)
async def arm_topic(
    request: Request,
    course_slug: str = Form(...),
    section_name: str = Form(...),
    topic_name: str = Form(...),
):
    """Arm a topic for the next recording."""
    session = _get_session(request)
    try:
        session.arm(course_slug, section_name, topic_name)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # Return updated status partial for HTMX swap
    return await status_partial(request)


@router.post("/disarm", response_class=HTMLResponse)
async def disarm(request: Request):
    """Disarm the currently armed topic."""
    session = _get_session(request)
    try:
        session.disarm()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return await status_partial(request)


# ------------------------------------------------------------------
# Watcher
# ------------------------------------------------------------------


@router.post("/watcher/start", response_class=HTMLResponse)
async def watcher_start(request: Request):
    """Start the file watcher."""
    watcher = _get_watcher(request)
    watcher.start()
    return await status_partial(request)


@router.post("/watcher/stop", response_class=HTMLResponse)
async def watcher_stop(request: Request):
    """Stop the file watcher."""
    watcher = _get_watcher(request)
    watcher.stop()
    return await status_partial(request)


# ------------------------------------------------------------------
# Status
# ------------------------------------------------------------------


@router.get("/status", response_class=JSONResponse)
async def status_json(request: Request):
    """Return session status as JSON."""
    session = _get_session(request)
    snap = session.snapshot()
    return _snapshot_to_dict(snap)


@router.get("/status-partial", response_class=HTMLResponse)
async def status_partial(request: Request):
    """Return the status panel as an HTMX partial."""
    templates = _get_templates(request)
    session = _get_session(request)
    watcher = _get_watcher(request)
    snap = session.snapshot()
    pairs = _get_pending_pairs(request)

    return templates.TemplateResponse(
        request,
        "partials/status.html",
        {
            "snapshot": snap,
            "pairs": pairs,
            "watcher_running": watcher.running,
            "watcher_mode": watcher.mode,
        },
    )


@router.get("/pairs-partial", response_class=HTMLResponse)
async def pairs_partial(request: Request):
    """Return the pending pairs list as an HTMX partial."""
    templates = _get_templates(request)
    pairs = _get_pending_pairs(request)

    return templates.TemplateResponse(
        request,
        "partials/pairs.html",
        {
            "pairs": pairs,
        },
    )


# ------------------------------------------------------------------
# SSE
# ------------------------------------------------------------------


@router.get("/events")
async def events(request: Request):
    """Server-Sent Events stream for real-time dashboard updates.

    Pushes ``event: status`` whenever the session state changes, plus
    a periodic heartbeat every 15 seconds to keep the connection alive.
    """
    sse_queue: asyncio.Queue[str] = request.app.state.sse_queue

    async def event_generator():
        while True:
            try:
                # Wait for an event or timeout for heartbeat
                msg = await asyncio.wait_for(sse_queue.get(), timeout=15.0)
                yield f"event: status\ndata: {msg}\n\n"
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
            except asyncio.CancelledError:
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _snapshot_to_dict(snap: SessionSnapshot) -> dict:
    """Convert a SessionSnapshot to a JSON-serializable dict."""
    armed = None
    if snap.armed_topic:
        armed = {
            "course_slug": snap.armed_topic.course_slug,
            "section_name": snap.armed_topic.section_name,
            "topic_name": snap.armed_topic.topic_name,
        }
    return {
        "state": snap.state.value,
        "armed_topic": armed,
        "obs_connected": snap.obs_connected,
        "last_output": str(snap.last_output) if snap.last_output else None,
        "error": snap.error,
    }


def _get_pending_pairs(request: Request) -> list:
    """Get pending pairs from the recordings root."""
    from clm.recordings.workflow.directories import find_pending_pairs, to_process_dir

    root = request.app.state.recordings_root
    raw_suffix = request.app.state.raw_suffix
    try:
        return find_pending_pairs(to_process_dir(root), raw_suffix=raw_suffix)
    except Exception:
        return []
