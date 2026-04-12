"""Route handlers for the recordings dashboard.

Provides:
- ``GET /`` — Dashboard page (Jinja2 template)
- ``GET /lectures`` — Lecture list from course (slide decks per section)
- ``POST /arm`` — Arm a slide deck for recording
- ``POST /disarm`` — Disarm the current deck
- ``GET /status`` — JSON session status snapshot
- ``GET /events`` — SSE stream for real-time updates
- ``GET /pairs`` — Pending pairs list (HTMX partial)
- ``POST /watcher/start`` — Start the file watcher
- ``POST /watcher/stop`` — Stop the file watcher
- ``POST /obs/connect`` — Connect to OBS WebSocket
- ``POST /obs/disconnect`` — Disconnect from OBS WebSocket
- ``GET /jobs`` — Processing jobs list (HTMX partial)
- ``POST /jobs/{id}/cancel`` — Cancel an in-flight job
- ``GET /backends`` — Active backend + capabilities JSON
- ``POST /set-lang`` — Set recording language cookie (de/en)
- ``POST /lectures/refresh`` — Rebuild Course from disk
"""

from __future__ import annotations

import asyncio
from typing import cast

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from loguru import logger

from clm.recordings.workflow.backends.base import ProcessingBackend
from clm.recordings.workflow.job_manager import JobManager
from clm.recordings.workflow.jobs import BackendCapabilities, ProcessingJob
from clm.recordings.workflow.session import RecordingSession, SessionSnapshot
from clm.recordings.workflow.watcher import RecordingsWatcher

router = APIRouter()


def _get_session(request: Request) -> RecordingSession:
    return cast(RecordingSession, request.app.state.session)


def _get_watcher(request: Request) -> RecordingsWatcher:
    return cast(RecordingsWatcher, request.app.state.watcher)


def _get_job_manager(request: Request) -> JobManager:
    return cast(JobManager, request.app.state.job_manager)


def _get_backend(request: Request) -> ProcessingBackend:
    return cast(ProcessingBackend, request.app.state.backend)


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
    job_manager = _get_job_manager(request)
    backend = _get_backend(request)
    snap = session.snapshot()
    pairs = _get_pending_pairs(request)
    jobs = _recent_jobs(job_manager)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "snapshot": snap,
            "pairs": pairs,
            "watcher_running": watcher.running,
            "watcher_mode": watcher.backend_name,
            "jobs": jobs,
            "backend": backend.capabilities,
        },
    )


@router.get("/lectures", response_class=HTMLResponse)
async def lectures(request: Request):
    """Render the lecture list showing slide decks per section.

    Uses the cached :class:`Course` object built at startup from the
    spec file and the course directory on disk.
    """
    templates = _get_templates(request)
    course = getattr(request.app.state, "course", None)
    session = _get_session(request)
    snap = session.snapshot()
    lang = _get_lang(request)

    sections: list[dict] = []
    course_slug: str = ""
    error: str | None = None

    if course is not None:
        try:
            from clm.recordings.workflow.deck_status import scan_section_deck_statuses
            from clm.recordings.workflow.naming import recording_relative_dir

            root = request.app.state.recordings_root
            raw_suffix = request.app.state.raw_suffix
            failed_jobs = _get_failed_jobs_map(request)

            for section in course.sections:
                decks = []
                section_name = section.name[lang]
                for nb in section.notebooks:
                    deck_name = nb.file_name(lang, "")
                    decks.append(
                        {
                            "deck_name": deck_name,
                            "display_name": deck_name,
                            "title_de": nb.title.de,
                            "title_en": nb.title.en,
                        }
                    )
                course_slug = course.output_dir_name[lang]
                rel_dir = recording_relative_dir(course_slug, section_name)
                statuses = scan_section_deck_statuses(
                    root,
                    str(rel_dir.parts[0]) if rel_dir.parts else course_slug,
                    str(rel_dir.parts[1]) if len(rel_dir.parts) > 1 else section_name,
                    [d["deck_name"] for d in decks],
                    raw_suffix=raw_suffix,
                    failed_jobs=failed_jobs,
                )
                for deck in decks:
                    deck["status"] = statuses.get(deck["deck_name"])
                sections.append(
                    {
                        "name": section_name,
                        "decks": decks,
                    }
                )
        except Exception as exc:
            error = str(exc)
            logger.warning("Could not build lecture list: {}", exc)
    elif request.app.state.spec_file is not None:
        error = (
            "Course spec file was provided but the Course could not be built. "
            "Check server logs for details."
        )
    else:
        error = "No course spec file configured. Pass --spec-file to clm recordings serve."

    return templates.TemplateResponse(
        request,
        "lectures.html",
        {
            "sections": sections,
            "course_slug": course_slug,
            "lang": lang,
            "snapshot": snap,
            "error": error,
        },
    )


