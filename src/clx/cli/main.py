import asyncio
import locale
import logging
import shutil
import signal
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import time

import click
from watchdog.observers import Observer

from clx.cli.file_event_handler import FileEventHandler
from clx.cli.git_dir_mover import git_dir_mover
from clx.core.course import Course
from clx.core.course_spec import CourseSpec
from clx.infrastructure.backends.sqlite_backend import SqliteBackend
from clx.infrastructure.database.db_operations import DatabaseManager
from clx.infrastructure.messaging.correlation_ids import all_correlation_ids
from clx.infrastructure.utils.path_utils import output_path_for

try:
    locale.setlocale(locale.LC_ALL, "en_US.UTF-8")
except locale.Error:
    # Fall back to default locale if en_US.UTF-8 is not available
    try:
        locale.setlocale(locale.LC_ALL, "C.UTF-8")
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


def _is_ci_environment() -> bool:
    """Detect if running in a CI/CD environment.

    Checks for common CI environment variables:
    - CI=true (generic)
    - GITHUB_ACTIONS=true (GitHub Actions)
    - GITLAB_CI=true (GitLab CI)
    - JENKINS_HOME (Jenkins)
    - CIRCLECI=true (CircleCI)
    - TRAVIS=true (Travis CI)
    - BUILDKITE=true (Buildkite)
    - DRONE=true (Drone CI)

    Returns:
        True if running in a CI environment, False otherwise
    """
    import os

    ci_indicators = [
        "CI",
        "GITHUB_ACTIONS",
        "GITLAB_CI",
        "JENKINS_HOME",
        "CIRCLECI",
        "TRAVIS",
        "BUILDKITE",
        "DRONE",
    ]

    return any(os.getenv(indicator) for indicator in ci_indicators)


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


@dataclass
class BuildConfig:
    """Configuration for course build process."""

    spec_file: Path
    data_dir: Path
    output_dir: Path
    log_level: str
    cache_db_path: Path
    jobs_db_path: Path
    ignore_db: bool
    force_db_init: bool
    keep_directory: bool
    watch: bool
    print_correlation_ids: bool

    # Worker configuration
    workers: str | None
    notebook_workers: int | None
    plantuml_workers: int | None
    drawio_workers: int | None
    no_auto_start: bool
    no_auto_stop: bool
    fresh_workers: bool

    # Build output configuration
    output_mode: str = "default"
    no_progress: bool = False


def initialize_paths_and_course(config: BuildConfig) -> tuple[Course, list[Path]]:
    """Initialize paths, load course spec, and create course object.

    Args:
        config: Build configuration

    Returns:
        Tuple of (course object, list of root output directories)
    """
    spec_file = config.spec_file.absolute()
    setup_logging(config.log_level)

    # Set data_dir to parent of spec file if not provided
    data_dir = config.data_dir
    if data_dir is None:
        data_dir = spec_file.parents[1]
        logger.debug(f"Data directory set to {data_dir}")
        assert data_dir.exists(), f"Data directory {data_dir} does not exist."

    # Set output_dir to data_dir/output if not provided
    output_dir = config.output_dir
    if output_dir is None:
        output_dir = data_dir / "output"
        output_dir.mkdir(exist_ok=True)
        logger.debug(f"Output directory set to {output_dir}")

    logger.info(f"Processing course from {spec_file.name} in {data_dir} to {output_dir}")

    # Load course specification and create course object
    spec = CourseSpec.from_file(spec_file)
    course = Course.from_spec(spec, data_dir, output_dir)

    # Calculate root directories for all language/speaker combinations
    root_dirs = [
        output_path_for(output_dir, is_speaker, language, course.name)
        for language in ["en", "de"]
        for is_speaker in [True, False]
    ]

    return course, root_dirs


