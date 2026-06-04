"""Route handlers for the recordings dashboard.

Provides:
- ``GET /`` — Dashboard page (Jinja2 template)
- ``GET /lectures`` — Lecture list from course (slide decks per section)
- ``POST /arm`` — Arm a slide deck for recording (low-level primitive)
- ``POST /disarm`` — Disarm the current deck (low-level primitive)
- ``POST /record`` — Arm + start OBS recording in one step
- ``POST /stop`` — Tell OBS to stop the current recording
- ``POST /pause`` — Tell OBS to pause the current recording
- ``POST /resume`` — Tell OBS to resume a paused recording
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
import json
from typing import cast

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from loguru import logger

from clm.recordings.state import CourseRecordingState
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


#: HX-Location payload that re-fetches ``/lectures`` and swaps only the
#: ``#lectures-dynamic`` subtree. Used by every lecture-page action
#: instead of ``HX-Redirect: /lectures``. ``HX-Redirect`` triggers a
#: full browser navigation, which resets ``window.scrollY`` to 0 — the
#: user had to scroll back to their deck after every click. ``HX-Location``
#: stays on the same document and swaps only the dynamic content, so
#: scroll position is preserved naturally.
_LECTURES_REFRESH_LOCATION = json.dumps(
    {
        "path": "/lectures",
        "target": "#lectures-dynamic",
        "select": "#lectures-dynamic",
        "swap": "outerHTML",
    }
)


def _lectures_refresh_response() -> HTMLResponse:
    """Return a response that refreshes ``#lectures-dynamic`` in-place.

    Replacement for ``HTMLResponse("", headers={"HX-Redirect": "/lectures"})``
    on routes invoked from the lectures page. Preserves scroll position
    because no full-page navigation happens.
    """
    return HTMLResponse("", headers={"HX-Location": _LECTURES_REFRESH_LOCATION})


def _resolve_lecture_id(section_name: str, deck_name: str) -> str:
    """Derive a stable ``lecture_id`` for *(section_name, deck_name)*.

    Used by ``/arm`` and ``/record`` to give the session a per-deck
    key it can use against :class:`CourseRecordingState`. The choice
    is deliberately simple — concatenating the two names with a
    separator that cannot appear in either side — because the
    identifier is only used internally (never surfaced to end users).
    """
    return f"{section_name}::{deck_name}"


def _scan_active_take_for_panel(
    *,
    root,
    course_slug: str,
    section_name: str,
    deck_name: str,
    part: int,
    raw_suffix: str,
):
    """Build a ``TakeFileInfo`` for the active (unsuffixed) take from disk.

    Returns ``None`` when neither a raw nor a final file exists for
    this (deck, part) — i.e. nothing has been recorded yet. The take
    number is left at ``0`` and the route handler patches it from
    ``state.json``; filesystem alone can't reveal the right number
    after a restore (the active slot's filename has no take suffix).
    """
    from clm.core.utils.text_utils import sanitize_file_name
    from clm.recordings.processing.batch import VIDEO_EXTENSIONS
    from clm.recordings.workflow.deck_status import TakeFileInfo
    from clm.recordings.workflow.directories import (
        archive_dir,
        final_dir,
        to_process_dir,
    )
    from clm.recordings.workflow.naming import parse_part, parse_raw_stem

    sanitized = sanitize_file_name(deck_name)
    rel = f"{sanitize_file_name(course_slug)}/{sanitize_file_name(section_name)}"

    raw_path = None
    raw_mtime: float | None = None
    for base_dir in (to_process_dir(root), archive_dir(root)):
        subtree = base_dir / rel
        if not subtree.is_dir():
            continue
        for child in subtree.iterdir():
            if not child.is_file() or child.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            base_with, is_raw = parse_raw_stem(child.stem, raw_suffix)
            if not is_raw:
                continue
            base, p = parse_part(base_with)
            if base != sanitized or p != part:
                continue
            raw_path = child
            try:
                raw_mtime = child.stat().st_mtime
            except OSError:
                pass
            break
        if raw_path is not None:
            break

    final_path = None
    final_mtime: float | None = None
    final_subtree = final_dir(root) / rel
    if final_subtree.is_dir():
        for child in final_subtree.iterdir():
            if not child.is_file() or child.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            base, p = parse_part(child.stem)
            if base == sanitized and p == part:
                final_path = child
                try:
                    final_mtime = child.stat().st_mtime
                except OSError:
                    pass
                break

    if raw_path is None and final_path is None:
        return None

    recorded_at = max(m for m in (raw_mtime, final_mtime) if m is not None)
    return TakeFileInfo(
        take=0,
        raw_path=raw_path,
        final_path=final_path,
        recorded_at=recorded_at,
    )


def _build_deck_provenance(request: Request, section_name: str, deck_name: str, lang: str):
    """Assemble slide-version/git provenance for the deck being armed (issue #208).

    Reads the built ``Course`` and spec file from app state — both present
    only when the dashboard was launched with ``--spec-file``. Returns
    ``None`` (no provenance) when no course is configured. Best-effort: the
    assembler itself never raises, but the whole step is also wrapped so a
    provenance failure can never block an arm/record action.
    """
    course = getattr(request.app.state, "course", None)
    spec_file = getattr(request.app.state, "spec_file", None)
    if course is None and spec_file is None:
        return None
    try:
        from clm.recordings.record_provenance import build_record_provenance

        return build_record_provenance(course, spec_file, section_name, deck_name, lang)
    except Exception as exc:  # pragma: no cover — defensive; assembler is total
        logger.warning("Could not assemble recording provenance: {}", exc)
        return None


def _get_or_load_course_state(request: Request, course_slug: str) -> CourseRecordingState:
    """Return the cached :class:`CourseRecordingState` for *course_slug*.

    Loads the existing state file from disk on first use, or creates a
    fresh empty state if no file exists yet. Subsequent calls return
    the cached in-memory instance so mutations from the session thread
    and the request thread share the same object.
    """
    cache = cast(dict[str, CourseRecordingState], request.app.state.recording_states)
    cached = cache.get(course_slug)
    if cached is not None:
        return cached
    loader = request.app.state.load_course_state
    fresh: CourseRecordingState = loader(course_slug)
    cache[course_slug] = fresh
    return fresh


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
            failed_per_part = _get_failed_jobs_per_part(request)
            active_per_part = _get_active_jobs_per_part(request)
            failed_jobs = _deck_level_jobs(failed_per_part)
            active_jobs = _deck_level_jobs(active_per_part)

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
                    active_jobs=active_jobs,
                    failed_jobs_per_part=failed_per_part,
                    active_jobs_per_part=active_per_part,
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
    lang = _get_lang(request)
    if not request.app.state.obs.connected:
        raise HTTPException(status_code=409, detail="OBS not connected")
    lecture_id = _resolve_lecture_id(section_name, deck_name)
    state = _get_or_load_course_state(request, course_slug)
    state.ensure_lecture(lecture_id, deck_name)
    provenance = _build_deck_provenance(request, section_name, deck_name, lang)
    try:
        session.arm(
            course_slug,
            section_name,
            deck_name,
            part_number=part_number,
            lang=lang,
            lecture_id=lecture_id,
            provenance=provenance,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if _from_lectures(request):
        return _lectures_refresh_response()
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
        return _lectures_refresh_response()
    return await status_partial(request)


@router.post("/record", response_class=HTMLResponse)
async def record_deck(
    request: Request,
    course_slug: str = Form(...),
    section_name: str = Form(...),
    deck_name: str = Form(...),
    part_number: int = Form(0),
):
    """Arm a deck and start OBS recording in one step.

    If OBS rejects the start request (e.g. not connected or already
    recording), the deck is left armed and a 502 is returned with the
    OBS error. The user can retry via the lower-level ``/arm`` route or
    start OBS manually.
    """
    session = _get_session(request)
    lang = _get_lang(request)
    if not request.app.state.obs.connected:
        raise HTTPException(status_code=409, detail="OBS not connected")
    lecture_id = _resolve_lecture_id(section_name, deck_name)
    state = _get_or_load_course_state(request, course_slug)
    state.ensure_lecture(lecture_id, deck_name)
    provenance = _build_deck_provenance(request, section_name, deck_name, lang)
    try:
        session.record(
            course_slug,
            section_name,
            deck_name,
            part_number=part_number,
            lang=lang,
            lecture_id=lecture_id,
            provenance=provenance,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ConnectionError as exc:
        logger.warning("OBS rejected start_record: {}", exc)
        _push_notice(request, "error", f"OBS rejected record start: {exc}")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if _from_lectures(request):
        return _lectures_refresh_response()
    return await status_partial(request)


@router.post("/advance", response_class=HTMLResponse)
async def advance_take(
    request: Request,
    course_slug: str = Form(...),
    section_name: str = Form(...),
    deck_name: str = Form(...),
    part_number: int = Form(0),
):
    """Demote the active take for ``(deck, part)`` into ``takes/`` without recording.

    Runs :meth:`RecordingSession.advance_take` — the same preserve-take
    cascade a retake would trigger, but without starting a new OBS
    recording. Lets the user slot the current recording into the take
    history (e.g. because they noticed a mistake mid-session) without
    having to record a throwaway just to trigger the demote.
    """
    session = _get_session(request)
    lang = _get_lang(request)
    lecture_id = _resolve_lecture_id(section_name, deck_name)
    try:
        preserved = session.advance_take(
            course_slug,
            section_name,
            deck_name,
            part_number=part_number,
            lang=lang,
            lecture_id=lecture_id,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if preserved:
        _push_notice(
            request,
            "success",
            f"Advanced take: demoted {len(preserved)} file{'s' if len(preserved) != 1 else ''} to takes/",
        )
    else:
        _push_notice(
            request,
            "info",
            f"No active take to advance for {deck_name}",
        )

    if _from_lectures(request):
        return _lectures_refresh_response()
    return await status_partial(request)


@router.post("/stop", response_class=HTMLResponse)
async def stop_recording(request: Request):
    """Tell OBS to stop the current recording.

    The session's existing STOPPED-event handler takes care of the
    rename. Returns the status partial so the dashboard updates.
    """
    session = _get_session(request)
    try:
        session.stop()
    except ConnectionError as exc:
        logger.warning("OBS rejected stop_record: {}", exc)
        _push_notice(request, "error", f"OBS rejected stop: {exc}")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if _from_lectures(request):
        return _lectures_refresh_response()
    return await status_partial(request)


@router.post("/pause", response_class=HTMLResponse)
async def pause_recording(request: Request):
    """Tell OBS to pause the current recording.

    The session's ``RecordStateChanged`` handler transitions to
    :class:`SessionState.PAUSED` when OBS confirms the pause.
    """
    session = _get_session(request)
    try:
        session.pause()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ConnectionError as exc:
        logger.warning("OBS rejected pause_record: {}", exc)
        _push_notice(request, "error", f"OBS rejected pause: {exc}")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if _from_lectures(request):
        return _lectures_refresh_response()
    return await status_partial(request)


@router.post("/resume", response_class=HTMLResponse)
async def resume_recording(request: Request):
    """Tell OBS to resume a paused recording."""
    session = _get_session(request)
    try:
        session.resume()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ConnectionError as exc:
        logger.warning("OBS rejected resume_record: {}", exc)
        _push_notice(request, "error", f"OBS rejected resume: {exc}")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if _from_lectures(request):
        return _lectures_refresh_response()
    return await status_partial(request)


@router.post("/process", response_class=HTMLResponse)
async def process_file(request: Request):
    """Manually submit one or more files for backend processing."""
    from pathlib import Path as _Path

    from clm.recordings.workflow.jobs import ProcessingOptions

    form = await request.form()
    raw_paths = form.getlist("raw_path")
    if not raw_paths:
        raise HTTPException(status_code=400, detail="No raw_path provided")

    job_manager = _get_job_manager(request)
    submitted = 0
    for raw_path in raw_paths:
        path = _Path(str(raw_path))
        if not path.exists():
            logger.warning("Skipping missing file: {}", raw_path)
            _push_notice(request, "warning", f"Skipped missing file: {path.name}")
            continue
        try:
            # submit_async returns immediately; the actual blocking
            # backend.submit (Auphonic upload, etc.) runs on a worker
            # thread. Keeps /process responsive regardless of backend.
            job_manager.submit_async(path, options=ProcessingOptions())
            submitted += 1
        except Exception as exc:
            logger.warning("Failed to submit {}: {}", raw_path, exc)
            _push_notice(request, "error", f"Failed to submit {path.name}: {exc}")
    if submitted:
        _push_notice(
            request,
            "success",
            f"Submitted {submitted} file{'s' if submitted != 1 else ''} for processing",
        )
    return _lectures_refresh_response()


# ------------------------------------------------------------------
# Take history (Phase C — UI parts-inline display)
# ------------------------------------------------------------------


@router.get(
    "/decks/{course_slug}/{section_name}/{deck_name}/takes",
    response_class=HTMLResponse,
)
async def deck_takes(
    request: Request,
    course_slug: str,
    section_name: str,
    deck_name: str,
    part: int = 0,
):
    """Return the inline take-history panel for ``(course, section, deck, part)``.

    Lazy-fetched by the chip strip in the lectures UI. The panel
    renders the active take as the first row (badged "Active") so the
    user has a single place for every take's metadata and Open
    affordance, followed by one row per superseded take from
    ``takes/<course>/<section>/`` matching the deck and part. The
    Restore button on history rows is disabled when the session has
    this exact deck armed/recording (defense in depth alongside the
    409 returned by the restore route).

    Filesystem is the source of truth for the active row's paths and
    status — the manual ``/process`` flow doesn't update
    ``state.json`` so a state-only view would still show ``recorded``
    after Auphonic finished. We scan ``to-process/``, ``archive/`` and
    ``final/`` for files in the unsuffixed (active) slot and only fall
    back to ``state.json`` for the take number, which is the one
    thing the filesystem can't tell us correctly post-restore.
    """
    from clm.recordings.workflow.deck_status import TakeFileInfo, scan_take_files

    templates = _get_templates(request)
    root = request.app.state.recordings_root
    raw_suffix = request.app.state.raw_suffix
    takes = scan_take_files(
        root,
        course_slug,
        section_name,
        deck_name,
        part=part,
        raw_suffix=raw_suffix,
    )
    session = _get_session(request)
    armed = session.armed_deck
    panel_locked = (
        armed is not None
        and armed.course_slug == course_slug
        and armed.section_name == section_name
        and armed.deck_name == deck_name
    )

    active = _scan_active_take_for_panel(
        root=root,
        course_slug=course_slug,
        section_name=section_name,
        deck_name=deck_name,
        part=part,
        raw_suffix=raw_suffix,
    )
    if active is not None:
        # Take number lives in state.json — filesystem can't recover it
        # post-restore. Falls back to None when no state exists, which
        # the template renders as a "?" placeholder.
        course_state = _get_or_load_course_state(request, course_slug)
        state_part = part if part > 0 else 1
        lecture = course_state.get_lecture(_resolve_lecture_id(section_name, deck_name))
        part_obj = (
            next((p for p in lecture.parts if p.part == state_part), None)
            if lecture is not None
            else None
        )
        if part_obj is not None:
            active = TakeFileInfo(
                take=part_obj.active_take,
                raw_path=active.raw_path,
                final_path=active.final_path,
                raw_size=active.raw_size,
                final_size=active.final_size,
                recorded_at=active.recorded_at,
            )

    return templates.TemplateResponse(
        request,
        "partials/takes.html",
        {
            "course_slug": course_slug,
            "section_name": section_name,
            "deck_name": deck_name,
            "part": part,
            "takes": takes,
            "active": active,
            "panel_locked": panel_locked,
            "restore_url_for": lambda t: (
                f"/decks/{course_slug}/{section_name}/{deck_name}/takes/{t.take}/restore?part={part}"
            ),
        },
    )


@router.post(
    "/decks/{course_slug}/{section_name}/{deck_name}/takes/{take}/restore",
    response_class=HTMLResponse,
)
async def restore_take(
    request: Request,
    course_slug: str,
    section_name: str,
    deck_name: str,
    take: int,
    part: int = 0,
):
    """Promote historical take *take* back to active for ``(deck, part)``.

    Runs :meth:`RecordingSession.restore_take`, which performs the
    filesystem swap with planned-rename rollback and updates the
    course-state index. The previously active take becomes a history
    entry under its existing take number, so a subsequent retake on the
    restored slot allocates a fresh take number rather than clobbering
    history.

    Returns the lectures-page refresh response so the chip strip and
    take history panel reflect the new active take.

    Status codes:
    - 200: Restore completed.
    - 404: ``take`` does not exist for this part, or the part has no
      state record.
    - 409: Session is busy (recording, paused, or renaming).
    """
    session = _get_session(request)
    lang = _get_lang(request)
    lecture_id = _resolve_lecture_id(section_name, deck_name)
    try:
        session.restore_take(
            course_slug,
            section_name,
            deck_name,
            take,
            part_number=part,
            lang=lang,
            lecture_id=lecture_id,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    _push_notice(
        request,
        "success",
        f"Restored take {take} for {deck_name}",
    )

    if _from_lectures(request):
        return _lectures_refresh_response()
    return await status_partial(request)


# ------------------------------------------------------------------
# Open in OS file browser (replacement for blocked file:/// links)
# ------------------------------------------------------------------


@router.post("/open-explorer", response_class=HTMLResponse)
async def open_explorer(request: Request, path: str = Form(...)):
    """Open the OS file browser pointed at *path* (file selected).

    Replaces ``<a href="file:///...">`` from the take-history panel —
    modern browsers refuse to follow ``file://`` links from an
    ``http://`` origin, so the Open button needs a server-side
    detour. The path must resolve under the configured recordings
    root (defense against an attacker submitting an arbitrary path
    via a forged form).

    On Windows we use ``explorer /select,<path>`` so the file is
    pre-selected; macOS uses ``open -R``; Linux falls back to
    ``xdg-open`` on the parent directory (no built-in "select"
    semantics across desktops).
    """
    import subprocess
    import sys
    from pathlib import Path as _Path

    target = _Path(path)
    try:
        target_resolved = target.resolve(strict=True)
    except (OSError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=f"Path not found: {path}") from exc

    root = _Path(request.app.state.recordings_root).resolve()
    try:
        target_resolved.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Path is outside the recordings root") from exc

    try:
        if sys.platform == "win32":
            # explorer.exe parses its own command line and is finicky:
            # passing ``["/select,", path]`` via the argv list inserts a
            # space after the comma, which makes Explorer fall back to
            # opening Documents instead of selecting the file. The
            # documented workaround is to pass a single command string
            # with the path quoted inline. ``resolve(strict=True)``
            # above guarantees the path is absolute and points at an
            # existing file under the recordings root, and Windows
            # disallows ``"`` in filenames, so embedding the path in
            # double quotes is safe here.
            subprocess.Popen(  # noqa: S603 - explorer is trusted, path is validated
                f'explorer /select,"{target_resolved}"'
            )
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(target_resolved)])  # noqa: S603, S607
        else:
            subprocess.Popen(["xdg-open", str(target_resolved.parent)])  # noqa: S603, S607
    except Exception as exc:
        logger.warning("Failed to open file browser for {}: {}", target_resolved, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return HTMLResponse("")


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
        _push_notice(request, "error", f"Could not connect to OBS: {exc}")
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
    response = _lectures_refresh_response()
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
    return _lectures_refresh_response()


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


NOTICE_PREFIX = "notice:"


def _sse_event_name_for(payload: str) -> str:
    """Classify an SSE queue *payload* into an ``event:`` name.

    Three buckets:

    * ``"notice:<level>|<message>"`` → ``event: notice``. Routed to the
      toast region; the ``notice:`` prefix is stripped from the data
      payload by the SSE generator.
    * Job-lifecycle payloads (``"job"``, ``"job:<id>"``,
      ``"submitted:<id>"``) → ``event: job``. Delivered to the
      Processing Jobs panel.
    * Everything else → ``event: status``. Delivered to the Status
      panel and the Lectures-page OBS banner.
    """
    if payload.startswith(NOTICE_PREFIX):
        return "notice"
    return "job" if payload.startswith(("job", "submitted")) else "status"


def _sse_payload_for(payload: str) -> str:
    """Return the ``data:`` body for an SSE *payload*.

    Strips the ``notice:`` prefix so clients receive the
    ``<level>|<message>`` pair ready to feed into ``showToast``.
    """
    if payload.startswith(NOTICE_PREFIX):
        return payload[len(NOTICE_PREFIX) :]
    return payload


def _push_notice(request: Request, level: str, message: str) -> None:
    """Push a toast notice onto the SSE stream.

    *level* is one of ``"info"``, ``"success"``, ``"warning"``, or
    ``"error"`` (matches the ``toast-<level>`` CSS classes). Payload
    format is ``"notice:<level>|<message>"`` — the pipe is a cheap
    delimiter that avoids a JSON round-trip.

    No-ops when the app has not wired ``push_sse`` (early-lifecycle
    smoke tests and the rare unit that uses ``create_app`` without
    running lifespan).
    """
    push = getattr(request.app.state, "push_sse", None)
    if push is None:
        return
    # Flatten newlines so the raw payload stays on a single SSE data line.
    safe_message = message.replace("\n", " ").replace("\r", " ")
    push(f"notice:{level}|{safe_message}")


@router.get("/events")
async def events(request: Request):
    """Server-Sent Events stream for real-time dashboard updates.

    Emits two event names so the dashboard can route updates without
    cross-refreshing every panel on every tick:

    * ``event: job`` — job-lifecycle messages (``"job"``, ``"job:<id>"``,
      ``"submitted:<id>"``). Delivered to the Processing Jobs panel.
    * ``event: status`` — everything else (session state changes, OBS
      connect/disconnect, watcher start/stop). Delivered to the Status
      panel.

    A periodic heartbeat (``: heartbeat``) keeps idle connections alive.

    Each client gets its own queue so every connected tab receives every
    event — a shared queue would round-robin events between tabs, which
    is how the lectures + dashboard combo ended up missing updates.
    """
    subscribers: list[asyncio.Queue[str]] = request.app.state.sse_subscribers
    my_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=256)
    subscribers.append(my_queue)

    # Seed the new subscriber so a fresh page load catches up with any
    # state transitions fired during the reconnect gap. Without this,
    # ``/record`` kicks off ``HX-Redirect: /lectures``, the browser
    # reloads, and the OBS ``RecordStateChanged`` event that arrives
    # while no subscriber is attached disappears — leaving the badge
    # stuck at ``armed``. The primed event is a plain status ping, so
    # the lectures page does one extra ``GET /lectures`` immediately
    # after SSE attaches and pulls whatever the server sees right now.
    my_queue.put_nowait("state_changed")

    async def event_generator():
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(my_queue.get(), timeout=15.0)
                    event_name = _sse_event_name_for(msg)
                    data = _sse_payload_for(msg)
                    yield f"event: {event_name}\ndata: {data}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                except asyncio.CancelledError:
                    break
        finally:
            try:
                subscribers.remove(my_queue)
            except ValueError:
                pass

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


def _get_active_jobs_per_part(request: Request) -> dict[tuple[str, int], str]:
    """Build a mapping of ``(deck, part) -> job_id`` for non-terminal jobs.

    Used by the chip strip in the lectures UI to mark individual chips
    as ``processing``. Newest-first iteration with per-slot dedupe so
    the most recent job per ``(deck, part)`` wins — same scheme as
    :func:`_get_failed_jobs_per_part` for symmetry.
    """
    from clm.recordings.workflow.jobs import JobState
    from clm.recordings.workflow.naming import parse_part, parse_raw_stem

    manager = _get_job_manager(request)
    raw_suffix = request.app.state.raw_suffix
    terminal = {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED}
    result: dict[tuple[str, int], str] = {}
    try:
        for job in manager.list_jobs():
            base_with_part, _ = parse_raw_stem(job.raw_path.stem, raw_suffix)
            base, part = parse_part(base_with_part)
            slot = (base, part)
            if slot in result:
                continue
            if job.state not in terminal:
                result[slot] = job.id
    except Exception:
        pass
    return result


def _get_failed_jobs_per_part(request: Request) -> dict[tuple[str, int], str]:
    """Build a mapping of ``(deck, part) -> job_id`` for the slot's most recent FAILED job.

    ``list_jobs()`` returns newest-first. We dedupe per ``(deck,
    part)`` slot — a retake of a specific part should clear the
    indicator for that slot, but a successful part 2 must not mask an
    unresolved part-1 failure. Only slots whose newest job is FAILED
    end up in the result.
    """
    from clm.recordings.workflow.jobs import JobState
    from clm.recordings.workflow.naming import parse_part, parse_raw_stem

    manager = _get_job_manager(request)
    raw_suffix = request.app.state.raw_suffix
    result: dict[tuple[str, int], str] = {}
    seen_slots: set[tuple[str, int]] = set()
    try:
        for job in manager.list_jobs():
            base_with_part, _ = parse_raw_stem(job.raw_path.stem, raw_suffix)
            base, part = parse_part(base_with_part)
            slot = (base, part)
            if slot in seen_slots:
                continue
            seen_slots.add(slot)
            if job.state == JobState.FAILED:
                result[slot] = job.id
    except Exception:
        pass
    return result


def _deck_level_jobs(per_part: dict[tuple[str, int], str]) -> dict[str, str]:
    """Collapse a per-``(deck, part)`` map into ``deck -> first job_id``.

    Used to keep the deck-level badge ("processing failed", "processing")
    semantics unchanged when the chip strip's per-part view is added.
    """
    out: dict[str, str] = {}
    for (deck, _part), job_id in per_part.items():
        out.setdefault(deck, job_id)
    return out


def _snapshot_to_dict(snap: SessionSnapshot) -> dict:
    """Convert a SessionSnapshot to a JSON-serializable dict."""
    armed = None
    if snap.armed_deck:
        armed = {
            "course_slug": snap.armed_deck.course_slug,
            "section_name": snap.armed_deck.section_name,
            "deck_name": snap.armed_deck.deck_name,
            "part_number": snap.armed_deck.part_number,
            "lang": snap.armed_deck.lang,
        }
    return {
        "state": snap.state.value,
        "armed_deck": armed,
        # Deprecated — kept for API consumers during transition
        "armed_topic": armed,
        "obs_connected": snap.obs_connected,
        "obs_state": snap.obs_state,
        "last_output": str(snap.last_output) if snap.last_output else None,
        "error": snap.error,
        "recording_elapsed_seconds": snap.recording_elapsed_seconds,
        "paused": snap.paused,
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


@router.post("/jobs/{job_id}/reconcile", response_class=HTMLResponse)
async def reconcile_job(request: Request, job_id: str):
    """Verify a job's displayed state against upstream + filesystem.

    Triggers the backend's ``reconcile`` hook. Useful when the user
    sees a stuck ``FAILED`` job whose upstream work actually finished —
    a common scenario after a server restart during an Auphonic
    production. Returns the refreshed jobs panel so HTMX can swap it.
    """
    manager = _get_job_manager(request)
    updated = manager.reconcile(job_id)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"No job with id {job_id}")
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
