"""Worker API Server for Docker container communication.

This module provides a lightweight FastAPI server that runs in a background
thread, allowing Docker containers to communicate with the CLX job queue
via REST API instead of direct SQLite access.
"""

import logging
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from clm.infrastructure.api.worker_routes import router as worker_router
from clm.infrastructure.database.job_queue import JobQueue

logger = logging.getLogger(__name__)

# Default port for the Worker API
DEFAULT_PORT = 8765
DEFAULT_HOST = "0.0.0.0"  # Bind to all interfaces for Docker access


class WorkerApiServer:
    """Manages the Worker REST API server lifecycle.

    This server runs in a background thread and provides REST endpoints
    for Docker workers to:
    - Register themselves
    - Claim jobs from the queue
    - Report job completion/failure
    - Send heartbeats
    - Check for job cancellation

    Usage:
        server = WorkerApiServer(db_path)
        server.start()  # Non-blocking, runs in background thread
        # ... do work ...
        server.stop()   # Graceful shutdown
    """

    def __init__(
        self,
        db_path: Path,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
    ):
        """Initialize the Worker API server.

        Args:
            db_path: Path to the SQLite database
            host: Host to bind to (default: 0.0.0.0 for Docker access)
            port: Port to bind to (default: 8765)
        """
        self.db_path = db_path
        self.host = host
        self.port = port

        self._app: FastAPI | None = None
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._started = threading.Event()
        self._shutdown_requested = threading.Event()

    def _create_app(self) -> FastAPI:
        """Create and configure the FastAPI application."""
        from clm import __version__

        app = FastAPI(
            title="CLX Worker API",
            description="REST API for Docker worker communication",
            version=__version__,
        )

        # Store JobQueue in app state for route handlers
        app.state.job_queue = JobQueue(self.db_path)
        app.state.db_path = self.db_path

        # Include worker routes
        app.include_router(worker_router)

        # Health check endpoint
        @app.get("/health")
        async def health():
            return {
                "status": "ok",
                "version": __version__,
                "api_version": "1.0",
                "database": str(self.db_path),
            }

        return app

    def _run_server(self):
        """Run the uvicorn server (called in background thread)."""
        self._app = self._create_app()

        config = uvicorn.Config(
            app=self._app,
            host=self.host,
            port=self.port,
            log_level="warning",  # Reduce uvicorn logging noise
            access_log=False,
        )
        self._server = uvicorn.Server(config)

        # Signal that server is starting
        logger.info(f"Worker API server starting on http://{self.host}:{self.port}")

        # Run the server
        # Note: We use a custom startup to signal when ready
        self._started.set()
        self._server.run()

    def start(self, timeout: float = 5.0) -> bool:
        """Start the API server in a background thread.

        Args:
            timeout: Maximum time to wait for server to start

        Returns:
            True if server started successfully, False otherwise
        """
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Worker API server is already running")
            return True

        self._started.clear()
        self._shutdown_requested.clear()

        self._thread = threading.Thread(
            target=self._run_server,
            name="WorkerApiServer",
            daemon=True,  # Don't block process exit
        )
        self._thread.start()

        # Wait for server to be ready
        if not self._started.wait(timeout=timeout):
            logger.error(f"Worker API server failed to start within {timeout}s")
            return False

        # Give uvicorn a moment to actually bind the port
        time.sleep(0.1)

        logger.info(
            f"Worker API server started on http://{self.host}:{self.port} "
            f"(Docker: http://host.docker.internal:{self.port})"
        )
        return True

    def stop(self, timeout: float = 5.0):
        """Stop the API server gracefully.

        Args:
            timeout: Maximum time to wait for server to stop
        """
        if self._server is None:
            return

        logger.info("Stopping Worker API server...")
        self._shutdown_requested.set()

        # Signal uvicorn to shutdown
        self._server.should_exit = True

        # Wait for thread to finish
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning("Worker API server thread did not stop cleanly")

        self._server = None
        self._thread = None
        self._app = None

        logger.info("Worker API server stopped")

    @property
    def is_running(self) -> bool:
        """Check if the server is currently running."""
        return (
            self._thread is not None
            and self._thread.is_alive()
            and not self._shutdown_requested.is_set()
        )

    @property
    def url(self) -> str:
        """Get the server URL."""
        return f"http://{self.host}:{self.port}"

    @property
    def docker_url(self) -> str:
        """Get the URL for Docker containers to use."""
        return f"http://host.docker.internal:{self.port}"


# Singleton instance for global access
_server_instance: WorkerApiServer | None = None
_server_lock = threading.Lock()


def get_worker_api_server(db_path: Path | None = None) -> WorkerApiServer | None:
    """Get the global Worker API server instance.

    Args:
        db_path: Path to database (required if creating new instance)

    Returns:
        WorkerApiServer instance, or None if not initialized
    """
    global _server_instance

    with _server_lock:
        if _server_instance is None and db_path is not None:
            _server_instance = WorkerApiServer(db_path)
        return _server_instance


def start_worker_api_server(db_path: Path, timeout: float = 5.0) -> WorkerApiServer:
    """Start the global Worker API server.

    This is the main entry point for starting the server. It ensures
    only one server instance exists.

    Args:
        db_path: Path to the SQLite database
        timeout: Maximum time to wait for server to start

    Returns:
        The WorkerApiServer instance

    Raises:
        RuntimeError: If server fails to start
    """
    global _server_instance

    with _server_lock:
        if _server_instance is not None and _server_instance.is_running:
            logger.debug("Worker API server already running")
            return _server_instance

        _server_instance = WorkerApiServer(db_path)
        if not _server_instance.start(timeout=timeout):
            raise RuntimeError("Failed to start Worker API server")

        return _server_instance


def stop_worker_api_server(timeout: float = 5.0):
    """Stop the global Worker API server."""
    global _server_instance

    with _server_lock:
        if _server_instance is not None:
            _server_instance.stop(timeout=timeout)
            _server_instance = None