def configure_workers(config: BuildConfig):
    """Load worker configuration with CLI overrides.

    Args:
        config: Build configuration

    Returns:
        Worker configuration object
    """
    from clx.infrastructure.workers.config_loader import load_worker_config

    cli_overrides = {}

    if config.workers:
        cli_overrides["default_execution_mode"] = config.workers
    if config.notebook_workers is not None:
        cli_overrides["notebook_count"] = config.notebook_workers
    if config.plantuml_workers is not None:
        cli_overrides["plantuml_count"] = config.plantuml_workers
    if config.drawio_workers is not None:
        cli_overrides["drawio_count"] = config.drawio_workers
    if config.no_auto_start:
        cli_overrides["auto_start"] = False
    if config.no_auto_stop:
        cli_overrides["auto_stop"] = False
    if config.fresh_workers:
        cli_overrides["reuse_workers"] = False

    return load_worker_config(cli_overrides)


def start_managed_workers(lifecycle_manager, worker_config) -> list:
    """Start managed workers if needed.

    Args:
        lifecycle_manager: Worker lifecycle manager
        worker_config: Worker configuration

    Returns:
        List of started worker IDs/handles

    Raises:
        Exception: If worker startup fails
    """
    started_workers = []
    should_start = lifecycle_manager.should_start_workers()

    if should_start:
        logger.info("Starting managed workers...")
        try:
            started_workers = lifecycle_manager.start_managed_workers()
            logger.info(f"Started {len(started_workers)} worker(s)")
        except Exception as e:
            logger.error(f"Failed to start workers: {e}", exc_info=True)
            raise

    return started_workers


async def process_course_with_backend(
    course: Course,
    root_dirs: list[Path],
    backend,
    config: BuildConfig,
    start_time: float,
):
    """Process course and optionally watch for changes.

    Args:
        course: Course object to process
        root_dirs: List of root output directories
        backend: Backend for job execution
        config: Build configuration
        start_time: Start time for timing metrics
    """
    with git_dir_mover(root_dirs, config.keep_directory):
        # Clean or preserve root directories
        for root_dir in root_dirs:
            if not config.keep_directory:
                logger.info(f"Removing root directory {root_dir}")
                shutil.rmtree(root_dir, ignore_errors=True)
            else:
                logger.info(f"Not removing root directory {root_dir}")

        # Process all course files
        await course.process_all(backend)
        end_time = time()

        # Print correlation IDs if requested
        if config.print_correlation_ids:
            await print_all_correlation_ids()

        # Print timing information
        print_separator(char="-", section="Timing")
        print(f"Total time: {round(end_time - start_time, 2)} seconds")

    # Watch mode: monitor for file changes and rebuild
    if config.watch:
        await watch_and_rebuild(course, backend, config.data_dir)


async def watch_and_rebuild(course: Course, backend, data_dir: Path):
    """Watch for file changes and automatically rebuild course.

    Args:
        course: Course object to process
        backend: Backend for job execution
        data_dir: Data directory to monitor
    """
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


