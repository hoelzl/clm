"""FastAPI application for the recordings workflow dashboard.

This is a **separate** app from the main ``clm serve`` dashboard.
It provides an HTMX-based UI for arming topics, monitoring OBS recording
state, and viewing pending/finished recordings.

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

    if obs is not None:
        try:
            obs.connect()
            logger.info("Connected to OBS on startup")
        except Exception as exc:
            logger.warning("Could not connect to OBS on startup: {}", exc)

    yield

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
) -> FastAPI:
    """Create the recordings dashboard FastAPI application.

    Args:
        recordings_root: Root directory (``to-process/``, ``final/``, ``archive/``).
        obs_host: OBS WebSocket host.
        obs_port: OBS WebSocket port.
        obs_password: OBS WebSocket password.
        spec_file: Optional CLM course spec XML file for lecture listing.
        raw_suffix: Raw filename suffix (default ``--RAW``).
    """
    from clm.recordings.workflow.directories import ensure_root
    from clm.recordings.workflow.obs import ObsClient
    from clm.recordings.workflow.session import RecordingSession

    app = FastAPI(
        title="CLM Recordings Dashboard",
        version=__version__,
        lifespan=lifespan,
    )

    # Ensure directory structure
    ensure_root(recordings_root)

    # OBS client and session manager
    obs = ObsClient(host=obs_host, port=obs_port, password=obs_password)

    # SSE event queue — session state changes are pushed here
    sse_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=256)

    def on_state_change(snapshot: object) -> None:
        """Push state change into the SSE queue (called from OBS thread)."""
        try:
            sse_queue.put_nowait("state_changed")
        except asyncio.QueueFull:
            pass  # Drop oldest-style: consumer will get the next one

    session = RecordingSession(
        obs,
        recordings_root,
        raw_suffix=raw_suffix,
        on_state_change=on_state_change,
    )

    # Store in app state for route handlers
    app.state.recordings_root = recordings_root
    app.state.raw_suffix = raw_suffix
    app.state.obs = obs
    app.state.session = session
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
