"""Command-line interface for CLX.

This module provides the main CLI entry point. Commands are organized
into separate modules under clx.cli.commands for maintainability.
"""

import logging
from pathlib import Path

import click

# Basic logging setup (will be reconfigured by commands as needed)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


@click.group()
@click.option(
    "--cache-db-path",
    type=click.Path(),
    default="clx_cache.db",
    help="Path to the cache database (stores processed file results)",
)
@click.option(
    "--jobs-db-path",
    type=click.Path(),
    default="clx_jobs.db",
    help="Path to the job queue database (stores jobs, workers, events)",
)
@click.pass_context
def cli(ctx, cache_db_path, jobs_db_path):
    """CLX - Course content processing system.

    Build and manage educational course materials with support for
    Jupyter notebooks, PlantUML diagrams, and Draw.io diagrams.
    """
    ctx.ensure_object(dict)
    ctx.obj["CACHE_DB_PATH"] = Path(cache_db_path)
    ctx.obj["JOBS_DB_PATH"] = Path(jobs_db_path)


# Import and register commands from submodules
# These imports must come after cli is defined, hence noqa: E402
# Re-export commonly used functions for backwards compatibility with tests
from clx.cli.commands.build import (  # noqa: E402
    build,
    list_targets,
)
from clx.cli.commands.config import config  # noqa: E402
from clx.cli.commands.database import db, delete_database  # noqa: E402
from clx.cli.commands.docker import docker_group  # noqa: E402
from clx.cli.commands.monitoring import monitor, serve  # noqa: E402
from clx.cli.commands.outline import outline  # noqa: E402
from clx.cli.commands.status import status  # noqa: E402
from clx.cli.commands.workers import workers_group  # noqa: E402

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
cli.add_command(workers_group)


# Re-export commonly used functions for backwards compatibility with tests
# These are kept separate to avoid ruff combining them with command imports
from clx.cli.commands.build import BuildConfig as BuildConfig  # noqa: E402
from clx.cli.commands.build import (  # noqa: E402
    _report_duplicate_file_warnings as _report_duplicate_file_warnings,
)
from clx.cli.commands.build import (  # noqa: E402
    _report_image_collisions as _report_image_collisions,
)
from clx.cli.commands.build import (  # noqa: E402
    _report_loading_issues as _report_loading_issues,
)
from clx.cli.commands.build import (  # noqa: E402
    initialize_paths_and_course as initialize_paths_and_course,
)
from clx.cli.commands.shared import (  # noqa: E402, F401
    is_ci_environment as _is_ci_environment,
)

if __name__ == "__main__":
    cli()