async def main(
    ctx,
    spec_file,
    data_dir,
    output_dir,
    watch,
    print_correlation_ids,
    log_level,
    cache_db_path,
    jobs_db_path,
    ignore_db,
    force_db_init,
    keep_directory,
    workers,
    notebook_workers,
    plantuml_workers,
    drawio_workers,
    no_auto_start,
    no_auto_stop,
    fresh_workers,
):
    """Main orchestration function for course building.

    This function coordinates the build process by:
    1. Initializing paths and loading the course
    2. Configuring and starting workers
    3. Processing the course with the backend
    4. Cleaning up workers when done
    """
    start_time = time()

    # Create configuration object from CLI parameters
    config = BuildConfig(
        spec_file=spec_file,
        data_dir=data_dir,
        output_dir=output_dir,
        log_level=log_level,
        cache_db_path=cache_db_path,
        jobs_db_path=jobs_db_path,
        ignore_db=ignore_db,
        force_db_init=force_db_init,
        keep_directory=keep_directory,
        watch=watch,
        print_correlation_ids=print_correlation_ids,
        workers=workers,
        notebook_workers=notebook_workers,
        plantuml_workers=plantuml_workers,
        drawio_workers=drawio_workers,
        no_auto_start=no_auto_start,
        no_auto_stop=no_auto_stop,
        fresh_workers=fresh_workers,
    )

    # Initialize paths, load course spec, and create course object
    course, root_dirs = initialize_paths_and_course(config)

    # Load worker configuration with CLI overrides
    worker_config = configure_workers(config)

    # Initialize job queue database (workers table, jobs table, etc.)
    from clx.infrastructure.database.schema import init_database
    from clx.infrastructure.workers.lifecycle_manager import WorkerLifecycleManager

    logger.debug(f"Initializing job queue database: {config.jobs_db_path}")
    init_database(config.jobs_db_path)

    # Create worker lifecycle manager
    lifecycle_manager = WorkerLifecycleManager(
        config=worker_config,
        db_path=config.jobs_db_path,
        workspace_path=course.output_dir,
    )

    # Start managed workers if needed
    started_workers = start_managed_workers(lifecycle_manager, worker_config)

    # Setup signal handler for graceful shutdown
    shutdown_requested = False

    def shutdown_handler(signum, frame):
        nonlocal shutdown_requested
        if shutdown_requested:
            # Second signal - force exit
            logger.warning(f"Received second shutdown signal {signum}, forcing exit")
            sys.exit(1)

        logger.info(f"Received shutdown signal {signum}, initiating graceful shutdown...")
        shutdown_requested = True

        # Trigger async cancellation by raising KeyboardInterrupt
        # This will be caught by the try/except and allow cleanup
        raise KeyboardInterrupt(f"Shutdown signal {signum} received")

    # Register signal handlers
    original_sigterm = signal.signal(signal.SIGTERM, shutdown_handler)
    original_sigint = signal.signal(signal.SIGINT, shutdown_handler)

    try:
        with DatabaseManager(config.cache_db_path, force_init=config.force_db_init) as db_manager:
            backend = SqliteBackend(
                db_path=config.jobs_db_path,
                workspace_path=course.output_dir,
                db_manager=db_manager,
                ignore_db=config.ignore_db,
            )

            try:
                async with backend:
                    await process_course_with_backend(
                        course=course,
                        root_dirs=root_dirs,
                        backend=backend,
                        config=config,
                        start_time=start_time,
                    )
            except KeyboardInterrupt:
                logger.info("Build interrupted, cleaning up...")
                # Re-raise to trigger cleanup
                raise
            finally:
                # Stop managed workers if auto_stop is enabled
                if started_workers and worker_config.auto_stop:
                    logger.info("Stopping managed workers...")
                    try:
                        lifecycle_manager.stop_managed_workers(started_workers)
                        logger.info(f"Stopped {len(started_workers)} worker(s)")
                    except Exception as e:
                        logger.error(f"Failed to stop workers: {e}", exc_info=True)
    finally:
        # Restore original signal handlers
        signal.signal(signal.SIGTERM, original_sigterm)
        signal.signal(signal.SIGINT, original_sigint)


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
    ctx.ensure_object(dict)
    ctx.obj["CACHE_DB_PATH"] = Path(cache_db_path)
    ctx.obj["JOBS_DB_PATH"] = Path(jobs_db_path)


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
@click.option("--ignore-db", is_flag=True, help="Ignore the database and process all files")
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
@click.option(
    "--workers",
    type=click.Choice(["direct", "docker"], case_sensitive=False),
    help="Worker execution mode (overrides config)",
)
@click.option(
    "--notebook-workers",
    type=int,
    help="Number of notebook workers (overrides config)",
)
@click.option(
    "--plantuml-workers",
    type=int,
    help="Number of PlantUML workers (overrides config)",
)
@click.option(
    "--drawio-workers",
    type=int,
    help="Number of Draw.io workers (overrides config)",
)
@click.option(
    "--no-auto-start",
    is_flag=True,
    help="Don't automatically start workers (use existing)",
)
@click.option(
    "--no-auto-stop",
    is_flag=True,
    help="Don't automatically stop workers after build",
)
@click.option(
    "--fresh-workers",
    is_flag=True,
    help="Start fresh workers (don't reuse existing)",
)
@click.pass_context
def build(
    ctx,
    spec_file,
    data_dir,
    output_dir,
    watch,
    print_correlation_ids,
    log_level,
    ignore_db,
    force_db_init,
    keep_directory,
    workers,
    notebook_workers,
    plantuml_workers,
    drawio_workers,
    no_auto_start,
    no_auto_stop,
    fresh_workers,
):
    cache_db_path = ctx.obj["CACHE_DB_PATH"]
    jobs_db_path = ctx.obj["JOBS_DB_PATH"]
    asyncio.run(
        main(
            ctx,
            spec_file,
            data_dir,
            output_dir,
            watch,
            print_correlation_ids,
            log_level,
            cache_db_path,
            jobs_db_path,
            ignore_db,
            force_db_init,
            keep_directory,
            workers,
            notebook_workers,
            plantuml_workers,
            drawio_workers,
            no_auto_start,
            no_auto_stop,
            fresh_workers,
        )
    )


