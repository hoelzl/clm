"""MCP server CLI command."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import click


@click.command("mcp")
@click.option(
    "--data-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Course data directory (contains slides/). "
    "Default: CLM_DATA_DIR env var, or current directory.",
)
@click.option(
    "--log-level",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
    default="WARNING",
    show_default=True,
    help="Logging level (logs go to stderr).",
)
def mcp_cmd(data_dir: Path | None, log_level: str):
    """Start the CLM MCP server (stdio transport).

    Exposes course navigation, search, and outline tools via the
    Model Context Protocol.  Designed to be launched by an MCP client
    (e.g., Claude Code via .mcp.json).

    \b
    Examples:
        clm mcp
        clm mcp --data-dir /path/to/course
        CLM_DATA_DIR=/path/to/course clm mcp
    """
    from clm.mcp.server import run_server

    logging.basicConfig(level=getattr(logging, log_level.upper()), force=True)

    resolved_dir = _resolve_data_dir(data_dir)
    run_server(resolved_dir)


def _resolve_data_dir(explicit: Path | None) -> Path:
    """Resolve the data directory: explicit > env var > cwd."""
    if explicit is not None:
        return explicit
    env_val = os.environ.get("CLM_DATA_DIR")
    if env_val:
        return Path(env_val)
    return Path.cwd()
