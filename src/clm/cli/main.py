"""Command-line interface for CLM.

This module provides the main CLI entry point. Commands are organized
into separate modules under clm.cli.commands and are imported lazily via
:class:`clm.cli._lazy_group.LazyGroup` — invoking ``clm <command>`` only
imports that command's module. Keep it that way: a new module-level
import of a command module here reintroduces its whole dependency chain
into every CLI start.
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


def _load_voiceover_group() -> click.Group:
    """Load the voiceover group with extract/inline wired in as subcommands.

    They keep their historical top-level names inside their own module but
    are registered under the group as ``extract`` / ``inline``.
    """
    from clm.cli.commands.voiceover import voiceover_group
    from clm.cli.commands.voiceover_tools import (
        extract_voiceover_cmd,
        inline_voiceover_cmd,
    )

    voiceover_group.add_command(extract_voiceover_cmd, name="extract")
    voiceover_group.add_command(inline_voiceover_cmd, name="inline")
    return voiceover_group


@click.group(
    cls=LazyGroup,
    lazy_subcommands={
        # -------------------------------------------------------------
        # Top-level commands that stay flat after the Phase 0 restructure.
        # -------------------------------------------------------------
        "build": f"{_COMMANDS}.build:build",
        "targets": f"{_COMMANDS}.build:list_targets",
        "validate": f"{_COMMANDS}.validate:validate_cmd",
        "delete-database": f"{_COMMANDS}.database:delete_database",
        "status": f"{_COMMANDS}.status:status",
        "monitor": f"{_COMMANDS}.monitoring:monitor",
        "info": f"{_COMMANDS}.info:info",
        "run": f"{_COMMANDS}.run:run_cmd",
        "sync-includes": f"{_COMMANDS}.sync_includes:sync_includes_cmd",
        "serve": f"{_COMMANDS}.monitoring:serve",
        "completion": f"{_COMMANDS}.completion:completion_cmd",
        # -------------------------------------------------------------
        # Infrastructure groups defined in their own modules.
        # -------------------------------------------------------------
        "calendar": f"{_COMMANDS}.calendar:calendar_group",
        "cassette": f"{_COMMANDS}.cassette:cassette_group",
        "config": f"{_COMMANDS}.config:config",
        "db": f"{_COMMANDS}.database:db",
        "docker": f"{_COMMANDS}.docker:docker_group",
        "jobs": f"{_COMMANDS}.jobs:jobs_group",
        "git": f"{_COMMANDS}.git_ops:git_group",
        "release": f"{_COMMANDS}.release:release_group",
        "workers": f"{_COMMANDS}.workers:workers_group",
        "zip": f"{_COMMANDS}.zip_ops:zip_group",
        "jupyterlite": f"{_COMMANDS}.jupyterlite:jupyterlite_group",
        # -------------------------------------------------------------
        # Optional commands gated behind extras.
        # -------------------------------------------------------------
        "voiceover": _load_voiceover_group,
        "polish": f"{_COMMANDS}.polish:polish",
        "recordings": f"{_COMMANDS}.recordings:recordings_group",
        "mcp": f"{_COMMANDS}.mcp_server:mcp_cmd",
    },
    optional_subcommands=("voiceover", "polish", "recordings", "mcp"),
)
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


# ---------------------------------------------------------------------
# Verb-grouped commands (Phase 0): the canonical invocations. The group
# objects are created eagerly (they are plain Click groups, no imports),
# their subcommands resolve lazily.
# ---------------------------------------------------------------------
slides_group = LazyGroup(
    name="slides",
    help="Slide authoring: normalize, validate, search, language tools, etc.",
    lazy_subcommands={
        "normalize": f"{_COMMANDS}.normalize_slides:normalize_slides_cmd",
        "assign-ids": f"{_COMMANDS}.assign_ids:assign_ids_cmd",
        "coverage": f"{_COMMANDS}.coverage:coverage_cmd",
        "split": f"{_COMMANDS}.split:split_cmd",
        "unify": f"{_COMMANDS}.unify:unify_cmd",
        "language-view": f"{_COMMANDS}.language_view:language_view_cmd",
        "suggest-sync": f"{_COMMANDS}.suggest_sync:suggest_sync_cmd",
        "sync": f"{_COMMANDS}.slides_sync:slides_sync_cmd",
        "translate": f"{_COMMANDS}.slides_translate:slides_translate_cmd",
        # `bootstrap` is an alias for the cold-start direction of the
        # same command.
        "bootstrap": f"{_COMMANDS}.slides_translate:slides_translate_cmd",
        "search": f"{_COMMANDS}.search_slides:search_slides_cmd",
        "tidy": f"{_COMMANDS}.tidy:tidy_cmd",
        "referenced-by": f"{_COMMANDS}.spec_decks:referenced_by_cmd",
        "slug-report": f"{_COMMANDS}.slug_report:slug_report_cmd",
        "coverage-report": f"{_COMMANDS}.coverage_report:coverage_report_cmd",
    },
)
cli.add_command(slides_group)

topic_group = LazyGroup(
    name="topic",
    help="Topic resolution and inspection.",
    lazy_subcommands={"resolve": f"{_COMMANDS}.resolve_topic:resolve_topic_cmd"},
)
cli.add_command(topic_group)

spec_group = LazyGroup(
    name="spec",
    help="Course-spec inspection: resolve the decks a spec pulls in.",
    lazy_subcommands={
        "decks": f"{_COMMANDS}.spec_decks:spec_decks_cmd",
        "orphans": f"{_COMMANDS}.spec_orphans:spec_orphans_cmd",
    },
)
cli.add_command(spec_group)

course_group = LazyGroup(
    name="course",
    help="Course-wide orchestration: readiness gate, mechanical conversion passes.",
    lazy_subcommands={"gate": f"{_COMMANDS}.course_gate:course_gate_cmd"},
)
cli.add_command(course_group)

authoring_group = LazyGroup(
    name="authoring",
    help="Authoring-rules introspection.",
    lazy_subcommands={"rules": f"{_COMMANDS}.authoring_rules:authoring_rules_cmd"},
)
cli.add_command(authoring_group)

# Course-document exports: outline, schedule, and LLM summary. These replace
# the former flat ``clm outline`` / ``clm schedule`` / ``clm summarize``
# top-level commands (removed; see migration docs).
export_group = LazyGroup(
    name="export",
    help="Export course documents: outline, schedule, and LLM summary.",
    lazy_subcommands={
        "outline": f"{_COMMANDS}.outline:outline",
        "schedule": f"{_COMMANDS}.schedule:schedule",
        "calendar": f"{_COMMANDS}.calendar:calendar",
        "summary": f"{_COMMANDS}.summarize:summary",
        "summarize": f"{_COMMANDS}.summarize:summary",  # noun-vs-verb alias
    },
)
cli.add_command(export_group)

# Register PowerShell shell completion (Bash/Zsh/Fish are native to Click).
# This makes the `_CLM_COMPLETE=powershell_complete` protocol work once a
# user has installed the script emitted by `clm completion powershell`.
from clm.cli.completion import register_powershell_completion  # noqa: E402

register_powershell_completion()

# The flat top-level aliases (``normalize-slides``, ``validate-slides``,
# ``extract-voiceover``, …) deprecated in CLM 1.6 were removed in 1.8.
# Use the verb-grouped invocations (``clm slides normalize``,
# ``clm validate``, ``clm voiceover extract``, …) instead.


# ---------------------------------------------------------------------
# Backwards-compatible module attributes (PEP 562). ``clm.cli.main`` used
# to import every command module, so tests and downstream code could do
# ``from clm.cli.main import BuildConfig`` or ``import build``-style
# lookups. Resolve those names lazily instead of eagerly importing the
# world. Optional-extra attributes resolve to ``None`` when their extra
# is not installed, matching the old try/except-ImportError assignments.
# ---------------------------------------------------------------------
_COMPAT_EXPORTS: dict[str, tuple[str, str]] = {
    "BuildConfig": (f"{_COMMANDS}.build", "BuildConfig"),
    "initialize_paths_and_course": (f"{_COMMANDS}.build", "initialize_paths_and_course"),
    "_report_duplicate_file_warnings": (f"{_COMMANDS}.build", "_report_duplicate_file_warnings"),
    "_report_image_collisions": (f"{_COMMANDS}.build", "_report_image_collisions"),
    "_report_loading_issues": (f"{_COMMANDS}.build", "_report_loading_issues"),
    "_is_ci_environment": (f"{_COMMANDS}.shared", "is_ci_environment"),
    "build": (f"{_COMMANDS}.build", "build"),
    "list_targets": (f"{_COMMANDS}.build", "list_targets"),
    "validate_cmd": (f"{_COMMANDS}.validate", "validate_cmd"),
    "delete_database": (f"{_COMMANDS}.database", "delete_database"),
    "db": (f"{_COMMANDS}.database", "db"),
    "status": (f"{_COMMANDS}.status", "status"),
    "monitor": (f"{_COMMANDS}.monitoring", "monitor"),
    "serve": (f"{_COMMANDS}.monitoring", "serve"),
    "info": (f"{_COMMANDS}.info", "info"),
    "run_cmd": (f"{_COMMANDS}.run", "run_cmd"),
    "sync_includes_cmd": (f"{_COMMANDS}.sync_includes", "sync_includes_cmd"),
    "completion_cmd": (f"{_COMMANDS}.completion", "completion_cmd"),
    "assign_ids_cmd": (f"{_COMMANDS}.assign_ids", "assign_ids_cmd"),
    "authoring_rules_cmd": (f"{_COMMANDS}.authoring_rules", "authoring_rules_cmd"),
    "calendar": (f"{_COMMANDS}.calendar", "calendar"),
    "calendar_group": (f"{_COMMANDS}.calendar", "calendar_group"),
    "cassette_group": (f"{_COMMANDS}.cassette", "cassette_group"),
    "config": (f"{_COMMANDS}.config", "config"),
    "course_gate_cmd": (f"{_COMMANDS}.course_gate", "course_gate_cmd"),
    "coverage_cmd": (f"{_COMMANDS}.coverage", "coverage_cmd"),
    "coverage_report_cmd": (f"{_COMMANDS}.coverage_report", "coverage_report_cmd"),
    "docker_group": (f"{_COMMANDS}.docker", "docker_group"),
    "git_group": (f"{_COMMANDS}.git_ops", "git_group"),
    "jobs_group": (f"{_COMMANDS}.jobs", "jobs_group"),
    "jupyterlite_group": (f"{_COMMANDS}.jupyterlite", "jupyterlite_group"),
    "language_view_cmd": (f"{_COMMANDS}.language_view", "language_view_cmd"),
    "normalize_slides_cmd": (f"{_COMMANDS}.normalize_slides", "normalize_slides_cmd"),
    "outline": (f"{_COMMANDS}.outline", "outline"),
    "release_group": (f"{_COMMANDS}.release", "release_group"),
    "resolve_topic_cmd": (f"{_COMMANDS}.resolve_topic", "resolve_topic_cmd"),
    "schedule": (f"{_COMMANDS}.schedule", "schedule"),
    "search_slides_cmd": (f"{_COMMANDS}.search_slides", "search_slides_cmd"),
    "slides_sync_cmd": (f"{_COMMANDS}.slides_sync", "slides_sync_cmd"),
    "slides_translate_cmd": (f"{_COMMANDS}.slides_translate", "slides_translate_cmd"),
    "slug_report_cmd": (f"{_COMMANDS}.slug_report", "slug_report_cmd"),
    "referenced_by_cmd": (f"{_COMMANDS}.spec_decks", "referenced_by_cmd"),
    "spec_decks_cmd": (f"{_COMMANDS}.spec_decks", "spec_decks_cmd"),
    "spec_orphans_cmd": (f"{_COMMANDS}.spec_orphans", "spec_orphans_cmd"),
    "split_cmd": (f"{_COMMANDS}.split", "split_cmd"),
    "suggest_sync_cmd": (f"{_COMMANDS}.suggest_sync", "suggest_sync_cmd"),
    "summary": (f"{_COMMANDS}.summarize", "summary"),
    "tidy_cmd": (f"{_COMMANDS}.tidy", "tidy_cmd"),
    "unify_cmd": (f"{_COMMANDS}.unify", "unify_cmd"),
    "extract_voiceover_cmd": (f"{_COMMANDS}.voiceover_tools", "extract_voiceover_cmd"),
    "inline_voiceover_cmd": (f"{_COMMANDS}.voiceover_tools", "inline_voiceover_cmd"),
    "workers_group": (f"{_COMMANDS}.workers", "workers_group"),
    "zip_group": (f"{_COMMANDS}.zip_ops", "zip_group"),
}

_OPTIONAL_COMPAT_EXPORTS: dict[str, tuple[str, str] | None] = {
    "voiceover_group": None,  # special-cased: needs extract/inline wiring
    "polish_cmd": (f"{_COMMANDS}.polish", "polish"),
    "recordings_group": (f"{_COMMANDS}.recordings", "recordings_group"),
    "mcp_cmd": (f"{_COMMANDS}.mcp_server", "mcp_cmd"),
}


def __getattr__(name: str):
    import importlib

    if name in _OPTIONAL_COMPAT_EXPORTS:
        try:
            if name == "voiceover_group":
                value = _load_voiceover_group()
            else:
                module_name, attr = _OPTIONAL_COMPAT_EXPORTS[name]  # type: ignore[misc]
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
