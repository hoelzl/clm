"""Monitoring commands.

This module provides the monitor and serve commands for real-time monitoring.
"""

import logging
from pathlib import Path

import click

logger = logging.getLogger(__name__)


@click.command()
@click.option(
    "--jobs-db-path",
    type=click.Path(exists=False, path_type=Path),
    help="Path to the job queue database (auto-detected if not specified)",
)
@click.option(
    "--refresh",
    type=click.IntRange(1, 10),
    default=2,
    help="Refresh interval in seconds (1-10, default: 2)",
)
@click.option(
    "--log-file",
    type=click.Path(path_type=Path),
    help="Log errors to file",
)
def monitor(jobs_db_path, refresh, log_file):
    """Launch real-time monitoring TUI.

    Displays live worker status, job queue, and activity in an
    interactive terminal interface.

    Examples:

        clx monitor                         # Use default settings
        clx monitor --refresh=5             # Update every 5 seconds
        clx monitor --jobs-db-path=/data/clm_jobs.db  # Custom database
    """
    try:
        from clm.cli.monitor.app import CLMMonitorApp
    except ImportError as e:
        click.echo(
            "Error: TUI dependencies not installed. Install with: pip install clx[tui]",
            err=True,
        )
        logger.error(f"Failed to import TUI dependencies: {e}", exc_info=True)
        raise SystemExit(1) from e

    # Set up logging if requested
    if log_file:
        logging.basicConfig(
            filename=str(log_file),
            level=logging.ERROR,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )

    # Auto-detect database path if not specified
    if not jobs_db_path:
        from clm.cli.status.collector import StatusCollector

        collector = StatusCollector()
        jobs_db_path = collector.db_path

    if not jobs_db_path.exists():
        click.echo(f"Error: Job queue database not found: {jobs_db_path}", err=True)
        click.echo("Run 'clx build course.yaml' to initialize the system.", err=True)
        raise SystemExit(2)

    # Launch TUI app
    app = CLMMonitorApp(
        db_path=jobs_db_path,
        refresh_interval=refresh,
    )

    try:
        app.run()
    except Exception as e:
        click.echo(f"Error running monitor: {e}", err=True)
        if log_file:
            click.echo(f"See {log_file} for details", err=True)
        logger.error(f"Monitor error: {e}", exc_info=True)
        raise SystemExit(1) from e


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

    Examples:

        clx serve                           # Start on localhost:8000
        clx serve --host=0.0.0.0 --port=8080  # Bind to all interfaces
        clx serve --jobs-db-path=/data/clm_jobs.db  # Custom database
    """
    try:
        import uvicorn

        from clm.web.app import create_app
    except ImportError as e:
        click.echo(
            "Error: Web dependencies not installed. Install with: pip install clx[web]",
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
        click.echo("Run 'clx build course.yaml' to initialize the system.", err=True)

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
