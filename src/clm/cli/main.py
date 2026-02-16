"""Command-line interface for CLM.

This module provides the main CLI entry point. Commands are organized
into separate modules under clm.cli.commands for maintainability.
"""

import logging
from pathlib import Path

import click

from clm.__version__ import __version__

# Basic logging setup (will be reconfigured by commands as needed)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


@click.group()
@click.version_option(version=__version__, prog_name="clm")
@click.option(
    "--cache-db-path",
    type=click.Path(),
    default="clm_cache.db",
    help="Path to the cache database (stores processed file results)",
)
@click.option(
    "--jobs-db-path",
    type=click.Path(),
    default="clm_jobs.db",
    help="Path to the job queue database (stores jobs, workers, events)",
)
@click.pass_context
def cli(ctx, cache_db_path, jobs_db_path):
    """CLM - Course content processing system.

    Build and manage educational course materials with support for
    Jupyter notebooks, PlantUML diagrams, and Draw.io diagrams.
    """
    ctx.ensure_object(dict)
    ctx.obj["CACHE_DB_PATH"] = Path(cache_db_path)
    ctx.obj["JOBS_DB_PATH"] = Path(jobs_db_path)


@cli.command()
@click.pass_context
def help(ctx):
    """Show this help message."""
    click.echo(ctx.parent.get_help())


# Import and register commands from submodules
# These imports must come after cli is defined, hence noqa: E402
# Re-export commonly used functions for backwards compatibility with tests
from clm.cli.commands.build import (  # noqa: E402
    build,
    list_targets,
)
from clm.cli.commands.config import config  # noqa: E402
from clm.cli.commands.database import db, delete_database  # noqa: E402
from clm.cli.commands.docker import docker_group  # noqa: E402
from clm.cli.commands.git_ops import git_group  # noqa: E402
from clm.cli.commands.jobs import jobs_group  # noqa: E402
from clm.cli.commands.monitoring import monitor, serve  # noqa: E402
from clm.cli.commands.outline import outline  # noqa: E402
from clm.cli.commands.status import status  # noqa: E402
from clm.cli.commands.workers import workers_group  # noqa: E402
from clm.cli.commands.zip_ops import zip_group  # noqa: E402

# Register individual commands
cli.add_command(build)
cli.add_command(list_targets, name="targets")
cli.add_command(delete_database)
cli.add_command(status)
cli.add_command(monitor)
cli.add_command(outline)
cli.add_command(serve)

# Register command groups
cli.add_command(config)
cli.add_command(db)
cli.add_command(docker_group)
cli.add_command(jobs_group)
cli.add_command(git_group)
cli.add_command(workers_group)
cli.add_command(zip_group)


# Re-export commonly used functions for backwards compatibility with tests
# These are kept separate to avoid ruff combining them with command imports
from clm.cli.commands.build import BuildConfig as BuildConfig  # noqa: E402
from clm.cli.commands.build import (  # noqa: E402
    _report_duplicate_file_warnings as _report_duplicate_file_warnings,
)
from clm.cli.commands.build import (  # noqa: E402
    _report_image_collisions as _report_image_collisions,
)
from clm.cli.commands.build import (  # noqa: E402
    _report_loading_issues as _report_loading_issues,
)
from clm.cli.commands.build import (  # noqa: E402
    initialize_paths_and_course as initialize_paths_and_course,
)
from clm.cli.commands.shared import (  # noqa: E402, F401
    is_ci_environment as _is_ci_environment,
)

if __name__ == "__main__":
    cli()
