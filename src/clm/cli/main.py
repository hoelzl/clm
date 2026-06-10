"""Command-line interface for CLM.

This module provides the main CLI entry point. Commands are organized
into separate modules under clm.cli.commands for maintainability.
"""

import warnings

# Suppress version-check warnings from the requests library.
# requests 2.32.x has overly strict compatibility checks that reject
# newer (but fully compatible) versions of urllib3 and chardet.
warnings.filterwarnings(
    "ignore",
    message=r"urllib3.*doesn't match a supported version",
    module="requests",
)

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


# Import commands and groups from submodules. Each group registers its
# own subcommands where it is defined — `clm <group> <cmd>` lives in
# commands/<group>/<cmd>.py (package groups) or commands/<group>.py
# (single-file groups); flat `clm <cmd>` lives in commands/<cmd>.py.
# These imports must come after cli is defined, hence noqa: E402.
from clm.cli.commands.build import build  # noqa: E402
from clm.cli.commands.calendar import calendar_group  # noqa: E402
from clm.cli.commands.cassette import cassette_group  # noqa: E402
from clm.cli.commands.completion import completion_cmd  # noqa: E402
from clm.cli.commands.config import config  # noqa: E402
from clm.cli.commands.course import course_group  # noqa: E402
from clm.cli.commands.db import db  # noqa: E402
from clm.cli.commands.docker import docker_group  # noqa: E402
from clm.cli.commands.export import export_group  # noqa: E402
from clm.cli.commands.git import git_group  # noqa: E402
from clm.cli.commands.info import info  # noqa: E402
from clm.cli.commands.jobs import jobs_group  # noqa: E402
from clm.cli.commands.jupyterlite import jupyterlite_group  # noqa: E402
from clm.cli.commands.monitor import monitor  # noqa: E402
from clm.cli.commands.release import release_group  # noqa: E402
from clm.cli.commands.run import run_cmd  # noqa: E402
from clm.cli.commands.serve import serve  # noqa: E402
from clm.cli.commands.slides import slides_group  # noqa: E402
from clm.cli.commands.status import status  # noqa: E402
from clm.cli.commands.validate import validate_cmd  # noqa: E402
from clm.cli.commands.workers import workers_group  # noqa: E402
from clm.cli.commands.zip import zip_group  # noqa: E402

# Optional commands gated behind extras
try:
    from clm.cli.commands.voiceover import voiceover_group  # noqa: E402
except ImportError:
    voiceover_group = None  # type: ignore[assignment]

try:
    from clm.cli.commands.recordings import recordings_group  # noqa: E402
except ImportError:
    recordings_group = None  # type: ignore[assignment]

try:
    from clm.cli.commands.mcp import mcp_cmd  # noqa: E402
except ImportError:
    mcp_cmd = None  # type: ignore[assignment]

# ---------------------------------------------------------------------
# Top-level commands that stay flat: the everyday verbs.
# ---------------------------------------------------------------------
cli.add_command(build)
cli.add_command(validate_cmd)
cli.add_command(status)
cli.add_command(monitor)
cli.add_command(info)
cli.add_command(run_cmd, name="run")
cli.add_command(serve)
cli.add_command(completion_cmd)

# Register PowerShell shell completion (Bash/Zsh/Fish are native to Click).
# This makes the `_CLM_COMPLETE=powershell_complete` protocol work once a
# user has installed the script emitted by `clm completion powershell`.
from clm.cli.completion import register_powershell_completion  # noqa: E402

register_powershell_completion()

# ---------------------------------------------------------------------
# Domain groups (issue #310). Each group arrives fully populated — its
# subcommands are registered where the group is defined.
# ---------------------------------------------------------------------
cli.add_command(slides_group)
cli.add_command(course_group)
cli.add_command(export_group)
cli.add_command(calendar_group, name="calendar")

# ---------------------------------------------------------------------
# Infrastructure groups.
# ---------------------------------------------------------------------
cli.add_command(cassette_group)
cli.add_command(config)
cli.add_command(db)
cli.add_command(docker_group)
cli.add_command(jobs_group)
cli.add_command(git_group)
cli.add_command(release_group)
cli.add_command(workers_group)
cli.add_command(zip_group)
cli.add_command(jupyterlite_group)

# Optional commands (gated behind extras)
if voiceover_group is not None:
    cli.add_command(voiceover_group)
if recordings_group is not None:
    cli.add_command(recordings_group)
if mcp_cmd is not None:
    cli.add_command(mcp_cmd)


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
