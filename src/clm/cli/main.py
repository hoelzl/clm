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


# Import and register commands from submodules
# These imports must come after cli is defined, hence noqa: E402
# Re-export commonly used functions for backwards compatibility with tests
from clm.cli.commands._aliases import deprecated_alias  # noqa: E402
from clm.cli.commands._groups import (  # noqa: E402
    authoring_group,
    slides_group,
    topic_group,
)
from clm.cli.commands.assign_ids import assign_ids_cmd  # noqa: E402
from clm.cli.commands.authoring_rules import authoring_rules_cmd  # noqa: E402
from clm.cli.commands.build import (  # noqa: E402
    build,
    list_targets,
)
from clm.cli.commands.cassette import cassette_group  # noqa: E402
from clm.cli.commands.completion import completion_cmd  # noqa: E402
from clm.cli.commands.config import config  # noqa: E402
from clm.cli.commands.coverage import coverage_cmd  # noqa: E402
from clm.cli.commands.database import db, delete_database  # noqa: E402
from clm.cli.commands.docker import docker_group  # noqa: E402
from clm.cli.commands.git_ops import git_group  # noqa: E402
from clm.cli.commands.info import info  # noqa: E402
from clm.cli.commands.jobs import jobs_group  # noqa: E402
from clm.cli.commands.language_view import language_view_cmd  # noqa: E402
from clm.cli.commands.monitoring import monitor, serve  # noqa: E402
from clm.cli.commands.normalize_slides import normalize_slides_cmd  # noqa: E402
from clm.cli.commands.outline import outline  # noqa: E402
from clm.cli.commands.release import release_group  # noqa: E402
from clm.cli.commands.resolve_topic import resolve_topic_cmd  # noqa: E402
from clm.cli.commands.search_slides import search_slides_cmd  # noqa: E402
from clm.cli.commands.slides_sync import slides_sync_cmd  # noqa: E402
from clm.cli.commands.split import split_cmd  # noqa: E402
from clm.cli.commands.status import status  # noqa: E402
from clm.cli.commands.suggest_sync import suggest_sync_cmd  # noqa: E402
from clm.cli.commands.summarize import summarize  # noqa: E402
from clm.cli.commands.sync_includes import sync_includes_cmd  # noqa: E402
from clm.cli.commands.unify import unify_cmd  # noqa: E402
from clm.cli.commands.validate import validate_cmd  # noqa: E402
from clm.cli.commands.validate_slides import validate_slides_cmd  # noqa: E402
from clm.cli.commands.validate_spec import validate_spec_cmd  # noqa: E402
from clm.cli.commands.voiceover_tools import (  # noqa: E402
    extract_voiceover_cmd,
    inline_voiceover_cmd,
)
from clm.cli.commands.workers import workers_group  # noqa: E402
from clm.cli.commands.zip_ops import zip_group  # noqa: E402

# Optional commands gated behind extras
try:
    from clm.cli.commands.voiceover import voiceover_group  # noqa: E402
except ImportError:
    voiceover_group = None  # type: ignore[assignment]

try:
    from clm.cli.commands.polish import polish as polish_cmd  # noqa: E402
except ImportError:
    polish_cmd = None  # type: ignore[assignment]

try:
    from clm.cli.commands.recordings import recordings_group  # noqa: E402
except ImportError:
    recordings_group = None  # type: ignore[assignment]

try:
    from clm.cli.commands.mcp_server import mcp_cmd  # noqa: E402
except ImportError:
    mcp_cmd = None  # type: ignore[assignment]

from clm.cli.commands.jupyterlite import jupyterlite_group  # noqa: E402

# ---------------------------------------------------------------------
# Top-level commands that stay flat after the Phase 0 restructure.
# ---------------------------------------------------------------------
cli.add_command(build)
cli.add_command(list_targets, name="targets")
cli.add_command(outline)
cli.add_command(validate_cmd)
cli.add_command(delete_database)
cli.add_command(status)
cli.add_command(monitor)
cli.add_command(info)
cli.add_command(sync_includes_cmd)
cli.add_command(summarize)
cli.add_command(serve)
cli.add_command(completion_cmd)

# Register PowerShell shell completion (Bash/Zsh/Fish are native to Click).
# This makes the `_CLM_COMPLETE=powershell_complete` protocol work once a
# user has installed the script emitted by `clm completion powershell`.
from clm.cli.completion import register_powershell_completion  # noqa: E402

register_powershell_completion()

# ---------------------------------------------------------------------
# Verb-grouped commands (Phase 0): the canonical invocations.
# ---------------------------------------------------------------------
slides_group.add_command(normalize_slides_cmd, name="normalize")
slides_group.add_command(assign_ids_cmd, name="assign-ids")
slides_group.add_command(coverage_cmd, name="coverage")
slides_group.add_command(split_cmd, name="split")
slides_group.add_command(unify_cmd, name="unify")
slides_group.add_command(language_view_cmd, name="language-view")
slides_group.add_command(suggest_sync_cmd, name="suggest-sync")
slides_group.add_command(slides_sync_cmd, name="sync")
slides_group.add_command(search_slides_cmd, name="search")
cli.add_command(slides_group)

topic_group.add_command(resolve_topic_cmd, name="resolve")
cli.add_command(topic_group)

authoring_group.add_command(authoring_rules_cmd, name="rules")
cli.add_command(authoring_group)

# ---------------------------------------------------------------------
# Existing infrastructure groups (unchanged by Phase 0).
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

# Optional commands (gated behind extras)
if voiceover_group is not None:
    # Phase 0: move extract/inline under the voiceover group as
    # canonical subcommands. They keep working as top-level deprecated
    # aliases below.
    voiceover_group.add_command(extract_voiceover_cmd, name="extract")
    voiceover_group.add_command(inline_voiceover_cmd, name="inline")
    cli.add_command(voiceover_group)

if polish_cmd is not None:
    cli.add_command(polish_cmd)
if recordings_group is not None:
    cli.add_command(recordings_group)
if mcp_cmd is not None:
    cli.add_command(mcp_cmd)
cli.add_command(jupyterlite_group)

# ---------------------------------------------------------------------
# Deprecated top-level aliases (Phase 0). Each old name keeps working
# with a deprecation notice naming the new invocation. Removal slated
# for CLM 1.7.
# ---------------------------------------------------------------------
cli.add_command(deprecated_alias(normalize_slides_cmd, new_invocation="slides normalize"))
cli.add_command(deprecated_alias(language_view_cmd, new_invocation="slides language-view"))
cli.add_command(deprecated_alias(suggest_sync_cmd, new_invocation="slides suggest-sync"))
cli.add_command(deprecated_alias(search_slides_cmd, new_invocation="slides search"))
cli.add_command(deprecated_alias(resolve_topic_cmd, new_invocation="topic resolve"))
cli.add_command(deprecated_alias(authoring_rules_cmd, new_invocation="authoring rules"))
cli.add_command(deprecated_alias(validate_slides_cmd, new_invocation="validate"))
cli.add_command(deprecated_alias(validate_spec_cmd, new_invocation="validate"))
if voiceover_group is not None:
    cli.add_command(deprecated_alias(extract_voiceover_cmd, new_invocation="voiceover extract"))
    cli.add_command(deprecated_alias(inline_voiceover_cmd, new_invocation="voiceover inline"))


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
