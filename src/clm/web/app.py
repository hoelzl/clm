"""FastAPI application for CLM web dashboard.

The dashboard API (``/api/*``) has no login: like the recordings dashboard, it
treats "the request arrived on the socket" as "the user asked for this", and
binds loopback so that stays true. What it therefore needs is containment
against the two things a *browser* can do without the user's cooperation —
cross-origin drives and DNS rebinding — which is
:func:`clm.infrastructure.web_security.install_web_security`.

Mobile Deck Studio (``--spec``) sits on top of that with a real access gate: a
bearer token on every ``/api/studio`` route. ``/ws`` is part of that gate, not
outside it — see :mod:`clm.web.api.websocket`.
"""

import logging
from collections.abc import AsyncGenerator, Iterable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from clm.__version__ import __version__
from clm.infrastructure.web_security import install_web_security, normalize_origin
from clm.web.api.routes import router as api_router
from clm.web.api.websocket import websocket_endpoint
from clm.web.services.monitor_service import MonitorService

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """Application lifespan context manager."""
    # Startup
    logger.info("Starting CLM Dashboard Server...")
    logger.info(f"Database: {app.state.db_path}")
    logger.info(f"Listening on: http://{app.state.host}:{app.state.port}")

    # Start background task for WebSocket updates
    # import asyncio
    # asyncio.create_task(ws_manager.send_periodic_updates(app.state.monitor_service))

    # Start the Studio external-change watcher (the two-editor guard) when a
    # course spec was configured (clm serve --spec).
    import asyncio

    watcher_task = None
    studio_service = getattr(app.state, "studio_service", None)
    if studio_service is not None:
        from clm.web.studio.watcher import watch_slides_dir

        watcher_task = asyncio.create_task(watch_slides_dir(studio_service))

    yield

    # Shutdown
    logger.info("Shutting down CLM Dashboard Server...")
    if watcher_task is not None:
        watcher_task.cancel()
        try:
            await watcher_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 - best-effort stop
            pass


def _checked_cors_origins(cors_origins: list[str]) -> list[str]:
    """Return the ``--cors-origin`` values, warning about ones that won't work.

    ``CORSMiddleware`` compares the ``Origin`` header verbatim while the origin
    guard compares a normalized form, so a value the two read differently is
    half-applied in silence:

    - ``localhost:3000`` (no scheme) matches neither — no browser ever sends
      an ``Origin`` without one — and is simply inert.
    - ``https://x.example/`` (trailing slash) normalizes for the guard but
      never matches ``CORSMiddleware``, so the flag widens what may *drive*
      the app without granting what the operator actually asked for.

    Both are operator typos, and both are invisible without this. The values
    are still returned: refusing to start over a mistyped origin would be
    worse than saying so.
    """
    for origin in cors_origins:
        if origin == "*":
            continue
        normalized = normalize_origin(origin)
        if normalized is None:
            logger.warning(
                "CORS origin %r is not a valid origin (expected scheme://host[:port]) "
                "and will match nothing.",
                origin,
            )
        elif normalized != origin:
            logger.warning(
                "CORS origin %r does not match what a browser sends; use %r.",
                origin,
                normalized,
            )
    return [o for o in cors_origins if o != "*"]


