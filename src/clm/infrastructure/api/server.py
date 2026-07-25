"""Worker API Server for Docker container communication.

This module provides a lightweight FastAPI server that runs in a background
thread, allowing Docker containers to communicate with the CLM job queue
via REST API instead of direct SQLite access.

Two things guard it, and both are on by default:

- **Where it listens** is decided by :mod:`clm.infrastructure.api.binding` —
  loopback plus (on Linux) the Docker bridge gateways, never all interfaces.
  Binding wider is coordinator mode: an explicit opt-in that also requires a
  pinned token.
- **Who may call it** is decided by :mod:`clm.infrastructure.api.auth` — a
  per-build bearer token enforced on every worker route.
"""

import logging
import os
import socket
import sys
import threading
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from clm.infrastructure.api import binding
from clm.infrastructure.api.binding import HOST_ENV_VAR, LOOPBACK_HOST
from clm.infrastructure.api.token import TOKEN_ENV_VAR, generate_token
from clm.infrastructure.api.worker_routes import router as worker_router
from clm.infrastructure.database.job_queue import JobQueue

logger = logging.getLogger(__name__)

#: Default port for the Worker API. Not a contract — containers are told the
#: real port via ``CLM_API_URL`` — so a build whose default port is taken moves
#: to an OS-assigned one. See :mod:`clm.infrastructure.api.binding`.
DEFAULT_PORT = binding.DEFAULT_PORT