@cli.command()
@click.option(
    "--which",
    type=click.Choice(["cache", "jobs", "both"], case_sensitive=False),
    default="both",
    help="Which database to delete",
)
@click.pass_context
def delete_database(ctx, which):
    """Delete CLX databases.

    Examples:
        clx delete-database --which=cache
        clx delete-database --which=jobs
        clx delete-database --which=both
    """
    cache_db_path = ctx.obj["CACHE_DB_PATH"]
    jobs_db_path = ctx.obj["JOBS_DB_PATH"]

    deleted = []

    if which in ("cache", "both"):
        if cache_db_path.exists():
            cache_db_path.unlink()
            deleted.append(f"cache database ({cache_db_path})")

    if which in ("jobs", "both"):
        if jobs_db_path.exists():
            jobs_db_path.unlink()
            deleted.append(f"job queue database ({jobs_db_path})")

    if deleted:
        click.echo(f"Deleted: {', '.join(deleted)}")
    else:
        click.echo("No databases found to delete.")


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
        click.echo(f"Configuration file already exists at {config_path}\nUse --force to overwrite.")
        return

    try:
        created_path = write_example_config(location=location.lower())
        click.echo(f"✓ Created configuration file: {created_path}")
        click.echo("\nEdit this file to customize CLX settings.")
        click.echo("See documentation for available options.")
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
    from clx.infrastructure.config import find_config_files, get_config_file_locations

    locations = get_config_file_locations()
    existing = find_config_files()

    click.echo("Configuration File Locations:")
    click.echo("=" * 60)

    click.echo("\nSystem config (lowest priority):")
    click.echo(f"  Path: {locations['system']}")
    if existing["system"]:
        click.echo("  Status: ✓ Exists")
    else:
        click.echo("  Status: Not found")

    click.echo("\nUser config:")
    click.echo(f"  Path: {locations['user']}")
    if existing["user"]:
        click.echo("  Status: ✓ Exists")
    else:
        click.echo("  Status: Not found")

    click.echo("\nProject config (highest priority):")
    click.echo(f"  Path: {locations['project']}")
    if existing["project"]:
        click.echo("  Status: ✓ Exists")
    else:
        click.echo("  Status: Not found")

    click.echo("\nPriority order (highest to lowest):")
    click.echo("  1. Environment variables")
    click.echo("  2. Project config (.clx/config.toml or clx.toml)")
    click.echo("  3. User config (~/.config/clx/config.toml)")
    click.echo("  4. System config (/etc/clx/config.toml)")
    click.echo("  5. Default values")