def create_app(
    db_path: Path,
    host: str = "127.0.0.1",
    port: int = 8000,
    cors_origins: list[str] | None = None,
    spec_path: Path | None = None,
    studio_token: str | None = None,
    allowed_hosts: Iterable[str] | None = None,
    allowed_origins: Iterable[str] = (),
) -> FastAPI:
    """Create and configure FastAPI application.

    Args:
        db_path: Path to SQLite database
        host: Host to bind to. Folded into the ``Host`` allowlist, so a
            deliberate non-loopback bind keeps working.
        port: Port to bind to
        cors_origins: Origins allowed to read responses cross-origin
            (``--cors-origin``). ``None`` — the default — installs no CORS
            middleware at all, which is what a same-origin app needs. Named
            origins are *also* added to the origin guard's allowlist, since
            "let this origin talk to me" is the only reason to pass the flag.
        spec_path: When given, enable the Mobile Deck Studio view scoped to this
            course spec (one course per server instance).
        studio_token: Bearer token required by the Studio API/WS (ignored when
            ``spec_path`` is None).
        allowed_hosts: Extra ``Host`` values to accept (``--allowed-host``).
            ``["*"]`` disables the host check.
        allowed_origins: Extra origins allowed to drive mutating requests and
            open ``/ws`` (``--allowed-origin``).

    Returns:
        Configured FastAPI application
    """
    app = FastAPI(
        title="CLM Dashboard API",
        description="Real-time monitoring API for CLM system",
        version=__version__,
        lifespan=lifespan,
    )

    # Store configuration in app state
    app.state.db_path = db_path
    app.state.host = host
    app.state.port = port

    # Initialize monitor service
    app.state.monitor_service = MonitorService(db_path=db_path)

    # Enable Mobile Deck Studio when a course spec was provided.
    if spec_path is not None:
        from clm.web.studio.routes import router as studio_router
        from clm.web.studio.service import StudioService

        app.state.studio_service = StudioService(spec_path)
        app.state.studio_token = studio_token
        app.include_router(studio_router)

        studio_static = Path(__file__).parent / "static" / "studio"
        if studio_static.exists():
            # The service worker must be served with `Service-Worker-Allowed: /`
            # so it can register with root scope (intercepting both /studio/ and
            # /api/studio/ for the P4 offline cache). This explicit route is added
            # BEFORE the StaticFiles mount so it wins over the mount's handler.
            sw_file = studio_static / "sw.js"
            if sw_file.exists():

                @app.get("/studio/sw.js", include_in_schema=False)
                async def studio_service_worker() -> FileResponse:
                    return FileResponse(
                        sw_file,
                        media_type="text/javascript",
                        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
                    )

            app.mount(
                "/studio",
                StaticFiles(directory=studio_static, html=True),
                name="studio",
            )

    # Configure CORS. The default is *no* CORS middleware: this app serves its
    # own frontend, and same-origin traffic never needs one. The previous
    # default — `allow_origins=["*"]` together with `allow_credentials=True` —
    # made Starlette echo whichever Origin asked, which is strictly worse than
    # a literal `*` because it makes credentialed cross-origin reads legal.
    guard_origins = list(allowed_origins)
    if cors_origins:
        wildcard = "*" in cors_origins
        if wildcard:
            # A credentialed wildcard is not a thing the CORS spec allows; the
            # only reason it "worked" is Starlette's echo. Honour the wildcard
            # and drop credentials rather than silently reinstating the bug.
            logger.warning(
                "CORS origin '*' cannot be combined with credentials; serving "
                "cross-origin responses without credentials. Name the origins "
                "explicitly with --cors-origin to keep credentials."
            )
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=not wildcard,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        # CORS governs who may *read* a response; the origin guard governs who
        # may *cause* one. Naming an origin for the first without the second
        # leaves a caller with a working preflight and a 403 behind it, so an
        # explicit --cors-origin implies --allowed-origin.
        guard_origins.extend(_checked_cors_origins(cors_origins))

    # Browser containment (D4): a Host allowlist closes DNS rebinding, and an
    # origin check on mutating requests closes CSRF. Installed last so both
    # guards sit outside CORS — a rebound request is refused before any other
    # middleware looks at it. Covers the /ws handshake too, which is why the
    # WebSocket is not exposed by CORS being off.
    effective_hosts = install_web_security(
        app,
        bind_host=host,
        allowed_hosts=allowed_hosts,
        allowed_origins=guard_origins,
    )
    logger.debug("Dashboard accepts Host values: %s", effective_hosts)
    app.state.allowed_hosts = effective_hosts

    # Include API router
    app.include_router(api_router)

    # WebSocket endpoint. The annotation is load-bearing: FastAPI resolves a
    # route's parameters from its signature, and an *unannotated* `websocket`
    # was analysed as a required query parameter — so every handshake was
    # closed with "Field required" and the Studio's disk-change banner never
    # fired. Found while adding the token check below.
    @app.websocket("/ws")
    async def websocket_route(websocket: WebSocket) -> None:
        """WebSocket endpoint."""
        await websocket_endpoint(websocket)

    # Serve static frontend files
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists() and (static_dir / "index.html").exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

        @app.get("/", response_class=HTMLResponse)
        async def serve_frontend():
            """Serve frontend index.html."""
            return FileResponse(static_dir / "index.html")
    else:
        # Serve a simple default page if no frontend is built
        @app.get("/", response_class=HTMLResponse)
        async def serve_default():
            """Serve default page when frontend is not available."""
            return HTMLResponse(
                content="""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>CLM Dashboard API</title>
                    <style>
                        body {
                            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                            max-width: 900px;
                            margin: 50px auto;
                            padding: 20px;
                            line-height: 1.6;
                        }
                        h1 { color: #2563eb; }
                        .endpoint { background: #f3f4f6; padding: 15px; margin: 10px 0; border-radius: 5px; }
                        .endpoint code { background: #1f2937; color: #10b981; padding: 2px 6px; border-radius: 3px; }
                        a { color: #2563eb; text-decoration: none; }
                        a:hover { text-decoration: underline; }
                    </style>
                </head>
                <body>
                    <h1>CLM Dashboard API v0.3.0</h1>
                    <p>Web API server is running successfully!</p>

                    <h2>Available Endpoints</h2>

                    <div class="endpoint">
                        <strong>GET <code>/api/health</code></strong><br>
                        Health check and server info
                    </div>

                    <div class="endpoint">
                        <strong>GET <code>/api/status</code></strong><br>
                        Complete system status (workers, queue, health)
                    </div>

                    <div class="endpoint">
                        <strong>GET <code>/api/workers</code></strong><br>
                        List all registered workers
                    </div>

                    <div class="endpoint">
                        <strong>GET <code>/api/jobs?status=pending&page=1&page_size=50</code></strong><br>
                        List jobs with pagination and filtering
                    </div>

                    <div class="endpoint">
                        <strong>WebSocket <code>/ws</code></strong><br>
                        Real-time updates (subscribe to: status, workers, jobs)
                    </div>

                    <h2>Documentation</h2>
                    <p>
                        <a href="/docs" target="_blank">Swagger UI Documentation</a> |
                        <a href="/redoc" target="_blank">ReDoc Documentation</a>
                    </p>

                    <p style="margin-top: 40px; color: #6b7280; font-size: 14px;">
                        <strong>Note:</strong> React frontend not built. To build the frontend,
                        see the web dashboard documentation.
                    </p>
                </body>
                </html>
                """
            )

    return app
