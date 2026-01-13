"""FastAPI application for CLX web dashboard."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from clx.web.api.routes import router as api_router
from clx.web.api.websocket import websocket_endpoint
from clx.web.services.monitor_service import MonitorService

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """Application lifespan context manager."""
    # Startup
    logger.info("Starting CLX Dashboard Server...")
    logger.info(f"Database: {app.state.db_path}")
    logger.info(f"Listening on: http://{app.state.host}:{app.state.port}")

    # Start background task for WebSocket updates
    # import asyncio
    # asyncio.create_task(ws_manager.send_periodic_updates(app.state.monitor_service))

    yield

    # Shutdown
    logger.info("Shutting down CLX Dashboard Server...")


def create_app(
    db_path: Path,
    host: str = "127.0.0.1",
    port: int = 8000,
    cors_origins: list[str] | None = None,
) -> FastAPI:
    """Create and configure FastAPI application.

    Args:
        db_path: Path to SQLite database
        host: Host to bind to
        port: Port to bind to
        cors_origins: CORS allowed origins

    Returns:
        Configured FastAPI application
    """
    app = FastAPI(
        title="CLX Dashboard API",
        description="Real-time monitoring API for CLX system",
        version="0.6.1",
        lifespan=lifespan,
    )

    # Store configuration in app state
    app.state.db_path = db_path
    app.state.host = host
    app.state.port = port

    # Initialize monitor service
    app.state.monitor_service = MonitorService(db_path=db_path)

    # Configure CORS
    if cors_origins is None:
        cors_origins = ["*"]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include API router
    app.include_router(api_router)

    # WebSocket endpoint
    @app.websocket("/ws")
    async def websocket_route(websocket):
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
                    <title>CLX Dashboard API</title>
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
                    <h1>CLX Dashboard API v0.3.0</h1>
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
