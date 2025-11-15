import asyncio
import locale
import logging
import shutil
import signal
from pathlib import Path
from time import time

import click
from watchdog.observers import Observer

from clx.core.course import Course
from clx.core.course_spec import CourseSpec
from clx.cli.file_event_handler import FileEventHandler
from clx.cli.git_dir_mover import git_dir_mover
from clx.infrastructure.database.db_operations import DatabaseManager
from clx.infrastructure.messaging.correlation_ids import all_correlation_ids
from clx.infrastructure.utils.path_utils import output_path_for
from clx.infrastructure.backends.sqlite_backend import SqliteBackend

try:
    locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
except locale.Error:
    # Fall back to default locale if en_US.UTF-8 is not available
    try:
        locale.setlocale(locale.LC_ALL, 'C.UTF-8')
    except locale.Error:
        # If that also fails, just use the default system locale
        pass

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def setup_logging(log_level_name: str):
    log_level = logging.getLevelName(log_level_name.upper())
    logging.getLogger().setLevel(log_level)
    logging.getLogger("clx").setLevel(log_level)
    logging.getLogger(__name__).setLevel(log_level)


async def print_all_correlation_ids():
    print_separator(char="-", section="Correlation IDs")
    print(f"Created {len(all_correlation_ids)} Correlation IDs")
    for cid, data in all_correlation_ids.items():
        print(f"  {cid}: {data.format_dependencies()}")


def print_separator(section: str = "", char: str = "="):
    if section:
        prefix = f"{char * 2} {section} "
    else:
        prefix = ""
    print(f"{prefix}{char * (72 - len(prefix))}")


async def main(
    ctx,
    spec_file,
    data_dir,
    output_dir,
    watch,
    print_tracebacks,
    print_correlation_ids,
    log_level,
    db_path,
    ignore_db,
    force_db_init,
    keep_directory,
):
    start_time = time()
    spec_file = spec_file.absolute()
    setup_logging(log_level)
    if data_dir is None:
        data_dir = spec_file.parents[1]
        logger.debug(f"Data directory set to {data_dir}")
        assert data_dir.exists(), f"Data directory {data_dir} does not exist."
    if output_dir is None:
        output_dir = data_dir / "output"
        output_dir.mkdir(exist_ok=True)
        logger.debug(f"Output directory set to {output_dir}")
    logger.info(
        f"Processing course from {spec_file.name} " f"in {data_dir} to {output_dir}"
    )
    spec = CourseSpec.from_file(spec_file)
    course = Course.from_spec(spec, data_dir, output_dir)
    root_dirs = [
        output_path_for(output_dir, is_speaker, language, course.name)
        for language in ["en", "de"]
        for is_speaker in [True, False]
    ]

    with DatabaseManager(db_path, force_init=force_db_init) as db_manager:
        backend = SqliteBackend(
            db_path=db_path,
            workspace_path=output_dir,
            db_manager=db_manager,
            ignore_db=ignore_db
        )

        async with backend:
            with git_dir_mover(root_dirs, keep_directory):
                for root_dir in root_dirs:
                    if not keep_directory:
                        logger.info(f"Removing root directory {root_dir}")
                        shutil.rmtree(root_dir, ignore_errors=True)
                    else:
                        logger.info(f"Not removing root directory {root_dir}")

                await course.process_all(backend)
                end_time = time()

                if print_correlation_ids:
                    await print_all_correlation_ids()

                print_separator(char="-", section="Timing")
                print(f"Total time: {round(end_time - start_time, 2)} seconds")

            if watch:
                logger.info("Watching for file changes")
                loop = asyncio.get_running_loop()

                event_handler = FileEventHandler(
                    course=course,
                    backend=backend,
                    data_dir=data_dir,
                    loop=loop,
                    patterns=["*"],
                )

                observer = Observer()
                observer.schedule(event_handler, str(data_dir), recursive=True)
                observer.start()
                logger.debug("Started observer")

                shut_down = False

                def shutdown_handler(sig, frame):
                    nonlocal shut_down
                    logger.info("Received shutdown signal")
                    shut_down = True

                # Register signal handlers
                signal.signal(signal.SIGINT, shutdown_handler)
                signal.signal(signal.SIGTERM, shutdown_handler)

                try:
                    while not shut_down:
                        await asyncio.sleep(1)
                except Exception as e:
                    logger.info(f"Received exception {e}")
                    raise
                finally:
                    logger.info("Shutting down backend")
                    await backend.shutdown()
                    observer.stop()
                    observer.join()


@click.group()
@click.option(
    "--db-path",
    type=click.Path(),
    default="clx_cache.db",
    help="Path to the SQLite database",
)
@click.pass_context
def cli(ctx, db_path):
    ctx.ensure_object(dict)
    ctx.obj["DB_PATH"] = Path(db_path)