# ------------------------------------------------------------------
# Actions
# ------------------------------------------------------------------


@router.post("/arm", response_class=HTMLResponse)
async def arm_deck(
    request: Request,
    course_slug: str = Form(...),
    section_name: str = Form(...),
    deck_name: str = Form(...),
    part_number: int = Form(0),
):
    """Arm a slide deck for the next recording."""
    session = _get_session(request)
    try:
        session.arm(course_slug, section_name, deck_name, part_number=part_number)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if _from_lectures(request):
        return HTMLResponse("", headers={"HX-Redirect": "/lectures"})
    return await status_partial(request)


@router.post("/disarm", response_class=HTMLResponse)
async def disarm(request: Request):
    """Disarm the currently armed topic."""
    session = _get_session(request)
    try:
        session.disarm()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if _from_lectures(request):
        return HTMLResponse("", headers={"HX-Redirect": "/lectures"})
    return await status_partial(request)


@router.post("/process", response_class=HTMLResponse)
async def process_file(request: Request, raw_path: str = Form(...)):
    """Manually submit a file for backend processing."""
    from pathlib import Path as _Path

    from clm.recordings.workflow.jobs import ProcessingOptions

    job_manager = _get_job_manager(request)
    path = _Path(raw_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {raw_path}")
    try:
        job_manager.submit(path, options=ProcessingOptions())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return HTMLResponse("", headers={"HX-Redirect": "/lectures"})


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
# OBS connection
# ------------------------------------------------------------------


@router.post("/obs/connect", response_class=HTMLResponse)
async def obs_connect(request: Request):
    """Connect to OBS WebSocket."""
    obs = request.app.state.obs
    try:
        obs.connect()
        logger.info("Connected to OBS via dashboard button")
    except Exception as exc:
        logger.warning("Failed to connect to OBS: {}", exc)
    return await status_partial(request)


@router.post("/obs/disconnect", response_class=HTMLResponse)
async def obs_disconnect(request: Request):
    """Disconnect from OBS WebSocket."""
    obs = request.app.state.obs
    obs.disconnect()
    logger.info("Disconnected from OBS via dashboard button")
    return await status_partial(request)


# ------------------------------------------------------------------
# Language selection
# ------------------------------------------------------------------


@router.post("/set-lang", response_class=HTMLResponse)
async def set_lang(request: Request, lang: str = Form(...)):
    """Set the recording language cookie and redirect back to lectures."""
    if lang not in ("de", "en"):
        lang = "de"
    response = HTMLResponse("", headers={"HX-Redirect": "/lectures"})
    response.set_cookie("clm_lang", lang, max_age=365 * 24 * 3600)
    return response


# ------------------------------------------------------------------
# Lectures refresh
# ------------------------------------------------------------------


@router.post("/lectures/refresh", response_class=HTMLResponse)
async def refresh_lectures(request: Request):
    """Rebuild the Course object from disk (picks up title changes, new slides)."""
    from .app import _build_course

    spec_file = request.app.state.spec_file
    if spec_file is not None:
        request.app.state.course = _build_course(spec_file)
    return HTMLResponse("", headers={"HX-Redirect": "/lectures"})


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
            "watcher_mode": watcher.backend_name,
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


def _from_lectures(request: Request) -> bool:
    """Check whether the HTMX request originated from the lectures page."""
    current_url = request.headers.get("hx-current-url", "")
    return "/lectures" in current_url


def _get_lang(request: Request) -> str:
    """Return the recording language from the ``clm_lang`` cookie (default ``"de"``)."""
    return request.cookies.get("clm_lang", "de")


def _get_failed_jobs_map(request: Request) -> dict[str, str]:
    """Build a mapping of deck names to failed job IDs.

    Scans recent jobs for those in FAILED state and maps the raw path's
    base name (deck name with part suffix stripped) to the job ID.
    """
    from clm.recordings.workflow.jobs import JobState
    from clm.recordings.workflow.naming import parse_part, parse_raw_stem

    manager = _get_job_manager(request)
    raw_suffix = request.app.state.raw_suffix
    result: dict[str, str] = {}
    try:
        for job in manager.list_jobs():
            if job.state == JobState.FAILED:
                base_with_part, _ = parse_raw_stem(job.raw_path.stem, raw_suffix)
                base, _ = parse_part(base_with_part)
                result.setdefault(base, job.id)
    except Exception:
        pass
    return result


def _snapshot_to_dict(snap: SessionSnapshot) -> dict:
    """Convert a SessionSnapshot to a JSON-serializable dict."""
    armed = None
    if snap.armed_deck:
        armed = {
            "course_slug": snap.armed_deck.course_slug,
            "section_name": snap.armed_deck.section_name,
            "deck_name": snap.armed_deck.deck_name,
            "part_number": snap.armed_deck.part_number,
        }
    return {
        "state": snap.state.value,
        "armed_deck": armed,
        # Deprecated — kept for API consumers during transition
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


def _recent_jobs(manager: JobManager, *, limit: int = 20) -> list[ProcessingJob]:
    """Return the most recent *limit* jobs from *manager*, newest first.

    Thin wrapper so routes don't need to know about the manager's
    ``list_jobs()`` shape. The manager returns jobs newest-first already.
    """
    try:
        return manager.list_jobs()[:limit]
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Failed to list jobs: {}", exc)
        return []


def _capabilities_to_dict(caps: BackendCapabilities) -> dict:
    """Serialize :class:`BackendCapabilities` to a plain dict for JSON output."""
    return {
        "name": caps.name,
        "display_name": caps.display_name,
        "description": caps.description,
        "video_in_video_out": caps.video_in_video_out,
        "is_synchronous": caps.is_synchronous,
        "requires_internet": caps.requires_internet,
        "requires_api_key": caps.requires_api_key,
        "supports_cut_lists": caps.supports_cut_lists,
        "supports_filler_removal": caps.supports_filler_removal,
        "supports_silence_removal": caps.supports_silence_removal,
        "supports_transcript": caps.supports_transcript,
        "supports_chapter_detection": caps.supports_chapter_detection,
        "max_file_size_mb": caps.max_file_size_mb,
        "supported_input_extensions": list(caps.supported_input_extensions),
    }


# ------------------------------------------------------------------
# Jobs (Phase C)
# ------------------------------------------------------------------


@router.get("/jobs", response_class=HTMLResponse)
async def jobs_partial(request: Request):
    """Return the processing-jobs panel as an HTMX partial."""
    templates = _get_templates(request)
    manager = _get_job_manager(request)
    backend = _get_backend(request)

    return templates.TemplateResponse(
        request,
        "partials/jobs.html",
        {
            "jobs": _recent_jobs(manager),
            "backend": backend.capabilities,
        },
    )


@router.post("/jobs/{job_id}/cancel", response_class=HTMLResponse)
async def cancel_job(request: Request, job_id: str):
    """Cancel an in-flight job by id and return the refreshed jobs panel."""
    manager = _get_job_manager(request)
    updated = manager.cancel(job_id)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"No job with id {job_id}")
    # Re-render the panel so HTMX can swap it in place.
    return await jobs_partial(request)


@router.get("/backends", response_class=JSONResponse)
async def backends_info(request: Request):
    """Return the active backend and its capabilities as JSON.

    The dashboard JavaScript uses this for conditional UI (e.g. showing
    a "Cut list" checkbox only when the backend supports cut lists).
    """
    backend = _get_backend(request)
    return {
        "active": backend.capabilities.name,
        "capabilities": _capabilities_to_dict(backend.capabilities),
    }
