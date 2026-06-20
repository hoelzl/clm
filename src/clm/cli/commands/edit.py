"""``clm edit`` — launch the mobile deck editor.

Starts an HTMX web server that lets you edit percent-format ``.py`` deck
files from a browser — primarily a phone on the same LAN. Every save
writes straight to the real file; git is your safety net.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import click

logger = logging.getLogger(__name__)


@click.command("edit")
@click.option(
    "--data-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Course data directory (contains slides/). "
    "Default: CLM_DATA_DIR env var, or current directory.",
)
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Host to bind to (use 0.0.0.0 to expose on the LAN for a phone).",
)
@click.option(
    "--port",
    type=int,
    default=8080,
    show_default=True,
    help="Port to bind to.",
)
@click.option(
    "--no-browser",
    is_flag=True,
    help="Do not auto-open a browser on the desktop.",
)
def edit_cmd(data_dir: Path | None, host: str, port: int, no_browser: bool):
    """Start the mobile deck editor.

    Browse and edit slide deck files (``.py``, ``.cpp``, ``.cs``, ``.java``,
    ``.ts``) from a browser. Designed for working on a course from your
    phone: run this on your desktop, then open the printed URL on your
    phone over the same Wi-Fi (or via a Tailscale tunnel when remote).

    Edits write directly to the source files on disk. Untouched cells are
    preserved byte-for-byte; use git to review and revert.

    \b
    Examples:
        clm edit                              # localhost:8080
        clm edit --host 0.0.0.0               # expose on the LAN
        clm edit --data-dir /path/to/course --port 9000
        CLM_DATA_DIR=/path/to/course clm edit
    """
    try:
        import uvicorn  # noqa: F401

        from clm.edit.app import create_app
    except ImportError as e:
        click.echo(
            "Error: Web dependencies not installed. "
            'Install with: pip install "coding-academy-lecture-manager[edit]"',
            err=True,
        )
        raise SystemExit(1) from e

    resolved_dir = _resolve_data_dir(data_dir)
    slides_dir = resolved_dir / "slides"
    if not slides_dir.is_dir():
        click.echo(
            f"Warning: no slides/ directory found in {resolved_dir}. "
            "The editor will start but list no decks.",
            err=True,
        )

    app = create_app(resolved_dir, host=host, port=port)

    display_host = "localhost" if host == "0.0.0.0" else host
    url = f"http://{display_host}:{port}"
    click.echo(f"Starting deck editor on {host}:{port}…")
    click.echo(f"Open: {url}")
    lan_urls = _lan_urls(port) if host in ("0.0.0.0", "::") else []
    if lan_urls:
        click.echo("Open on your phone (or scan the QR code below):")
        for lan_url in lan_urls:
            click.echo(f"  {lan_url}")
        # Print a scannable QR code for the first LAN URL so the phone can
        # scan it straight off the desktop terminal.
        try:
            from clm.edit.qr import print_terminal

            click.echo()  # blank line before the block
            print_terminal(lan_urls[0])
        except Exception:  # pragma: no cover - defensive; never block startup
            pass
    click.echo("Press CTRL+C to stop")

    if not no_browser:
        import webbrowser

        webbrowser.open(url)

    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    except Exception as e:  # pragma: no cover - defensive
        click.echo(f"Error running server: {e}", err=True)
        logger.error("Server error: %s", e, exc_info=True)
        raise SystemExit(1) from e


def _resolve_data_dir(explicit: Path | None) -> Path:
    """Resolve the data directory: explicit > env var > cwd (mirrors ``clm mcp``)."""
    if explicit is not None:
        return explicit
    env_val = os.environ.get("CLM_DATA_DIR")
    if env_val:
        return Path(env_val)
    return Path.cwd()


def _lan_urls(port: int) -> list[str]:
    """Best-effort LAN URLs for the host machine (for the phone)."""
    try:
        from clm.edit.app import _lan_urls as _urls

        return _urls(port)
    except Exception:  # pragma: no cover - defensive
        return []
