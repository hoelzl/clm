"""Configuration management commands.

This module provides commands for managing CLX configuration files.
"""

import click


@click.group()
def config():
    """Manage CLX configuration files."""
    pass


@config.command(name="init")
@click.option(
    "--location",
    type=click.Choice(["user", "project"], case_sensitive=False),
    default="user",
    help="Where to create the configuration file.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite existing configuration file.",
)
def config_init(location, force):
    """Create an example configuration file.

    This command creates a configuration file with all available options
    documented. By default, it creates a user-level config file at
    ~/.config/clx/config.toml (or platform equivalent).

    Use --location=project to create a project-level config file at
    .clx/config.toml in the current directory.

    Examples:
        clx config init                  # Create user config
        clx config init --location=project  # Create project config
        clx config init --force          # Overwrite existing config
    """
    from clm.infrastructure.config import (
        get_config_file_locations,
        write_example_config,
    )

    locations = get_config_file_locations()
    config_path = locations[location.lower()]

    # Check if file already exists
    if config_path.exists() and not force:
        click.echo(f"Configuration file already exists at {config_path}\nUse --force to overwrite.")
        return

    try:
        created_path = write_example_config(location=location.lower())
        click.echo(f"Created configuration file: {created_path}")
        click.echo("\nEdit this file to customize CLX settings.")
        click.echo("See documentation for available options.")
    except PermissionError as e:
        click.echo(f"Error: Permission denied creating config file: {e}", err=True)
    except Exception as e:
        click.echo(f"Error creating configuration file: {e}", err=True)


@config.command(name="show")
def config_show():
    """Show current configuration values.

    This command displays the current configuration, including values
    from all sources (config files and environment variables).
    """
    from clm.infrastructure.config import get_config

    cfg = get_config(reload=True)

    click.echo("Current CLX Configuration:")
    click.echo("=" * 60)

    click.echo("\n[Paths]")
    click.echo(f"  cache_db_path: {cfg.paths.cache_db_path}")
    click.echo(f"  jobs_db_path: {cfg.paths.jobs_db_path}")
    click.echo(f"  workspace_path: {cfg.paths.workspace_path or '(not set)'}")

    click.echo("\n[External Tools]")
    click.echo(f"  plantuml_jar: {cfg.external_tools.plantuml_jar or '(not set)'}")
    click.echo(f"  drawio_executable: {cfg.external_tools.drawio_executable or '(not set)'}")

    click.echo("\n[Logging]")
    click.echo(f"  log_level: {cfg.logging.log_level}")
    click.echo(f"  enable_test_logging: {cfg.logging.enable_test_logging}")
    click.echo(f"  e2e_progress_interval: {cfg.logging.testing.e2e_progress_interval}")
    click.echo(f"  e2e_long_job_threshold: {cfg.logging.testing.e2e_long_job_threshold}")
    click.echo(f"  e2e_show_worker_details: {cfg.logging.testing.e2e_show_worker_details}")

    click.echo("\n[Jupyter]")
    click.echo(f"  jinja_line_statement_prefix: {cfg.jupyter.jinja_line_statement_prefix}")
    click.echo(f"  jinja_templates_path: {cfg.jupyter.jinja_templates_path}")
    click.echo(f"  log_cell_processing: {cfg.jupyter.log_cell_processing}")

    click.echo("\n[Workers]")
    click.echo(f"  worker_type: {cfg.workers.worker_type or '(not set)'}")
    click.echo(f"  worker_id: {cfg.workers.worker_id or '(not set)'}")
    click.echo(f"  use_sqlite_queue: {cfg.workers.use_sqlite_queue}")


@config.command(name="locate")
def config_locate():
    """Show configuration file locations.

    This command shows where CLX looks for configuration files
    and which files currently exist.
    """
    from clm.infrastructure.config import find_config_files, get_config_file_locations

    locations = get_config_file_locations()
    existing = find_config_files()

    click.echo("Configuration File Locations:")
    click.echo("=" * 60)

    click.echo("\nSystem config (lowest priority):")
    click.echo(f"  Path: {locations['system']}")
    if existing["system"]:
        click.echo("  Status: Exists")
    else:
        click.echo("  Status: Not found")

    click.echo("\nUser config:")
    click.echo(f"  Path: {locations['user']}")
    if existing["user"]:
        click.echo("  Status: Exists")
    else:
        click.echo("  Status: Not found")

    click.echo("\nProject config (highest priority):")
    click.echo(f"  Path: {locations['project']}")
    if existing["project"]:
        click.echo("  Status: Exists")
    else:
        click.echo("  Status: Not found")

    click.echo("\nPriority order (highest to lowest):")
    click.echo("  1. Environment variables")
    click.echo("  2. Project config (.clx/config.toml or clx.toml)")
    click.echo("  3. User config (~/.config/clx/config.toml)")
    click.echo("  4. System config (/etc/clx/config.toml)")
    click.echo("  5. Default values")
