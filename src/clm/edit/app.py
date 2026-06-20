"""FastAPI application factory for the mobile deck editor.

A self-contained HTMX app — separate from the ``clm serve`` job-monitor
dashboard. It browses the ``slides/`` tree of a data directory and
supports full cell editing (read / edit / add / delete / reorder) of
percent-format ``.py`` (and ``.cpp``/``.cs``/``.java``/``.ts``) deck
files via :class:`clm.edit.deck_file.DeckFile`.

Launch with ``clm edit``.
"""

from __future__ import annotations

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
    """Application lifespan: log startup, nothing to clean up on shutdown."""
    data_dir: Path = app.state.data_dir
    logger.info("Starting CLM deck editor…")
    logger.info("Data directory: {}", data_dir)
    logger.info("Listening on: http://{}:{}", app.state.host, app.state.port)
    if app.state.host in ("0.0.0.0", "::"):
        for url in _lan_urls(app.state.port):
            logger.info("Open on your phone: {}", url)
    yield
    logger.info("Shutting down CLM deck editor…")


def create_app(
    data_dir: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
) -> FastAPI:
    """Create and configure the deck-editor FastAPI application.

    Args:
        data_dir: Root data directory (contains ``slides/``).
        host: Bind host (``0.0.0.0`` to expose on the LAN for a phone).
        port: Bind port.
    """
    app = FastAPI(
        title="CLM Deck Editor",
        version=__version__,
        lifespan=lifespan,
    )

    app.state.data_dir = data_dir
    app.state.slides_dir = data_dir / "slides"
    app.state.host = host
    app.state.port = port

    templates_dir = Path(__file__).parent / "templates"
    static_dir = Path(__file__).parent / "static"

    app.state.templates = Jinja2Templates(directory=str(templates_dir))

    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(router)
    return app


def _lan_urls(port: int) -> list[str]:
    """Best-effort list of ``http://<lan-ip>:<port>`` URLs for the host machine.

    Used only to print a clickable hint when binding to ``0.0.0.0`` so the
    user knows what to open on their phone. Never raises.
    """
    urls: list[str] = []
    try:
        import socket

        hostname = socket.gethostname()
        infos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
        seen: set[str] = set()
        for info in infos:
            sockaddr = info[4]
            ip = str(sockaddr[0])
            if ip in seen or ip.startswith("127."):
                continue
            seen.add(ip)
            # IPv6 addresses need brackets in URLs.
            host = f"[{ip}]" if ":" in ip else ip
            urls.append(f"http://{host}:{port}")
    except OSError:
        pass
    return urls
