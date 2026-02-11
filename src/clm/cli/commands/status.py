"""Status command.

This module provides the status command for displaying CLX system status.
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
    "--workers",
    "workers_only",
    is_flag=True,
    help="Show only worker information",
)
@click.option(
    "--jobs",
    "jobs_only",
    is_flag=True,
    help="Show only job queue information",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json", "compact"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.option(
    "--no-color",
    is_flag=True,
    help="Disable colored output",
)
def status(jobs_db_path, workers_only, jobs_only, output_format, no_color):
    """Show CLX system status.

    Displays worker availability, job queue status, and system health.

    Examples:

        clx status                      # Show full status
        clx status --workers            # Show only workers
        clx status --format=json        # JSON output
        clx status --jobs-db-path=/data/clx_jobs.db  # Custom database
    """
    from clm.cli.status.collector import StatusCollector
    from clm.cli.status.formatter import StatusFormatter
    from clm.cli.status.formatters import (
        CompactFormatter,
        JsonFormatter,
        TableFormatter,
    )

    # Create collector and collect status
    with StatusCollector(db_path=jobs_db_path) as collector:
        try:
            status_info = collector.collect()
        except Exception as e:
            click.echo(f"Error collecting status: {e}", err=True)
            logger.error(f"Error collecting status: {e}", exc_info=True)
            return 2

    # Create formatter
    formatter: StatusFormatter
    if output_format == "json":
        formatter = JsonFormatter(pretty=True)
    elif output_format == "compact":
        formatter = CompactFormatter()
    else:  # table
        formatter = TableFormatter(use_color=not no_color)

    # Format and display
    output = formatter.format(status_info, workers_only=workers_only, jobs_only=jobs_only)
    click.echo(output)

    # Exit with appropriate code
    exit_code = formatter.get_exit_code(status_info)
    raise SystemExit(exit_code)
