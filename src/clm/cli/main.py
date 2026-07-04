"""Command-line interface for CLM.

This module provides the main CLI entry point. Commands are organized
into separate modules under clm.cli.commands — ``clm <group> <cmd>``
lives in commands/<group>/<cmd>.py (package groups) or
commands/<group>.py (single-file groups); flat ``clm <cmd>`` lives in
commands/<cmd>.py. Command modules are imported lazily via
:class:`clm.cli._lazy_group.LazyGroup` — invoking ``clm <command>`` only
imports that command's module (for a group: the group's package, which
registers its own subcommands). Keep it that way: a module-level import
of a command module here reintroduces its whole dependency chain into
every CLI start.
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
from clm.cli._lazy_group import LazyGroup

# Basic logging setup (will be reconfigured by commands as needed)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

_COMMANDS = "clm.cli.commands"


@click.group(
    cls=LazyGroup,
    lazy_subcommands={
        # -------------------------------------------------------------
        # Top-level commands that stay flat: the everyday verbs.
        # -------------------------------------------------------------
        "build": f"{_COMMANDS}.build:build",
        "kernel-triage": f"{_COMMANDS}.kernel_triage:kernel_triage_cmd",
        "validate": f"{_COMMANDS}.validate:validate_cmd",
        "status": f"{_COMMANDS}.status:status",
        "monitor": f"{_COMMANDS}.monitor:monitor",
        "info": f"{_COMMANDS}.info:info",
        "run": f"{_COMMANDS}.run:run_cmd",
        "serve": f"{_COMMANDS}.serve:serve",
        "completion": f"{_COMMANDS}.completion:completion_cmd",
        # -------------------------------------------------------------
        # Domain groups (issue #310). Each group arrives fully
        # populated — its subcommands are registered where the group is
        # defined, so loading the group imports its whole package.
        # -------------------------------------------------------------
        "slides": f"{_COMMANDS}.slides:slides_group",
        "course": f"{_COMMANDS}.course:course_group",
        "export": f"{_COMMANDS}.export:export_group",
        "calendar": f"{_COMMANDS}.calendar:calendar_group",
        "query": f"{_COMMANDS}.query:query_group",
        # -------------------------------------------------------------
        # Infrastructure groups.
        # -------------------------------------------------------------
        "cache": f"{_COMMANDS}.cache:cache_group",
        "cassette": f"{_COMMANDS}.cassette:cassette_group",
        "config": f"{_COMMANDS}.config:config",
        "db": f"{_COMMANDS}.db:db",
        "docker": f"{_COMMANDS}.docker:docker_group",
        "provision": f"{_COMMANDS}.provision:provision_group",
        "jobs": f"{_COMMANDS}.jobs:jobs_group",
        "git": f"{_COMMANDS}.git:git_group",
        "release": f"{_COMMANDS}.release:release_group",
        "workers": f"{_COMMANDS}.workers:workers_group",
        "zip": f"{_COMMANDS}.zip:zip_group",
        "jupyterlite": f"{_COMMANDS}.jupyterlite:jupyterlite_group",
        # -------------------------------------------------------------
        # Optional commands gated behind extras.
        # -------------------------------------------------------------
        "voiceover": f"{_COMMANDS}.voiceover:voiceover_group",
        "harvest": f"{_COMMANDS}.harvest:harvest_group",
        "recordings": f"{_COMMANDS}.recordings:recordings_group",
        "mcp": f"{_COMMANDS}.mcp:mcp_cmd",
    },
    optional_subcommands=("voiceover", "harvest", "recordings", "mcp"),
)
@click.version_option(version=__version__, prog_name="clm")
@click.option(
    "--cache-db-path",
    type=click.Path(),
    default="clm_cache.db",
    envvar="CLM_CACHE_DB_PATH",
    help=("Path to the cache database (stores processed file results). Env: CLM_CACHE_DB_PATH."),
)
@click.option(
    "--jobs-db-path",
    type=click.Path(),
    default="clm_jobs.db",
    envvar="CLM_JOBS_DB_PATH",
    help=(
        "Path to the job queue database (stores jobs, workers, events). "
        "Ephemeral — safe to place on a RAM disk (e.g. CLM_JOBS_DB_PATH=Z:\\clm_jobs.db) "
        "in Direct worker mode. Env: CLM_JOBS_DB_PATH."
    ),
)
@click.option(
    "--telemetry-db-path",
    type=click.Path(),
    default=None,
    envvar="CLM_TELEMETRY_DB_PATH",
    help=(
        "Path to the execution-telemetry database (per-deck kernel "
        "crash/flake history; issue #330). Default: clm_telemetry.db "
        "next to the cache database. Env: CLM_TELEMETRY_DB_PATH."
    ),
)
@click.pass_context
def cli(ctx, cache_db_path, jobs_db_path, telemetry_db_path):
    """CLM - Course content processing system.

    Build and manage educational course materials with support for
    Jupyter notebooks, PlantUML diagrams, and Draw.io diagrams.
    """
    from click.core import ParameterSource

    from clm.infrastructure.database.execution_telemetry import default_telemetry_db_path
    from clm.infrastructure.utils.path_utils import find_project_root

    def _anchor_default(name: str, value: str) -> Path:
        """Anchor a DB-path *default* to the discovered project root (issue #477).

        A build/status run from a topic subdirectory must open the SAME database
        as one run from the repo root — otherwise the relative default
        ``clm_cache.db`` / ``clm_jobs.db`` resolves under the subdir and silently
        diverges. Only the untouched default is re-anchored; an explicitly
        supplied path (absolute or relative) is respected verbatim so a caller who
        passes ``--cache-db-path foo.db`` still gets a cwd-relative file.
        """
        path = Path(value)
        if ctx.get_parameter_source(name) == ParameterSource.DEFAULT and not path.is_absolute():
            return find_project_root() / path
        return path

    cache_db = _anchor_default("cache_db_path", cache_db_path)
    jobs_db = _anchor_default("jobs_db_path", jobs_db_path)

    ctx.ensure_object(dict)
    ctx.obj["CACHE_DB_PATH"] = cache_db
    ctx.obj["JOBS_DB_PATH"] = jobs_db
    ctx.obj["TELEMETRY_DB_PATH"] = (
        Path(telemetry_db_path)
        if telemetry_db_path is not None
        else default_telemetry_db_path(cache_db)
    )


@cli.command()
@click.pass_context
def help(ctx):
    """Show this help message."""
    click.echo(ctx.parent.get_help())


# Register PowerShell shell completion (Bash/Zsh/Fish are native to Click).
# This makes the `_CLM_COMPLETE=powershell_complete` protocol work once a
# user has installed the script emitted by `clm completion powershell`.
from clm.cli.completion import register_powershell_completion  # noqa: E402

register_powershell_completion()


# ---------------------------------------------------------------------
# Backwards-compatible module attributes (PEP 562). ``clm.cli.main`` used
# to import every command module, so tests and downstream code could do
# ``from clm.cli.main import BuildConfig`` or ``from clm.cli.main import
# slides_group``. Resolve those names lazily instead of eagerly importing
# the world. Optional-extra attributes resolve to ``None`` when their
# extra is not installed, matching the old try/except-ImportError
# assignments.
# ---------------------------------------------------------------------
_COMPAT_EXPORTS: dict[str, tuple[str, str]] = {
    "BuildConfig": (f"{_COMMANDS}.build", "BuildConfig"),
    "initialize_paths_and_course": (f"{_COMMANDS}.build", "initialize_paths_and_course"),
    "_report_duplicate_file_warnings": (f"{_COMMANDS}.build", "_report_duplicate_file_warnings"),
    "_report_image_collisions": (f"{_COMMANDS}.build", "_report_image_collisions"),
    "_report_loading_issues": (f"{_COMMANDS}.build", "_report_loading_issues"),
    "_is_ci_environment": (f"{_COMMANDS}.shared", "is_ci_environment"),
    "build": (f"{_COMMANDS}.build", "build"),
    "validate_cmd": (f"{_COMMANDS}.validate", "validate_cmd"),
    "status": (f"{_COMMANDS}.status", "status"),
    "monitor": (f"{_COMMANDS}.monitor", "monitor"),
    "info": (f"{_COMMANDS}.info", "info"),
    "run_cmd": (f"{_COMMANDS}.run", "run_cmd"),
    "serve": (f"{_COMMANDS}.serve", "serve"),
    "completion_cmd": (f"{_COMMANDS}.completion", "completion_cmd"),
    "slides_group": (f"{_COMMANDS}.slides", "slides_group"),
    "course_group": (f"{_COMMANDS}.course", "course_group"),
    "export_group": (f"{_COMMANDS}.export", "export_group"),
    "calendar_group": (f"{_COMMANDS}.calendar", "calendar_group"),
    "query_group": (f"{_COMMANDS}.query", "query_group"),
    "cassette_group": (f"{_COMMANDS}.cassette", "cassette_group"),
    "config": (f"{_COMMANDS}.config", "config"),
    "db": (f"{_COMMANDS}.db", "db"),
    "docker_group": (f"{_COMMANDS}.docker", "docker_group"),
    "jobs_group": (f"{_COMMANDS}.jobs", "jobs_group"),
    "git_group": (f"{_COMMANDS}.git", "git_group"),
    "release_group": (f"{_COMMANDS}.release", "release_group"),
    "workers_group": (f"{_COMMANDS}.workers", "workers_group"),
    "zip_group": (f"{_COMMANDS}.zip", "zip_group"),
    "jupyterlite_group": (f"{_COMMANDS}.jupyterlite", "jupyterlite_group"),
}

_OPTIONAL_COMPAT_EXPORTS: dict[str, tuple[str, str]] = {
    "voiceover_group": (f"{_COMMANDS}.voiceover", "voiceover_group"),
    "harvest_group": (f"{_COMMANDS}.harvest", "harvest_group"),
    "recordings_group": (f"{_COMMANDS}.recordings", "recordings_group"),
    "mcp_cmd": (f"{_COMMANDS}.mcp", "mcp_cmd"),
}


def __getattr__(name: str):
    import importlib

    if name in _OPTIONAL_COMPAT_EXPORTS:
        module_name, attr = _OPTIONAL_COMPAT_EXPORTS[name]
        try:
            value = getattr(importlib.import_module(module_name), attr)
        except ImportError:
            value = None
        globals()[name] = value
        return value
    try:
        module_name, attr = _COMPAT_EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(importlib.import_module(module_name), attr)
    globals()[name] = value
    return value


if __name__ == "__main__":
    cli()
