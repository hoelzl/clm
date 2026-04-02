"""FastAPI application for the recordings workflow dashboard.

This is a **separate** app from the main ``clm serve`` dashboard.
It provides an HTMX-based UI for arming topics, monitoring OBS recording
state, viewing pending/finished recordings, and managing the file watcher.

Launch with ``clm recordings serve``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger

from clm.__version__ import __version__

from .routes import router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Application lifespan: connect to OBS on startup, disconnect on shutdown."""
    obs = getattr(app.state, "obs", None)
    watcher = getattr(app.state, "watcher", None)

    if obs is not None:
        try:
            obs.connect()
            logger.info("Connected to OBS on startup")
        except Exception as exc:
            logger.warning("Could not connect to OBS on startup: {}", exc)

    yield

    if watcher is not None:
        watcher.stop()

    if obs is not None:
        obs.disconnect()


def create_app(
    recordings_root: Path,
    *,
    obs_host: str = "localhost",
    obs_port: int = 4455,
    obs_password: str = "",
    spec_file: Path | None = None,
    raw_suffix: str = "--RAW",
    processing_backend: str = "external",
    stability_check_interval: float = 2.0,
    stability_check_count: int = 3,
) -> FastAPI:
    """Create the recordings dashboard FastAPI application.

    Args:
        recordings_root: Root directory (``to-process/``, ``final/``, ``archive/``).
        obs_host: OBS WebSocket host.
        obs_port: OBS WebSocket port.
        obs_password: OBS WebSocket password.
        spec_file: Optional CLM course spec XML file for lecture listing.
        raw_suffix: Raw filename suffix (default ``--RAW``).
        processing_backend: ``"external"`` or ``"onnx"``.
        stability_check_interval: Seconds between file-size polls.
        stability_check_count: Consecutive identical polls = stable.
    """
    from clm.recordings.workflow.directories import ensure_root
    from clm.recordings.workflow.obs import ObsClient
    from clm.recordings.workflow.session import RecordingSession
    from clm.recordings.workflow.watcher import RecordingsWatcher

    app = FastAPI(
        title="CLM Recordings Dashboard",
        version=__version__,
        lifespan=lifespan,
    )

    # Ensure directory structure
    ensure_root(recordings_root)

    # OBS client and session manager
    obs = ObsClient(host=obs_host, port=obs_port, password=obs_password)

    # SSE event queue — session and watcher events are pushed here
    sse_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=256)

    def _push_sse(event: str) -> None:
        try:
            sse_queue.put_nowait(event)
        except asyncio.QueueFull:
            pass

    def on_state_change(snapshot: object) -> None:
        """Push state change into the SSE queue (called from OBS thread)."""
        _push_sse("state_changed")

    session = RecordingSession(
        obs,
        recordings_root,
        raw_suffix=raw_suffix,
        on_state_change=on_state_change,
    )

    # File watcher
    watcher = RecordingsWatcher(
        recordings_root,
        backend=processing_backend,
        raw_suffix=raw_suffix,
        stability_interval=stability_check_interval,
        stability_checks=stability_check_count,
        on_assembled=lambda result: _push_sse("assembled"),
        on_processing=lambda path: _push_sse("processing"),
        on_error=lambda path, err: _push_sse("watcher_error"),
    )

    # Store in app state for route handlers
    app.state.recordings_root = recordings_root
    app.state.raw_suffix = raw_suffix
    app.state.obs = obs
    app.state.session = session
    app.state.watcher = watcher
    app.state.sse_queue = sse_queue
    app.state.spec_file = spec_file

    # Templates and static files
    templates_dir = Path(__file__).parent / "templates"
    static_dir = Path(__file__).parent / "static"

    app.state.templates = Jinja2Templates(directory=str(templates_dir))

    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Include routes
    app.include_router(router)

    return app
