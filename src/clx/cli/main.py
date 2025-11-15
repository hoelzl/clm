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


@cli.command(name="start-services")
@click.option(
    "--db-path",
    type=click.Path(),
    default="clx_jobs.db",
    help="Path to SQLite database",
)
@click.option(
    "--workspace",
    type=click.Path(),
    default=".",
    help="Workspace path for workers",
)
@click.option(
    "--wait/--no-wait",
    default=True,
    help="Wait for workers to register",
)
def start_services(db_path, workspace, wait):
    """Start persistent worker services.

    This starts workers that will continue running after this command exits.
    Workers must be explicitly stopped with 'clx stop-services'.

    Examples:
        clx start-services
        clx start-services --db-path=/data/clx_jobs.db
        clx start-services --no-wait
    """
    from clx.infrastructure.database.schema import init_database
    from clx.infrastructure.workers.config_loader import load_worker_config
    from clx.infrastructure.workers.lifecycle_manager import WorkerLifecycleManager

    db_path = Path(db_path).absolute()
    workspace = Path(workspace).absolute()

    # Validate paths
    if not workspace.exists():
        click.echo(f"Error: Workspace directory does not exist: {workspace}", err=True)
        return 1

    # Initialize database
    click.echo(f"Initializing database: {db_path}")
    init_database(db_path)

    # Load configuration
    config = load_worker_config()

    # Create lifecycle manager
    manager = WorkerLifecycleManager(
        config=config, db_path=db_path, workspace_path=workspace
    )

    try:
        # Start persistent workers
        click.echo("Starting persistent workers...")
        workers = manager.start_persistent_workers()

        if not workers:
            click.echo("Warning: No workers were started", err=True)
            return 1

        # Save state
        manager.state_manager.save_worker_state(
            workers=workers,
            db_path=db_path,
            workspace_path=str(workspace),
            network_name=config.network_name,
        )

        # Report success
        click.echo(f"✓ Started {len(workers)} worker(s)")
        click.echo("")
        click.echo("Workers by type:")
        from collections import Counter

        counts = Counter(w.worker_type for w in workers)
        for worker_type, count in sorted(counts.items()):
            click.echo(f"  {worker_type}: {count}")

        click.echo("")
        click.echo("To process a course:")
        click.echo(f"  clx build course.yaml --db-path={db_path}")
        click.echo("")
        click.echo("To stop workers:")
        click.echo(f"  clx stop-services --db-path={db_path}")

        return 0

    except Exception as e:
        click.echo(f"Failed to start services: {e}", err=True)
        logger.error(f"Failed to start services: {e}", exc_info=True)
        return 1


@cli.command(name="stop-services")
@click.option(
    "--db-path",
    type=click.Path(),
    default="clx_jobs.db",
    help="Path to SQLite database",
)
@click.option(
    "--force",
    is_flag=True,
    help="Force cleanup even if state file is missing",
)
def stop_services(db_path, force):
    """Stop persistent worker services.

    Stops workers that were started with 'clx start-services'.

    Examples:
        clx stop-services
        clx stop-services --db-path=/data/clx_jobs.db
        clx stop-services --force
    """
    from clx.infrastructure.workers.config_loader import load_worker_config
    from clx.infrastructure.workers.lifecycle_manager import WorkerLifecycleManager
    from clx.infrastructure.workers.state_manager import WorkerStateManager

    db_path = Path(db_path).absolute()

    # Load state
    state_manager = WorkerStateManager()
    state = state_manager.load_worker_state()

    if not state and not force:
        click.echo("Error: No worker state found.", err=True)
        click.echo("Did you run 'clx start-services'?")
        click.echo("Use --force to clean up workers from database anyway.")
        return 1

    if state:
        # Validate database path matches
        if state.db_path != str(db_path):
            click.echo(
                f"Warning: Database path mismatch:\n"
                f"  State file:  {state.db_path}\n"
                f"  You specified: {db_path}",
                err=True,
            )
            if not force:
                click.echo("Use --force to override", err=True)
                return 1

    # Load configuration
    config = load_worker_config()

    # Create lifecycle manager
    manager = WorkerLifecycleManager(
        config=config,
        db_path=db_path,
        workspace_path=db_path.parent,  # Doesn't matter for shutdown
    )

    try:
        if state and state.workers:
            click.echo(f"Stopping {len(state.workers)} worker(s)...")
            manager.stop_persistent_workers(state.workers)
        else:
            click.echo("Cleaning up workers from database...")
            manager.cleanup_all_workers()

        # Clear state file
        state_manager.clear_worker_state()

        click.echo("✓ Services stopped")
        return 0

    except Exception as e:
        click.echo(f"Failed to stop services: {e}", err=True)
        logger.error(f"Failed to stop services: {e}", exc_info=True)
        return 1


@cli.group(name="workers")
def workers_group():
    """Manage CLX workers."""
    pass