@cli.command(name="start-services")
@click.option(
    "--jobs-db-path",
    type=click.Path(),
    default="clx_jobs.db",
    help="Path to the job queue database",
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
def start_services(jobs_db_path, workspace, wait):
    """Start persistent worker services.

    This starts workers that will continue running after this command exits.
    Workers must be explicitly stopped with 'clx stop-services'.

    Examples:
        clx start-services
        clx start-services --jobs-db-path=/data/clx_jobs.db
        clx start-services --no-wait
    """
    from clx.infrastructure.database.schema import init_database
    from clx.infrastructure.workers.config_loader import load_worker_config
    from clx.infrastructure.workers.lifecycle_manager import WorkerLifecycleManager

    jobs_db_path = Path(jobs_db_path).absolute()
    workspace = Path(workspace).absolute()

    # Validate paths
    if not workspace.exists():
        click.echo(f"Error: Workspace directory does not exist: {workspace}", err=True)
        return 1

    # Initialize database
    click.echo(f"Initializing job queue database: {jobs_db_path}")
    init_database(jobs_db_path)

    # Load configuration
    config = load_worker_config()

    # Create lifecycle manager
    manager = WorkerLifecycleManager(config=config, db_path=jobs_db_path, workspace_path=workspace)

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
            db_path=jobs_db_path,
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
        click.echo(f"  clx build course.yaml --jobs-db-path={jobs_db_path}")
        click.echo("")
        click.echo("To stop workers:")
        click.echo(f"  clx stop-services --jobs-db-path={jobs_db_path}")

        return 0

    except Exception as e:
        click.echo(f"Failed to start services: {e}", err=True)
        logger.error(f"Failed to start services: {e}", exc_info=True)
        return 1


@cli.command(name="stop-services")
@click.option(
    "--jobs-db-path",
    type=click.Path(),
    default="clx_jobs.db",
    help="Path to the job queue database",
)
@click.option(
    "--force",
    is_flag=True,
    help="Force cleanup even if state file is missing",
)
def stop_services(jobs_db_path, force):
    """Stop persistent worker services.

    Stops workers that were started with 'clx start-services'.

    Examples:
        clx stop-services
        clx stop-services --jobs-db-path=/data/clx_jobs.db
        clx stop-services --force
    """
    from clx.infrastructure.workers.config_loader import load_worker_config
    from clx.infrastructure.workers.lifecycle_manager import WorkerLifecycleManager
    from clx.infrastructure.workers.state_manager import WorkerStateManager

    jobs_db_path = Path(jobs_db_path).absolute()

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
        if state.db_path != str(jobs_db_path):
            click.echo(
                f"Warning: Database path mismatch:\n"
                f"  State file:  {state.db_path}\n"
                f"  You specified: {jobs_db_path}",
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
        db_path=jobs_db_path,
        workspace_path=jobs_db_path.parent,  # Doesn't matter for shutdown
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
    "--jobs-db-path",
    type=click.Path(),
    default="clx_jobs.db",
    help="Path to the job queue database",
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
def workers_list(jobs_db_path, format, status):
    """List registered workers.

    Examples:
        clx workers list
        clx workers list --status=idle
        clx workers list --format=json
        clx workers list --status=busy --status=hung
    """
    from clx.infrastructure.workers.discovery import WorkerDiscovery

    jobs_db_path = Path(jobs_db_path)

    if not jobs_db_path.exists():
        click.echo(f"Error: Job queue database not found: {jobs_db_path}", err=True)
        return 1

    # Discover workers
    discovery = WorkerDiscovery(jobs_db_path)
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
    "--jobs-db-path",
    type=click.Path(),
    default="clx_jobs.db",
    help="Path to the job queue database",
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
def workers_cleanup(jobs_db_path, force, cleanup_all):
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

    jobs_db_path = Path(jobs_db_path)

    if not jobs_db_path.exists():
        click.echo(f"Error: Job queue database not found: {jobs_db_path}", err=True)
        return 1

    # Discover workers to clean up
    discovery = WorkerDiscovery(jobs_db_path)

    if cleanup_all:
        workers = discovery.discover_workers()
        click.echo("Warning: Cleaning up ALL workers", err=True)
    else:
        # Only dead/hung workers or stale heartbeats
        workers = discovery.discover_workers(status_filter=["dead", "hung"])

        # Also include workers with very stale heartbeats
        all_workers = discovery.discover_workers(status_filter=["idle", "busy"])
        stale_workers = [
            w for w in all_workers if (datetime.now() - w.last_heartbeat).total_seconds() > 60
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
    job_queue = JobQueue(jobs_db_path)
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


@cli.command()
@click.option(
    "--jobs-db-path",
    type=click.Path(exists=False, path_type=Path),
    help="Path to the job queue database (auto-detected if not specified)",
)
@click.option(
    "--workers",
    "workers_only",
    is_flag=True,
    help="Show only worker information",
)
@click.option(
    "--jobs",
    "jobs_only",
    is_flag=True,
    help="Show only job queue information",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json", "compact"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.option(
    "--no-color",
    is_flag=True,
    help="Disable colored output",
)
def status(jobs_db_path, workers_only, jobs_only, output_format, no_color):
    """Show CLX system status.

    Displays worker availability, job queue status, and system health.

    Examples:

        clx status                      # Show full status
        clx status --workers            # Show only workers
        clx status --format=json        # JSON output
        clx status --jobs-db-path=/data/clx_jobs.db  # Custom database
    """
    from clx.cli.status.collector import StatusCollector
    from clx.cli.status.formatters import (
        CompactFormatter,
        JsonFormatter,
        TableFormatter,
    )

    # Create collector
    collector = StatusCollector(db_path=jobs_db_path)

    # Collect status
    try:
        status_info = collector.collect()
    except Exception as e:
        click.echo(f"Error collecting status: {e}", err=True)
        logger.error(f"Error collecting status: {e}", exc_info=True)
        return 2

    # Create formatter
    if output_format == "json":
        formatter = JsonFormatter(pretty=True)
    elif output_format == "compact":
        formatter = CompactFormatter()
    else:  # table
        formatter = TableFormatter(use_color=not no_color)

    # Format and display
    output = formatter.format(status_info, workers_only=workers_only, jobs_only=jobs_only)
    click.echo(output)

    # Exit with appropriate code
    exit_code = formatter.get_exit_code(status_info)
    raise SystemExit(exit_code)


@cli.command()
@click.option(
    "--jobs-db-path",
    type=click.Path(exists=False, path_type=Path),
    help="Path to the job queue database (auto-detected if not specified)",
)
@click.option(
    "--refresh",
    type=click.IntRange(1, 10),
    default=2,
    help="Refresh interval in seconds (1-10, default: 2)",
)
@click.option(
    "--log-file",
    type=click.Path(path_type=Path),
    help="Log errors to file",
)
def monitor(jobs_db_path, refresh, log_file):
    """Launch real-time monitoring TUI.

    Displays live worker status, job queue, and activity in an
    interactive terminal interface.

    Examples:

        clx monitor                         # Use default settings
        clx monitor --refresh=5             # Update every 5 seconds
        clx monitor --jobs-db-path=/data/clx_jobs.db  # Custom database
    """
    try:
        from clx.cli.monitor.app import CLXMonitorApp
    except ImportError as e:
        click.echo(
            "Error: TUI dependencies not installed. Install with: pip install clx[tui]",
            err=True,
        )
        logger.error(f"Failed to import TUI dependencies: {e}", exc_info=True)
        raise SystemExit(1) from e

    # Set up logging if requested
    if log_file:
        logging.basicConfig(
            filename=str(log_file),
            level=logging.ERROR,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )

    # Auto-detect database path if not specified
    if not jobs_db_path:
        from clx.cli.status.collector import StatusCollector

        collector = StatusCollector()
        jobs_db_path = collector.db_path

    if not jobs_db_path.exists():
        click.echo(f"Error: Job queue database not found: {jobs_db_path}", err=True)
        click.echo("Run 'clx build course.yaml' to initialize the system.", err=True)
        raise SystemExit(2)

    # Launch TUI app
    app = CLXMonitorApp(
        db_path=jobs_db_path,
        refresh_interval=refresh,
    )

    try:
        app.run()
    except Exception as e:
        click.echo(f"Error running monitor: {e}", err=True)
        if log_file:
            click.echo(f"See {log_file} for details", err=True)
        logger.error(f"Monitor error: {e}", exc_info=True)
        raise SystemExit(1) from e


@cli.command()
@click.option(
    "--host",
    default="127.0.0.1",
    help="Host to bind to (default: 127.0.0.1, use 0.0.0.0 for all interfaces)",
)
@click.option(
    "--port",
    type=int,
    default=8000,
    help="Port to bind to (default: 8000)",
)
@click.option(
    "--jobs-db-path",
    type=click.Path(exists=False, path_type=Path),
    help="Path to the job queue database (auto-detected if not specified)",
)
@click.option(
    "--no-browser",
    is_flag=True,
    help="Do not auto-open browser",
)
@click.option(
    "--reload",
    is_flag=True,
    help="Enable auto-reload for development",
)
@click.option(
    "--cors-origin",
    multiple=True,
    help="CORS allowed origins (can specify multiple times, default: *)",
)
def serve(host, port, jobs_db_path, no_browser, reload, cors_origin):
    """Start web dashboard server.

    Launches FastAPI server with REST API and WebSocket support for
    remote monitoring via web browser.

    Examples:

        clx serve                           # Start on localhost:8000
        clx serve --host=0.0.0.0 --port=8080  # Bind to all interfaces
        clx serve --jobs-db-path=/data/clx_jobs.db  # Custom database
    """
    try:
        import uvicorn

        from clx.web.app import create_app
    except ImportError as e:
        click.echo(
            "Error: Web dependencies not installed. Install with: pip install clx[web]",
            err=True,
        )
        logger.error(f"Failed to import web dependencies: {e}", exc_info=True)
        raise SystemExit(1) from e

    # Auto-detect database path if not specified
    if not jobs_db_path:
        from clx.cli.status.collector import StatusCollector

        collector = StatusCollector()
        jobs_db_path = collector.db_path

    if not jobs_db_path.exists():
        click.echo(f"Warning: Job queue database not found: {jobs_db_path}", err=True)
        click.echo("The server will start, but data will be unavailable.", err=True)
        click.echo("Run 'clx build course.yaml' to initialize the system.", err=True)

    # Create app
    cors_origins = list(cors_origin) if cors_origin else None
    app = create_app(
        db_path=jobs_db_path,
        host=host,
        port=port,
        cors_origins=cors_origins,
    )

    # Open browser
    if not no_browser:
        import webbrowser

        url = f"http://{host if host != '0.0.0.0' else 'localhost'}:{port}"
        click.echo(f"Opening browser to {url}...")
        webbrowser.open(url)

    # Run server
    click.echo(f"Starting server on {host}:{port}...")
    click.echo(f"API Documentation: http://{host}:{port}/docs")
    click.echo("Press CTRL+C to stop")

    try:
        uvicorn.run(
            app,
            host=host,
            port=port,
            reload=reload,
            log_level="info",
        )
    except Exception as e:
        click.echo(f"Error running server: {e}", err=True)
        logger.error(f"Server error: {e}", exc_info=True)
        raise SystemExit(1) from e


if __name__ == "__main__":
    cli()