#: Default bind address. Containers reach the host through
#: ``host.docker.internal``; on Docker Desktop that forwards to loopback, and
#: on Linux the additional bridge-gateway binds are added at start time.
DEFAULT_HOST = LOOPBACK_HOST


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
        host: str | None = None,
        port: int | None = None,
        cache_db_path: Path | None = None,
        token: str | None = None,
    ):
        """Initialize the Worker API server.

        Args:
            db_path: Path to the SQLite job database (clm_jobs.db)
            host: Address to bind. ``None`` (the default) selects local mode:
                loopback, plus the Docker bridge gateways on Linux. Naming an
                address explicitly — here or via ``CLM_WORKER_API_HOST`` —
                selects coordinator mode and requires a pinned token.
            port: Port to bind. ``None`` (the default) consults
                ``CLM_WORKER_API_PORT`` and then falls back to
                :data:`DEFAULT_PORT`, which is advisory: if it is taken, the
                server moves to an OS-assigned port. Naming a port — here or in
                the environment — pins it, making a collision an error. ``0``
                asks the OS for a free port, which is how the test suite gives
                every test its own server.
            cache_db_path: Path to the executed_notebooks cache database
                (clm_cache.db). If None, the cache endpoints fall back to
                ``db_path`` for backwards compatibility — the executed_notebooks
                table is created on demand by ``ExecutedNotebookCache``.
            token: The bearer token every worker route requires. ``None``
                falls back to ``CLM_API_TOKEN``, and failing that a freshly
                generated per-build secret.

        Raises:
            ValueError: If the requested bind address reaches beyond this
                machine but no token was pinned. Generated tokens exist only
                in this process, so there would be no way to tell another
                machine what to present — refusing is the only honest answer.
                Also if ``CLM_WORKER_API_PORT`` is not a port number.
        """
        self.db_path = db_path
        self.port, self.port_pinned = binding.resolve_port(port)
        self.cache_db_path = cache_db_path

        explicit_host = host if host is not None else (os.environ.get(HOST_ENV_VAR) or None)
        pinned_token = token if token is not None else (os.environ.get(TOKEN_ENV_VAR) or None)

        self._gateways = binding.docker_gateway_hosts()
        self.bind_hosts = binding.resolve_bind_hosts(explicit_host, gateways=self._gateways)
        exposed = binding.classify_hosts(self.bind_hosts, gateways=self._gateways)
        if exposed and pinned_token is None:
            raise ValueError(
                f"Worker API refuses to bind {', '.join(exposed)} without a configured "
                f"token. Binding beyond loopback and the Docker bridge exposes job "
                f"claiming and the executed-notebook cache to other machines, so it is "
                f"only supported in coordinator mode: set {TOKEN_ENV_VAR} to a shared "
                f"secret that participating machines also present. Unset "
                f"{HOST_ENV_VAR} to return to the default local bind."
            )

        self.token = pinned_token or generate_token()
        self.coordinator_mode = bool(exposed)

        #: Primary address, kept for URL building and log lines.
        self.host = self.bind_hosts[0]

        #: Addresses actually bound, filled in by :meth:`_bind`.
        self.bound_hosts: list[str] = []

        self._app: FastAPI | None = None
        self._server: uvicorn.Server | None = None
        self._sockets: list[socket.socket] = []
        self._thread: threading.Thread | None = None
        self._started = threading.Event()
        self._shutdown_requested = threading.Event()

    def _create_app(self) -> FastAPI:
        """Create and configure the FastAPI application."""
        from contextlib import asynccontextmanager

        from clm import __version__

        db_path = self.db_path
        cache_db_path = self.cache_db_path
        token = self.token

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            app.state.job_queue = JobQueue(db_path)
            app.state.db_path = db_path
            app.state.cache_db_path = cache_db_path
            yield
            app.state.job_queue.close()

        app = FastAPI(
            title="CLM Worker API",
            description="REST API for Docker worker communication",
            version=__version__,
            lifespan=lifespan,
        )
        # The token lives on app.state so ``require_api_token`` can reach it
        # from any request, and so tests can build an app without a server.
        # Set outside the lifespan: TestClient-less unit tests never run it.
        app.state.api_token = token

        # Include worker routes
        app.include_router(worker_router)

        # Health check endpoint. Unauthenticated on purpose — a worker that
        # cannot yet authenticate still needs a liveness probe, and this is
        # the one place a misconfigured token can be diagnosed from. It
        # therefore reports nothing an anonymous caller should not see: no
        # database path (that names a host filesystem and, on a share, a
        # server name), and no token.
        @app.get("/health")
        async def health():
            return {
                "status": "ok",
                "version": __version__,
                "api_version": "1.0",
            }

        return app

    def _bind(self) -> list[socket.socket]:
        """Bind every address in :attr:`bind_hosts`, loopback first.

        The first (loopback) bind is mandatory and decides the port: an
        unpinned default port that is already taken is swapped for an
        OS-assigned one, while a pinned port that is taken raises. Either way
        the first successful bind fixes the port, so every address stays on one
        port and :attr:`port` reports the one actually taken.

        Gateway binds are best-effort: a Docker network can disappear between
        discovery and bind, and losing one is a Docker-mode connectivity
        problem, not a reason to abort a build that may not even use Docker.
        """
        primary, *secondary = self.bind_hosts
        first = binding.bind_socket_with_fallback(
            primary, self.port, allow_fallback=not self.port_pinned
        )
        self.port = first.getsockname()[1]
        sockets: list[socket.socket] = [first]
        self.bound_hosts = [primary]

        for host in secondary:
            try:
                sockets.append(binding.bind_socket(host, self.port))
            except OSError as e:
                logger.warning(f"Worker API could not bind {host}:{self.port}: {e}")
                continue
            self.bound_hosts.append(host)

        self._warn_if_containers_cannot_reach_us(sockets)
        return sockets

    def _warn_if_containers_cannot_reach_us(self, sockets: list[socket.socket]) -> None:
        """Say so, loudly, when a Linux host bound loopback and nothing else.

        Docker Desktop forwards ``host.docker.internal`` to the host's
        loopback, so on Windows/macOS a loopback-only bind is complete. On
        Linux it is not: containers arrive via the bridge gateway, and if
        discovery found none — the Docker socket is unreadable, the SDK is
        missing, the daemon is remote — a loopback-only server is invisible to
        them. Every job would then sit ``pending`` with nothing in the host
        log explaining why, which is precisely the shape of failure that costs
        an afternoon.
        """
        if sys.platform != "linux" or self.coordinator_mode:
            return
        if len(sockets) > 1:
            return
        logger.warning(
            f"Worker API bound only {LOOPBACK_HOST}: no Docker bridge gateway was "
            f"discovered. On Linux, containers reach the host through that gateway, not "
            f"through loopback — so Docker workers will not be able to register and their "
            f"jobs will stay pending. Check that the Docker daemon is reachable from this "
            f"process, or set {HOST_ENV_VAR} to an address the containers can route to "
            f"(which also requires {TOKEN_ENV_VAR})."
        )

    def _close_sockets(self) -> None:
        for sock in self._sockets:
            try:
                sock.close()
            except OSError:  # pragma: no cover - closing twice is harmless
                pass
        self._sockets = []

    def _run_server(self):
        """Run the uvicorn server (called in background thread)."""
        assert self._app is not None, "_bind/_create_app run before the thread starts"
        assert self._server is not None

        # Run the server on the sockets the caller already bound, so that by
        # the time start() returns the port is genuinely accepting.
        self._started.set()
        self._server.run(sockets=self._sockets)

    def start(self, timeout: float = 5.0) -> bool:
        """Start the API server in a background thread.

        Args:
            timeout: Maximum time to wait for server to start

        Returns:
            True if server started successfully, False otherwise

        Raises:
            OSError: If the primary (loopback) address cannot be bound — which
                for a *pinned* port includes "the port is already in use". An
                unpinned default port that is taken is swapped for an
                OS-assigned one instead, and :attr:`port` reports which.
        """
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Worker API server is already running")
            return True

        self._started.clear()
        self._shutdown_requested.clear()

        self._sockets = self._bind()
        self.host = self.bind_hosts[0]

        self._app = self._create_app()
        config = uvicorn.Config(
            app=self._app,
            log_level="warning",  # Reduce uvicorn logging noise
            access_log=False,
        )
        self._server = uvicorn.Server(config)

        logger.info(f"Worker API server starting on http://{self.host}:{self.port}")

        self._thread = threading.Thread(
            target=self._run_server,
            name="WorkerApiServer",
            daemon=True,  # Don't block process exit
        )
        self._thread.start()

        # Wait for server to be ready
        if not self._started.wait(timeout=timeout):
            logger.error(f"Worker API server failed to start within {timeout}s")
            self._close_sockets()
            return False

        bound = ", ".join(f"{h}:{self.port}" for h in self.bound_hosts)
        logger.info(
            f"Worker API server started on {bound} "
            f"(Docker: http://host.docker.internal:{self.port}); "
            f"{'coordinator mode, pinned token' if self.coordinator_mode else 'local bind'}, "
            f"bearer token required on every worker route"
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
        # uvicorn closes the sockets it was handed on a clean shutdown; this
        # covers the path where the thread had to be abandoned.
        self._close_sockets()

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
        return f"http://{binding.DOCKER_HOST_ALIAS}:{self.port}"


# Singleton instance for global access
_server_instance: WorkerApiServer | None = None
_server_lock = threading.Lock()


def get_worker_api_server(
    db_path: Path | None = None,
    cache_db_path: Path | None = None,
) -> WorkerApiServer | None:
    """Get the global Worker API server instance.

    Args:
        db_path: Path to job database (required if creating new instance)
        cache_db_path: Path to executed_notebooks cache database (optional)

    Returns:
        WorkerApiServer instance, or None if not initialized
    """
    global _server_instance

    with _server_lock:
        if _server_instance is None and db_path is not None:
            _server_instance = WorkerApiServer(db_path, cache_db_path=cache_db_path)
        return _server_instance


def start_worker_api_server(
    db_path: Path,
    timeout: float = 5.0,
    cache_db_path: Path | None = None,
    port: int | None = None,
) -> WorkerApiServer:
    """Start the global Worker API server.

    This is the main entry point for starting the server. It ensures
    only one server instance exists.

    Args:
        db_path: Path to the SQLite job database
        timeout: Maximum time to wait for server to start
        cache_db_path: Path to the executed_notebooks cache database
            (clm_cache.db). Required for cache endpoints to write to the
            real cache; falls back to ``db_path`` if not provided.
        port: Port to pin, or ``None`` to take the port policy in
            :func:`clm.infrastructure.api.binding.resolve_port` —
            ``CLM_WORKER_API_PORT``, else the advisory default.

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

        _server_instance = WorkerApiServer(db_path, cache_db_path=cache_db_path, port=port)
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


def get_worker_api_endpoint() -> tuple[str, str] | None:
    """Return ``(docker_url, token)`` for the running server, or None.

    :class:`~clm.infrastructure.workers.worker_executor.DockerWorkerExecutor`
    calls this to fill in ``CLM_API_URL`` and ``CLM_API_TOKEN`` for each
    container it starts. Both come from the same call on purpose: the URL
    carries the port the server *actually* bound, which is not necessarily
    :data:`DEFAULT_PORT`, and a container told the wrong port fails exactly
    like a container told the wrong token — jobs sit ``pending`` with nothing
    in the host log. Reading the two from one place is what keeps them from
    drifting apart.
    """
    with _server_lock:
        if _server_instance is None:
            return None
        return _server_instance.docker_url, _server_instance.token