@cli.command()
@click.argument(
    "spec-file",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--data-dir",
    "-d",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
)
@click.option(
    "--output-dir",
    "-o",
    type=click.Path(exists=False, file_okay=False, dir_okay=True, path_type=Path),
)
@click.option(
    "--watch",
    "-w",
    is_flag=True,
    help="Watch for file changes and automatically process them.",
)
@click.option(
    "--print-tracebacks",
    is_flag=True,
    help="Include tracebacks in the error summary.",
)
@click.option(
    "--print-correlation-ids",
    is_flag=True,
    help="Print all correlation IDs that were generated.",
)
@click.option(
    "--log-level",
    type=click.Choice(LOG_LEVELS, case_sensitive=False),
    default="INFO",
    help="Set the logging level.",
)
@click.option(
    "--ignore-db", is_flag=True, help="Ignore the database and process all files"
)
@click.option(
    "--force-db-init",
    is_flag=True,
    help="Force initialization of the database, deleting all data.",
)
@click.option(
    "--keep-directory",
    is_flag=True,
    help="Keep the existing directories and do not move or restore Git directories.",
)
@click.pass_context
def build(
    ctx,
    spec_file,
    data_dir,
    output_dir,
    watch,
    print_tracebacks,
    print_correlation_ids,
    log_level,
    ignore_db,
    force_db_init,
    keep_directory,
):
    db_path = ctx.obj["DB_PATH"]
    asyncio.run(
        main(
            ctx,
            spec_file,
            data_dir,
            output_dir,
            watch,
            print_tracebacks,
            print_correlation_ids,
            log_level,
            db_path,
            ignore_db,
            force_db_init,
            keep_directory,
        )
    )


@cli.command()
@click.pass_context
def delete_database(ctx):
    db_path = ctx.obj["DB_PATH"]
    if db_path.exists():
        db_path.unlink()
        click.echo(f"Database at {db_path} has been deleted.")
    else:
        click.echo(f"No database found at {db_path}.")


@cli.group()
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
    from clx.infrastructure.config import (
        get_config_file_locations,
        write_example_config,
    )

    locations = get_config_file_locations()
    config_path = locations[location.lower()]

    # Check if file already exists
    if config_path.exists() and not force:
        click.echo(
            f"Configuration file already exists at {config_path}\n"
            f"Use --force to overwrite."
        )
        return

    try:
        created_path = write_example_config(location=location.lower())
        click.echo(f"✓ Created configuration file: {created_path}")
        click.echo(f"\nEdit this file to customize CLX settings.")
        click.echo(f"See documentation for available options.")
    except PermissionError as e:
        click.echo(f"✗ Error: Permission denied creating config file: {e}", err=True)
    except Exception as e:
        click.echo(f"✗ Error creating configuration file: {e}", err=True)


@config.command(name="show")
def config_show():
    """Show current configuration values.

    This command displays the current configuration, including values
    from all sources (config files and environment variables).
    """
    from clx.infrastructure.config import get_config

    cfg = get_config(reload=True)

    click.echo("Current CLX Configuration:")
    click.echo("=" * 60)

    click.echo("\n[Paths]")
    click.echo(f"  db_path: {cfg.paths.db_path}")
    click.echo(f"  workspace_path: {cfg.paths.workspace_path or '(not set)'}")

    click.echo("\n[External Tools]")
    click.echo(f"  plantuml_jar: {cfg.external_tools.plantuml_jar or '(not set)'}")
    click.echo(
        f"  drawio_executable: {cfg.external_tools.drawio_executable or '(not set)'}"
    )

    click.echo("\n[Logging]")
    click.echo(f"  log_level: {cfg.logging.log_level}")
    click.echo(f"  enable_test_logging: {cfg.logging.enable_test_logging}")
    click.echo(f"  e2e_progress_interval: {cfg.logging.testing.e2e_progress_interval}")
    click.echo(
        f"  e2e_long_job_threshold: {cfg.logging.testing.e2e_long_job_threshold}"
    )
    click.echo(
        f"  e2e_show_worker_details: {cfg.logging.testing.e2e_show_worker_details}"
    )

    click.echo("\n[Jupyter]")
    click.echo(
        f"  jinja_line_statement_prefix: {cfg.jupyter.jinja_line_statement_prefix}"
    )
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
    from clx.infrastructure.config import find_config_files, get_config_file_locations

    locations = get_config_file_locations()
    existing = find_config_files()

    click.echo("Configuration File Locations:")
    click.echo("=" * 60)

    click.echo("\nSystem config (lowest priority):")
    click.echo(f"  Path: {locations['system']}")
    if existing["system"]:
        click.echo(f"  Status: ✓ Exists")
    else:
        click.echo(f"  Status: Not found")

    click.echo("\nUser config:")
    click.echo(f"  Path: {locations['user']}")
    if existing["user"]:
        click.echo(f"  Status: ✓ Exists")
    else:
        click.echo(f"  Status: Not found")

    click.echo("\nProject config (highest priority):")
    click.echo(f"  Path: {locations['project']}")
    if existing["project"]:
        click.echo(f"  Status: ✓ Exists")
    else:
        click.echo(f"  Status: Not found")

    click.echo("\nPriority order (highest to lowest):")
    click.echo("  1. Environment variables")
    click.echo("  2. Project config (.clx/config.toml or clx.toml)")
    click.echo("  3. User config (~/.config/clx/config.toml)")
    click.echo("  4. System config (/etc/clx/config.toml)")
    click.echo("  5. Default values")


if __name__ == "__main__":
    cli()