@workers_group.command(name="list")
@click.option(
    "--db-path",
    type=click.Path(),
    default="clx_jobs.db",
    help="Path to SQLite database",
)
@click.option(
    "--format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.option(
    "--status",
    multiple=True,
    type=click.Choice(["idle", "busy", "hung", "dead"], case_sensitive=False),
    help="Filter by status (can specify multiple)",
)
def workers_list(db_path, format, status):
    """List registered workers.

    Examples:
        clx workers list
        clx workers list --status=idle
        clx workers list --format=json
        clx workers list --status=busy --status=hung
    """
    from clx.infrastructure.workers.discovery import WorkerDiscovery

    db_path = Path(db_path)

    if not db_path.exists():
        click.echo(f"Error: Database not found: {db_path}", err=True)
        return 1

    # Discover workers
    discovery = WorkerDiscovery(db_path)
    status_filter = list(status) if status else None
    workers = discovery.discover_workers(status_filter=status_filter)

    if not workers:
        click.echo("No workers found")
        return 0

    if format == "json":
        import json

        data = [
            {
                "id": w.db_id,
                "type": w.worker_type,
                "executor_id": w.executor_id,
                "status": w.status,
                "started_at": w.started_at.isoformat(),
                "last_heartbeat": w.last_heartbeat.isoformat(),
                "jobs_processed": w.jobs_processed,
                "jobs_failed": w.jobs_failed,
                "is_healthy": w.is_healthy,
            }
            for w in workers
        ]
        click.echo(json.dumps(data, indent=2))
    else:
        # Table format
        try:
            from tabulate import tabulate
        except ImportError:
            click.echo(
                "Error: tabulate library not installed. Use --format=json instead.",
                err=True,
            )
            return 1

        rows = []
        for w in workers:
            # Calculate uptime
            uptime = datetime.now() - w.started_at
            uptime_str = str(uptime).split(".")[0]  # Remove microseconds

            # Health indicator
            health = "✓" if w.is_healthy else "✗"

            rows.append(
                [
                    w.db_id,
                    w.worker_type,
                    w.executor_id[:12] if len(w.executor_id) > 12 else w.executor_id,
                    w.status,
                    health,
                    uptime_str,
                    w.jobs_processed,
                    w.jobs_failed,
                ]
            )

        headers = [
            "ID",
            "Type",
            "Executor",
            "Status",
            "Health",
            "Uptime",
            "Processed",
            "Failed",
        ]
        click.echo(tabulate(rows, headers=headers, tablefmt="simple"))

    return 0


@workers_group.command(name="cleanup")
@click.option(
    "--db-path",
    type=click.Path(),
    default="clx_jobs.db",
    help="Path to SQLite database",
)
@click.option(
    "--force",
    is_flag=True,
    help="Skip confirmation prompt",
)
@click.option(
    "--all",
    "cleanup_all",
    is_flag=True,
    help="Clean up all workers (not just dead/hung)",
)
def workers_cleanup(db_path, force, cleanup_all):
    """Clean up dead workers and orphaned processes.

    By default, this removes workers that are:
    - Marked as 'dead' or 'hung' in the database
    - Have stale heartbeats (>60 seconds old)

    Examples:
        clx workers cleanup
        clx workers cleanup --force
        clx workers cleanup --all --force
    """
    from clx.infrastructure.database.job_queue import JobQueue
    from clx.infrastructure.workers.discovery import WorkerDiscovery

    db_path = Path(db_path)

    if not db_path.exists():
        click.echo(f"Error: Database not found: {db_path}", err=True)
        return 1

    # Discover workers to clean up
    discovery = WorkerDiscovery(db_path)

    if cleanup_all:
        workers = discovery.discover_workers()
        click.echo("Warning: Cleaning up ALL workers", err=True)
    else:
        # Only dead/hung workers or stale heartbeats
        workers = discovery.discover_workers(status_filter=["dead", "hung"])

        # Also include workers with very stale heartbeats
        all_workers = discovery.discover_workers(status_filter=["idle", "busy"])
        stale_workers = [
            w
            for w in all_workers
            if (datetime.now() - w.last_heartbeat).total_seconds() > 60
        ]
        workers.extend(stale_workers)

    if not workers:
        click.echo("No workers to clean up")
        return 0

    # Show what will be cleaned
    click.echo(f"Found {len(workers)} worker(s) to clean up:")
    for w in workers:
        click.echo(f"  #{w.db_id} ({w.worker_type}, {w.status})")

    # Confirm
    if not force:
        if not click.confirm("Remove these workers?"):
            click.echo("Cancelled")
            return 0

    # Clean up
    job_queue = JobQueue(db_path)
    conn = job_queue._get_conn()

    cleaned = 0
    for worker in workers:
        try:
            # Remove from database
            conn.execute("DELETE FROM workers WHERE id = ?", (worker.db_id,))
            cleaned += 1
            click.echo(f"  Cleaned up worker #{worker.db_id}")

        except Exception as e:
            click.echo(f"  Error cleaning worker #{worker.db_id}: {e}", err=True)

    conn.commit()

    click.echo(f"✓ Cleaned up {cleaned} worker(s)")
    return 0


if __name__ == "__main__":
    cli()
