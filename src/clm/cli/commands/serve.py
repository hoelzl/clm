"""``clm serve`` — web dashboard server."""

import logging
from pathlib import Path

import click

logger = logging.getLogger(__name__)


@click.command()
@click.option(
    "--host",
    default="127.0.0.1",
    help="Host to bind to (default: 127.0.0.1, use 0.0.0.0 for all interfaces)",
)
@click.option(
    "--port",
    type=int,
    default=8000,
    help="Port to bind to (default: 8000)",
)
@click.option(
    "--jobs-db-path",
    type=click.Path(exists=False, path_type=Path),
    help="Path to the job queue database (auto-detected if not specified)",
)
@click.option(
    "--no-browser",
    is_flag=True,
    help="Do not auto-open browser",
)
@click.option(
    "--reload",
    is_flag=True,
    help="Enable auto-reload for development",
)
@click.option(
    "--cors-origin",
    multiple=True,
    help="Origin allowed to read responses cross-origin (repeatable). Default: "
    "none — the dashboard serves its own frontend and needs no CORS. Naming an "
    "origin here also lets it drive the dashboard, as --allowed-origin would.",
)
@click.option(
    "--allowed-host",
    multiple=True,
    help="Extra Host header value to accept (repeatable). Needed when you reach "
    "the dashboard under a name other than localhost — e.g. a Tailscale "
    "hostname. Pass '*' to disable the check entirely.",
)
@click.option(
    "--allowed-origin",
    multiple=True,
    help="Extra origin allowed to drive the dashboard and open /ws (repeatable), "
    "e.g. https://host.tailnet.ts.net. Only needed behind a reverse proxy that "
    "rewrites the Host header.",
)
@click.option(
    "--spec",
    "spec_path",
    type=click.Path(exists=True, path_type=Path),
    help="Enable the Mobile Deck Studio scoped to this course spec (one course "
    "per server instance).",
)
@click.option(
    "--rotate-token",
    is_flag=True,
    help="Rotate the persistent Studio pairing token (invalidates old QR codes).",
)
def serve(
    host,
    port,
    jobs_db_path,
    no_browser,
    reload,
    cors_origin,
    allowed_host,
    allowed_origin,
    spec_path,
    rotate_token,
):
    """Start web dashboard server.

    Launches FastAPI server with REST API and WebSocket support for
    remote monitoring via web browser. With ``--spec`` it additionally serves
    the Mobile Deck Studio — a phone-friendly authoring surface for the given
    course's decks (browse, search, and edit cells with byte-exact write-back
    and optimistic-concurrency guards against concurrent desktop edits).

    The monitoring dashboard has no login: it binds localhost by default, only
    answers to a Host that names this server, and refuses requests driven from
    another origin — which is what keeps a web page open in another tab from
    reading your build state. Use --allowed-host / --allowed-origin when you
    deliberately reach it under a different name. The Studio surface adds a
    bearer token on top, required by both its API and the /ws stream.

    \b
    Examples:
        clm serve                           # Start on localhost:8000
        clm serve --host=0.0.0.0 --port=8080  # Bind to all interfaces
        clm serve --jobs-db-path=/data/clm_jobs.db  # Custom database
        clm serve --spec course.xml         # Also enable Mobile Deck Studio
    """
    try:
        import uvicorn

        from clm.web.app import create_app
    except ImportError as e:
        click.echo(
            "Error: Web dependencies not installed. Install with: pip install clm[web]",
            err=True,
        )
        logger.error(f"Failed to import web dependencies: {e}", exc_info=True)
        raise SystemExit(1) from e

    # Auto-detect database path if not specified
    if not jobs_db_path:
        from clm.cli.status.collector import StatusCollector

        collector = StatusCollector()
        jobs_db_path = collector.db_path

    if not jobs_db_path.exists():
        click.echo(f"Warning: Job queue database not found: {jobs_db_path}", err=True)
        click.echo("The server will start, but data will be unavailable.", err=True)
        click.echo("Run 'clm build course.xml' to initialize the system.", err=True)

    # Studio pairing token (persistent across restarts so the QR stays valid).
    studio_token: str | None = None
    if spec_path is not None:
        from clm.web.studio.auth import get_or_create_token

        studio_token = get_or_create_token(rotate=rotate_token)

    # Create app
    cors_origins: list[str] | None = list(cors_origin) if cors_origin else None
    app = create_app(
        db_path=jobs_db_path,
        host=host,
        port=port,
        cors_origins=cors_origins,
        spec_path=spec_path,
        studio_token=studio_token,
        allowed_hosts=list(allowed_host),
        allowed_origins=list(allowed_origin),
    )

    # A wildcard bind promises remote access the Host allowlist cannot deliver
    # on its own; say so here rather than letting the user discover it as a
    # wall of 400s from another machine.
    from clm.infrastructure.web_security import remote_access_warning

    reach_warning = remote_access_warning(host, allowed_host)
    if reach_warning:
        click.echo(f"Note: {reach_warning}", err=True)

    # Mobile Deck Studio pairing: print the URL + a scannable QR code.
    if spec_path is not None and studio_token is not None:
        from clm.web.studio import qr

        display_host = host if host != "0.0.0.0" else "localhost"
        studio_url = f"http://{display_host}:{port}/studio/?token={studio_token}"
        click.echo("")
        click.echo(f"Mobile Deck Studio: {studio_url}")
        if qr.is_available():
            click.echo("Scan to pair a phone (or open the URL above):")
            qr.print_terminal(studio_url)
        else:
            click.echo("(install the [web] extra's 'segno' for a scannable QR code)")
        click.echo(
            "Note: for phone access over Tailscale, run 'tailscale serve' so the "
            "PWA gets a trusted HTTPS origin."
        )
        click.echo("")

    # Open browser
    if not no_browser:
        import webbrowser

        url = f"http://{host if host != '0.0.0.0' else 'localhost'}:{port}"
        click.echo(f"Opening browser to {url}...")
        webbrowser.open(url)

    # Run server
    click.echo(f"Starting server on {host}:{port}...")
    click.echo(f"API Documentation: http://{host}:{port}/docs")
    click.echo("Press CTRL+C to stop")

    try:
        uvicorn.run(
            app,
            host=host,
            port=port,
            reload=reload,
            log_level="info",
        )
    except Exception as e:
        click.echo(f"Error running server: {e}", err=True)
        logger.error(f"Server error: {e}", exc_info=True)
        raise SystemExit(1) from e
