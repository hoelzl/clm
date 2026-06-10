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
    help="CORS allowed origins (can specify multiple times, default: *)",
)
def serve(host, port, jobs_db_path, no_browser, reload, cors_origin):
    """Start web dashboard server.

    Launches FastAPI server with REST API and WebSocket support for
    remote monitoring via web browser.

    \b
    Examples:
        clm serve                           # Start on localhost:8000
        clm serve --host=0.0.0.0 --port=8080  # Bind to all interfaces
        clm serve --jobs-db-path=/data/clm_jobs.db  # Custom database
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

    # Create app
    cors_origins: list[str] | None = list(cors_origin) if cors_origin else None
    app = create_app(
        db_path=jobs_db_path,
        host=host,
        port=port,
        cors_origins=cors_origins,
    )

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
