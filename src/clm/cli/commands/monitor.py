"""``clm monitor`` — real-time monitoring TUI."""

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

    \b
    Examples:
        clm monitor                         # Use default settings
        clm monitor --refresh=5             # Update every 5 seconds
        clm monitor --jobs-db-path=/data/clm_jobs.db  # Custom database
    """
    try:
        from clm.cli.monitor.app import CLMMonitorApp
    except ImportError as e:
        click.echo(
            "Error: TUI dependencies not installed. Install with: pip install clm[tui]",
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
        click.echo("Run 'clm build course.xml' to initialize the system.", err=True)
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
