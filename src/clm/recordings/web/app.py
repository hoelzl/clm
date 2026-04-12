"""FastAPI application for the recordings workflow dashboard.

This is a **separate** app from the main ``clm serve`` dashboard.
It provides an HTMX-based UI for arming slide decks, monitoring OBS
recording state, viewing pending/finished recordings, and managing the
file watcher.

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


def _build_course(spec_file: Path) -> object | None:
    """Build a :class:`Course` from *spec_file*, or ``None`` on failure.

    Returns the Course object (typed as ``object`` to avoid importing
    heavy core modules at module level).
    """
    try:
        from clm.core.course import Course
        from clm.core.course_spec import CourseSpec

        spec = CourseSpec.from_file(spec_file)
        course_root = spec_file.parent
        return Course.from_spec(spec, course_root, output_root=course_root)
    except Exception as exc:
        logger.warning("Could not build Course from {}: {}", spec_file, exc)
        return None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Application lifespan: connect to OBS on startup, disconnect on shutdown.

    Also captures the running event loop into ``app.state.event_loop`` so
    thread-bound callbacks (``_on_job_event``, OBS state-change) can
    marshal events back onto the loop via :func:`asyncio.run_coroutine_threadsafe`
    or :meth:`asyncio.AbstractEventLoop.call_soon_threadsafe`.
    """
    obs = getattr(app.state, "obs", None)
    watcher = getattr(app.state, "watcher", None)
    job_manager = getattr(app.state, "job_manager", None)

    # Stash the running loop so cross-thread SSE pushes can marshal
    # work correctly. Must be done *before* connecting OBS or starting
    # the job poller — both fire callbacks from non-loop threads.
    app.state.event_loop = asyncio.get_running_loop()

    if obs is not None:
        try:
            obs.connect()
            logger.info("Connected to OBS on startup")
        except Exception as exc:
            logger.warning("Could not connect to OBS on startup: {}", exc)

    yield

    if watcher is not None:
        watcher.stop()

    if job_manager is not None:
        try:
            job_manager.shutdown()
        except Exception as exc:
            logger.warning("JobManager shutdown raised: {}", exc)

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
    processing_backend: str = "onnx",
    stability_check_interval: float = 2.0,
    stability_check_count: int = 3,
    auphonic_api_key: str = "",
    auphonic_preset: str = "",
) -> FastAPI:
    """Create the recordings dashboard FastAPI application.

    Args:
        recordings_root: Root directory (``to-process/``, ``final/``, ``archive/``).
        obs_host: OBS WebSocket host.
        obs_port: OBS WebSocket port.
        obs_password: OBS WebSocket password.
        spec_file: Optional CLM course spec XML file for lecture listing.
        raw_suffix: Raw filename suffix (default ``--RAW``).
        processing_backend: ``"onnx"`` (default), ``"external"``, or ``"auphonic"``.
        stability_check_interval: Seconds between file-size polls.
        stability_check_count: Consecutive identical polls = stable.
        auphonic_api_key: API key for the Auphonic backend (required when
            ``processing_backend == "auphonic"``; ignored otherwise).
        auphonic_preset: Optional managed preset name to reference on
            every Auphonic production. Empty means inline algorithms.
    """
    from clm.infrastructure.config import AuphonicConfig, RecordingsConfig
    from clm.recordings.workflow.backends import make_backend
    from clm.recordings.workflow.directories import ensure_root
    from clm.recordings.workflow.event_bus import EventBus
    from clm.recordings.workflow.job_manager import JOB_EVENT_TOPIC, JobManager
    from clm.recordings.workflow.job_store import DEFAULT_JOBS_FILE, JsonFileJobStore
    from clm.recordings.workflow.jobs import ProcessingJob
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

    # SSE event queue — session, watcher, and job events are pushed here
    sse_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=256)

    def _push_sse(event: str) -> None:
        """Push *event* onto the SSE queue, safely from any thread.

        ``asyncio.Queue.put_nowait`` is **not** thread-safe when called
        from outside the loop's own thread. We capture the loop in the
        lifespan handler and marshal the put via ``call_soon_threadsafe``
        when invoked from a worker thread (OBS callback, JobManager
        poller, watcher dispatch). Callers on the loop thread take the
        fast path and put directly.
        """

        def _do_put() -> None:
            try:
                sse_queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

        loop = getattr(app.state, "event_loop", None)
        if loop is None:
            # Before lifespan fired (e.g. during create_app smoke tests
            # or unit tests that haven't started the app). Fall back to
            # the direct put — it's fine because no background threads
            # are running yet.
            _do_put()
            return

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop is loop:
            _do_put()
        else:
            loop.call_soon_threadsafe(_do_put)

    def on_state_change(snapshot: object) -> None:
        """Push state change into the SSE queue (called from OBS thread)."""
        _push_sse("state_changed")

    session = RecordingSession(
        obs,
        recordings_root,
        raw_suffix=raw_suffix,
        on_state_change=on_state_change,
    )

    # Job infrastructure: store, bus, backend, manager
    job_store = JsonFileJobStore(recordings_root / DEFAULT_JOBS_FILE)
    event_bus = EventBus()

    backend_config = RecordingsConfig(
        processing_backend=processing_backend,
        raw_suffix=raw_suffix,
        auphonic=AuphonicConfig(
            api_key=auphonic_api_key,
            preset=auphonic_preset,
        ),
    )
    backend = make_backend(backend_config, root_dir=recordings_root)

    job_manager = JobManager(
        backend=backend,
        root_dir=recordings_root,
        store=job_store,
        bus=event_bus,
        raw_suffix=raw_suffix,
    )

    def _on_job_event(topic: str, payload: object) -> None:
        """Forward job lifecycle events onto the SSE queue.

        Runs on the publisher's thread (the JobManager poller, or a
        watcher dispatch thread, or the request thread for
        synchronous backends). Uses the same ``put_nowait`` pattern as
        the OBS callback above.
        """
        if isinstance(payload, ProcessingJob):
            _push_sse(f"job:{payload.id}")
        else:
            _push_sse("job")

    event_bus.subscribe(_on_job_event, topic=JOB_EVENT_TOPIC)

    # File watcher
    watcher = RecordingsWatcher(
        recordings_root,
        job_manager,
        backend,
        stability_interval=stability_check_interval,
        stability_checks=stability_check_count,
        on_submitted=lambda job: _push_sse(f"submitted:{job.id}"),
        on_error=lambda path, err: _push_sse("watcher_error"),
    )

    # Build Course from spec file (if provided) for the lectures page
    course = _build_course(spec_file) if spec_file is not None else None

    # Store in app state for route handlers
    app.state.recordings_root = recordings_root
    app.state.raw_suffix = raw_suffix
    app.state.obs = obs
    app.state.session = session
    app.state.watcher = watcher
    app.state.sse_queue = sse_queue
    app.state.spec_file = spec_file
    app.state.course = course
    app.state.job_store = job_store
    app.state.event_bus = event_bus
    app.state.job_manager = job_manager
    app.state.backend = backend

    # Templates and static files
    templates_dir = Path(__file__).parent / "templates"
    static_dir = Path(__file__).parent / "static"

    app.state.templates = Jinja2Templates(directory=str(templates_dir))

    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Include routes
    app.include_router(router)

    return app
