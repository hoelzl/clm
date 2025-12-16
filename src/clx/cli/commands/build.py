"""Build command and related functionality.

This module contains the main build command for processing courses,
along with the targets command for listing output targets.
"""

import asyncio
import shutil
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from time import time
from typing import Literal

import click
from attrs import evolve
from rich.console import Console

from clx.cli.build_reporter import BuildReporter

# Import shared logging setup
from clx.cli.commands.shared import LOG_LEVELS, cli_console, get_logger, setup_logging
from clx.cli.file_event_handler import FileEventHandler
from clx.cli.git_dir_mover import git_dir_mover
from clx.cli.output_formatter import (
    DefaultOutputFormatter,
    JSONOutputFormatter,
    OutputFormatter,
    QuietOutputFormatter,
    VerboseOutputFormatter,
)
from clx.core.course import Course
from clx.core.course_spec import CourseSpec, CourseSpecError
from clx.infrastructure.backends.sqlite_backend import SqliteBackend
from clx.infrastructure.database.db_operations import DatabaseManager
from clx.infrastructure.messaging.correlation_ids import all_correlation_ids
from clx.infrastructure.utils.path_utils import output_path_for

logger = get_logger(__name__)


@dataclass
class BuildConfig:
    """Configuration for course build process."""

    spec_file: Path
    data_dir: Path
    output_dir: Path
    log_level: str
    cache_db_path: Path
    jobs_db_path: Path
    ignore_cache: bool
    clear_cache: bool
    keep_directory: bool
    watch: bool
    print_correlation_ids: bool

    # Worker configuration
    workers: str | None
    notebook_workers: int | None
    plantuml_workers: int | None
    drawio_workers: int | None
    notebook_image: str | None

    # Watch mode configuration
    watch_mode: str = "fast"
    debounce: float = 0.3

    # Build output configuration
    output_mode: str = "default"
    no_progress: bool = False
    no_color: bool = False
    verbose_logging: bool = False

    # Output filtering
    language: str | None = None
    speaker_only: bool = False
    selected_targets: list[str] | None = None

    # Notebook execution mode
    force_execute: bool = False

    # Image storage mode
    image_mode: str = "duplicated"  # "duplicated" or "shared"


def create_output_formatter(config: BuildConfig) -> OutputFormatter:
    """Create appropriate output formatter based on configuration."""
    output_mode = config.output_mode.lower()

    if output_mode == "json":
        return JSONOutputFormatter()
    elif output_mode == "quiet":
        return QuietOutputFormatter()
    elif output_mode == "verbose":
        return VerboseOutputFormatter(
            show_progress=not config.no_progress,
            use_color=not config.no_color,
        )
    else:  # default
        return DefaultOutputFormatter(
            show_progress=not config.no_progress,
            use_color=not config.no_color,
        )


def report_validation_errors(
    validation_errors: list[str],
    spec_file: Path,
    output_mode: str,
    no_color: bool = False,
) -> None:
    """Report validation errors in the appropriate output format."""
    import json as json_module

    from clx.cli.build_data_classes import BuildError

    output_mode = output_mode.lower()

    # Convert validation errors to BuildError objects for consistent formatting
    build_errors = [
        BuildError(
            error_type="configuration",
            category="spec_validation",
            severity="error",
            file_path=str(spec_file),
            message=error,
            actionable_guidance="Fix the error in the course spec file and try again",
        )
        for error in validation_errors
    ]

    if output_mode == "json":
        output = {
            "status": "validation_failed",
            "spec_file": str(spec_file),
            "error_count": len(build_errors),
            "errors": [
                {
                    "error_type": e.error_type,
                    "category": e.category,
                    "severity": e.severity,
                    "message": e.message,
                    "file_path": e.file_path,
                    "actionable_guidance": e.actionable_guidance,
                }
                for e in build_errors
            ],
        }
        print(json_module.dumps(output, indent=2))
    elif output_mode == "quiet":
        cli_console.print(
            f"Spec validation failed with {len(validation_errors)} error(s): {spec_file}",
            style="red",
        )
    else:
        console = Console(force_terminal=not no_color, file=sys.stderr)
        console.print(f"\n[bold red]✗ Course spec validation failed[/bold red] ({spec_file})\n")
        console.print(f"Found {len(validation_errors)} validation error(s):\n")

        for i, error in enumerate(validation_errors, 1):
            console.print(f"  [red]{i}. {error}[/red]")

        console.print("\n[bold]Action:[/bold] Fix the errors in your spec file and try again.")

        if output_mode == "verbose":
            from clx.infrastructure.logging.log_paths import get_log_dir

            log_dir = get_log_dir()
            console.print(f"\n[dim]Full logs available in: {log_dir}[/dim]")


async def print_all_correlation_ids():
    """Print all correlation IDs using Rich console."""
    cli_console.rule("[cyan]Correlation IDs[/cyan]", characters="-")
    cli_console.print(f"Created {len(all_correlation_ids)} Correlation IDs")
    for cid, data in all_correlation_ids.items():
        cli_console.print(f"  {cid}: {data.format_dependencies()}")


def initialize_paths_and_course(config: BuildConfig) -> tuple[Course, list[Path], Path]:
    """Initialize paths, load course spec, and create course object."""
    spec_file = config.spec_file.absolute()
    setup_logging(config.log_level, console_logging=config.verbose_logging)

    # Set data_dir to parent of spec file if not provided
    data_dir = config.data_dir
    if data_dir is None:
        data_dir = spec_file.parents[1]
        logger.debug(f"Data directory set to {data_dir}")
        assert data_dir.exists(), f"Data directory {data_dir} does not exist."

    # Load course specification first to check for output targets
    try:
        spec = CourseSpec.from_file(spec_file)
    except CourseSpecError as e:
        logger.error(f"Failed to parse spec file: {e}")
        if config.output_mode.lower() == "json":
            import json

            error_output = {
                "status": "error",
                "error_type": "spec_parsing",
                "file": str(spec_file),
                "message": str(e),
            }
            print(json.dumps(error_output, indent=2))
            raise SystemExit(1) from None
        else:
            console = Console(file=sys.stderr, force_terminal=not config.no_color)
            console.print("\n[bold red]Spec File Error[/bold red]\n")
            console.print(str(e))
            raise SystemExit(1) from None

    # Validate spec
    validation_errors = spec.validate()
    if validation_errors:
        for error in validation_errors:
            logger.error(f"Spec validation error: {error}")
        report_validation_errors(
            validation_errors,
            spec_file,
            output_mode=config.output_mode,
            no_color=config.no_color,
        )
        if config.output_mode.lower() == "json":
            raise SystemExit(1)
        else:
            raise click.ClickException(
                f"Course spec validation failed with {len(validation_errors)} error(s)."
            )

    # Determine output_dir behavior
    output_dir = config.output_dir
    if output_dir is None and not spec.output_targets:
        output_dir = data_dir / "output"
        output_dir.mkdir(exist_ok=True)
        logger.debug(f"Output directory set to {output_dir}")

    if output_dir is not None:
        logger.info(f"Processing course from {spec_file.name} in {data_dir} to {output_dir}")
    elif spec.output_targets:
        target_names = [t.name for t in spec.output_targets]
        logger.info(
            f"Processing course from {spec_file.name} in {data_dir} with targets: {target_names}"
        )

    # Convert CLI options to filter parameters
    output_languages = [config.language] if config.language else None
    output_kinds = ["speaker"] if config.speaker_only else None

    if output_languages:
        logger.info(f"Generating output for language(s): {output_languages}")
    if output_kinds:
        logger.info(f"Generating output for kind(s): {output_kinds}")
    if config.selected_targets:
        logger.info(f"Building only targets: {config.selected_targets}")

    # Create course object
    course = Course.from_spec(
        spec,
        data_dir,
        output_dir,
        output_languages=output_languages,
        output_kinds=output_kinds,
        fallback_execute=config.force_execute,
        selected_targets=config.selected_targets,
        image_mode=config.image_mode,
    )

    # Calculate root directories for cleanup
    root_dirs = []
    languages = output_languages if output_languages else ["en", "de"]

    if course.output_targets:
        for target in course.output_targets:
            target_languages = (
                list(target.languages & set(languages)) if languages else list(target.languages)
            )
            for lang in target_languages:
                if target.kinds & {"code-along", "completed"}:
                    root_dirs.append(
                        output_path_for(
                            target.output_root,
                            False,
                            lang,
                            course.name,
                            skip_toplevel=target.is_explicit,
                        )
                    )
                if "speaker" in target.kinds:
                    root_dirs.append(
                        output_path_for(
                            target.output_root,
                            True,
                            lang,
                            course.name,
                            skip_toplevel=target.is_explicit,
                        )
                    )
    else:
        if config.speaker_only:
            is_speaker_options = [True]
        else:
            is_speaker_options = [True, False]

        for language in languages:
            for is_speaker in is_speaker_options:
                root_dirs.append(
                    output_path_for(course.output_root, is_speaker, language, course.name)
                )

    return course, root_dirs, data_dir


def configure_workers(config: BuildConfig):
    """Load worker configuration with CLI overrides."""
    from clx.infrastructure.workers.config_loader import load_worker_config

    cli_overrides: dict[str, str | int | bool] = {}

    if config.workers:
        cli_overrides["default_execution_mode"] = config.workers
    if config.notebook_workers is not None:
        cli_overrides["notebook_count"] = config.notebook_workers
    if config.plantuml_workers is not None:
        cli_overrides["plantuml_count"] = config.plantuml_workers
    if config.drawio_workers is not None:
        cli_overrides["drawio_count"] = config.drawio_workers
    if config.notebook_image is not None:
        cli_overrides["notebook_image"] = config.notebook_image

    return load_worker_config(cli_overrides)


def start_managed_workers(lifecycle_manager, worker_config) -> list:
    """Start managed workers if needed."""
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


def _report_duplicate_file_warnings(course: Course, build_reporter: BuildReporter) -> None:
    """Check for duplicate output files and report warnings."""
    from clx.cli.build_data_classes import BuildWarning

    try:
        duplicates = course.detect_duplicate_output_files()

        for dup in duplicates:
            source_files = dup["files"]
            source_paths = "\n  - ".join(str(p) for p in source_files)

            warning = BuildWarning(
                category="duplicate_output_file",
                message=(
                    f"Duplicate output file '{dup['output_name']}' "
                    f"(lang={dup['language']}, format={dup['format']}, kind={dup['kind']}). "
                    f"Multiple source files produce the same output:\n  - {source_paths}"
                ),
                severity="high",
                file_path=str(source_files[0]) if source_files else None,
            )
            build_reporter.report_warning(warning)

        if duplicates:
            logger.warning(
                f"Found {len(duplicates)} duplicate output file(s). "
                f"This may cause unpredictable compilation results."
            )

    except Exception as e:
        logger.warning(f"Could not check for duplicate output files: {e}")


def _report_image_collisions(course: Course, build_reporter: BuildReporter) -> bool:
    """Check for image filename collisions and report errors."""
    if course.image_mode == "duplicated":
        return False

    from clx.cli.build_data_classes import BuildError

    collisions = course.image_registry.collisions
    if not collisions:
        return False

    for collision in collisions:
        source_paths = "\n  - ".join(str(p) for p in collision.paths)

        error = BuildError(
            error_type="configuration",
            category="image_collision",
            severity="error",
            message=(
                f"Image path collision: 'img/{collision.relative_path}' exists at multiple "
                f"locations with different content:\n  - {source_paths}"
            ),
            file_path=str(collision.paths[0]) if collision.paths else "unknown",
            actionable_guidance=(
                "Rename one of the image files or move it to a different subfolder "
                "within img/ to give it a unique relative path"
            ),
        )
        build_reporter.report_error(error)

    logger.error(
        f"Found {len(collisions)} image filename collision(s). "
        f"Build cannot proceed with duplicate image filenames."
    )
    return True


def _report_loading_issues(course: Course, build_reporter: BuildReporter) -> None:
    """Report any errors or warnings encountered during course loading."""
    from clx.cli.build_data_classes import BuildError, BuildWarning

    for error in course.loading_errors:
        category = error.get("category", "loading_error")
        message = error.get("message", "Unknown loading error")
        details = error.get("details", {})

        error_type: Literal["user", "configuration", "infrastructure"]
        if category == "topic_not_found":
            error_type = "configuration"
            available = details.get("available_topics", [])
            if available:
                message += f"\n  Available topic IDs (first 10): {', '.join(available)}"
            guidance = (
                "Check that the topic ID in your course spec matches the directory name pattern"
            )
        elif category == "file_load_error":
            error_type = "user"
            guidance = "Check the file for encoding issues or syntax errors"
        else:
            error_type = "infrastructure"
            guidance = "Check logs for more details"

        file_path = details.get("file_path") or "unknown"

        build_error = BuildError(
            error_type=error_type,
            category=category,
            severity="error",
            message=message,
            file_path=file_path,
            actionable_guidance=guidance,
        )
        build_reporter.report_error(build_error)

    for warning in course.loading_warnings:
        category = warning.get("category", "loading_warning")
        message = warning.get("message", "Unknown loading warning")
        details = warning.get("details", {})

        if category == "duplicate_topic_id":
            first_path = details.get("first_path", "")
            dup_path = details.get("duplicate_path", "")
            if first_path and dup_path:
                message += f"\n  First: {first_path}\n  Duplicate: {dup_path}"

        build_warning = BuildWarning(
            category=category,
            message=message,
            severity="high",
            file_path=details.get("file_path") or details.get("duplicate_path"),
        )
        build_reporter.report_warning(build_warning)

    if course.loading_errors:
        logger.error(
            f"Found {len(course.loading_errors)} loading error(s). Some files may not be processed."
        )
    if course.loading_warnings:
        logger.warning(
            f"Found {len(course.loading_warnings)} loading warning(s). Check output for details."
        )


async def process_course_with_backend(
    course: Course,
    root_dirs: list[Path],
    backend,
    config: BuildConfig,
    start_time: float,
    build_reporter: BuildReporter,
):
    """Process course and optionally watch for changes."""
    from clx.core.utils.execution_utils import (
        NUM_EXECUTION_STAGES,
        execution_stages,
        get_stage_name,
    )

    with git_dir_mover(root_dirs, config.keep_directory):
        for root_dir in root_dirs:
            if not config.keep_directory:
                logger.info(f"Removing root directory {root_dir}")
                shutil.rmtree(root_dir, ignore_errors=True)
            else:
                logger.info(f"Not removing root directory {root_dir}")

        # Pre-create all output directories before processing starts.
        # This is necessary for Docker workers which may have bind mount
        # visibility issues when directories are created concurrently.
        course.precreate_output_directories()

        total_files = len(course.files)
        build_reporter.start_build(
            course_name=course.name.en,
            total_files=total_files,
            total_stages=NUM_EXECUTION_STAGES,
        )

        _report_duplicate_file_warnings(course, build_reporter)
        _report_loading_issues(course, build_reporter)

        if _report_image_collisions(course, build_reporter):
            build_reporter.finish_build()
            build_reporter.cleanup()
            raise SystemExit("Build failed: image filename collisions detected")

        try:
            for stage in execution_stages():
                num_jobs = await course.count_stage_operations(stage)

                if num_jobs > 0:
                    stage_name = get_stage_name(stage)
                    build_reporter.start_stage(stage_name, num_jobs)

                await course.process_stage(stage, backend)

            await course.process_dir_group(backend)

        finally:
            build_reporter.finish_build()
            build_reporter.cleanup()

        if config.print_correlation_ids:
            await print_all_correlation_ids()

    if config.watch:
        await watch_and_rebuild(course, backend, config)


async def watch_and_rebuild(course: Course, backend, config: BuildConfig):
    """Watch for file changes and automatically rebuild course."""
    from watchdog.observers import Observer

    if config.watch_mode == "fast":
        logger.info("Watch mode enabled with fast processing (notebooks only, no HTML)")
        for section in course.sections:
            for i, topic in enumerate(section.topics):
                section.topics[i] = evolve(topic, skip_html=True)
    else:
        logger.info("Watch mode enabled with normal processing (all formats)")

    logger.info(f"File change debounce delay: {config.debounce}s")
    loop = asyncio.get_running_loop()

    event_handler = FileEventHandler(
        course=course,
        backend=backend,
        data_dir=config.data_dir,
        loop=loop,
        debounce_delay=config.debounce,
        patterns=["*"],
    )

    observer = Observer()
    observer.schedule(event_handler, str(config.data_dir), recursive=True)
    observer.start()
    logger.debug("Started observer")

    shut_down = False

    def shutdown_handler(sig, frame):
        nonlocal shut_down
        shut_down = True

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


async def main_build(
    ctx,
    spec_file,
    data_dir,
    output_dir,
    watch,
    watch_mode,
    debounce,
    print_correlation_ids,
    log_level,
    cache_db_path,
    jobs_db_path,
    ignore_cache,
    clear_cache,
    keep_directory,
    workers,
    notebook_workers,
    plantuml_workers,
    drawio_workers,
    notebook_image,
    output_mode,
    no_progress,
    no_color,
    verbose_logging,
    language,
    speaker_only,
    targets,
    force_execute,
    image_mode,
):
    """Main orchestration function for course building."""
    start_time = time()

    selected_targets = [t.strip() for t in targets.split(",") if t.strip()] if targets else None

    config = BuildConfig(
        spec_file=spec_file,
        data_dir=data_dir,
        output_dir=output_dir,
        log_level=log_level,
        cache_db_path=cache_db_path,
        jobs_db_path=jobs_db_path,
        ignore_cache=ignore_cache,
        clear_cache=clear_cache,
        keep_directory=keep_directory,
        watch=watch,
        watch_mode=watch_mode,
        debounce=debounce,
        print_correlation_ids=print_correlation_ids,
        workers=workers,
        notebook_workers=notebook_workers,
        plantuml_workers=plantuml_workers,
        drawio_workers=drawio_workers,
        notebook_image=notebook_image,
        output_mode=output_mode,
        no_progress=no_progress,
        no_color=no_color,
        verbose_logging=verbose_logging,
        language=language,
        speaker_only=speaker_only,
        selected_targets=selected_targets,
        force_execute=force_execute,
        image_mode=image_mode,
    )

    course, root_dirs, data_dir = initialize_paths_and_course(config)

    output_formatter = create_output_formatter(config)
    build_reporter = BuildReporter(output_formatter)

    worker_config = configure_workers(config)

    from clx.infrastructure.database.schema import init_database
    from clx.infrastructure.workers.lifecycle_manager import WorkerLifecycleManager

    logger.debug(f"Initializing job queue database: {config.jobs_db_path}")
    init_database(config.jobs_db_path)

    lifecycle_manager = WorkerLifecycleManager(
        config=worker_config,
        db_path=config.jobs_db_path,
        workspace_path=course.output_root,
        cache_db_path=config.cache_db_path,
        data_dir=data_dir,
    )

    started_workers = start_managed_workers(lifecycle_manager, worker_config)

    try:
        with DatabaseManager(config.cache_db_path, force_init=config.clear_cache) as db_manager:
            backend = SqliteBackend(
                db_path=config.jobs_db_path,
                workspace_path=course.output_root,
                db_manager=db_manager,
                ignore_db=config.ignore_cache,
                build_reporter=build_reporter,
            )

            async with backend:
                await process_course_with_backend(
                    course=course,
                    root_dirs=root_dirs,
                    backend=backend,
                    config=config,
                    start_time=start_time,
                    build_reporter=build_reporter,
                )
    except KeyboardInterrupt:
        logger.info("Build interrupted, cleaning up...")
        raise
    finally:
        if started_workers and worker_config.auto_stop:
            logger.info("Stopping managed workers...")
            try:
                lifecycle_manager.stop_managed_workers(started_workers)
                logger.info(f"Stopped {len(started_workers)} worker(s)")
            except Exception as e:
                logger.error(f"Failed to stop workers: {e}", exc_info=True)


@click.command()
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
    "--watch-mode",
    type=click.Choice(["fast", "normal"], case_sensitive=False),
    default="fast",
    help="Watch mode processing speed: fast (notebooks only, no HTML) or normal (all formats).",
)
@click.option(
    "--debounce",
    type=float,
    default=0.3,
    help="Debounce delay for file changes in watch mode (seconds).",
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
    "--ignore-cache",
    is_flag=True,
    help="Ignore cached results and reprocess all files (still updates cache).",
)
@click.option(
    "--clear-cache",
    is_flag=True,
    help="Clear the result cache before building, forcing all files to be reprocessed.",
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
    "--notebook-image",
    type=str,
    help="Docker image for notebook workers. Can be full image name or just a tag (e.g., 'lite', 'full'). Default is :latest which uses the lite variant. Only used with --workers=docker.",
)
@click.option(
    "--output-mode",
    "-O",
    type=click.Choice(["default", "verbose", "quiet", "json"], case_sensitive=False),
    default="default",
    help="Output mode for build progress reporting.",
)
@click.option(
    "--no-progress",
    is_flag=True,
    help="Disable progress bar display.",
)
@click.option(
    "--no-color",
    is_flag=True,
    help="Disable colored output.",
)
@click.option(
    "--verbose-logging",
    is_flag=True,
    help="Show log messages in console (by default logs go to file only).",
)
@click.option(
    "--language",
    "-L",
    type=click.Choice(["de", "en"], case_sensitive=False),
    help="Generate output for only one language (default: both de and en).",
)
@click.option(
    "--speaker-only",
    is_flag=True,
    help="Generate only speaker notes (skip public outputs like code-along and completed).",
)
@click.option(
    "--targets",
    "-T",
    type=str,
    help="Comma-separated list of output target names to build (from spec file).",
)
@click.option(
    "--force-execute",
    is_flag=True,
    help="Execute notebooks for each output format instead of reusing a cached execution.",
)
@click.option(
    "--image-mode",
    type=click.Choice(["duplicated", "shared"], case_sensitive=False),
    default="duplicated",
    help="Image storage: 'duplicated' (default) copies to each output variant, 'shared' stores once centrally.",
)
@click.pass_context
def build(
    ctx,
    spec_file,
    data_dir,
    output_dir,
    watch,
    watch_mode,
    debounce,
    print_correlation_ids,
    log_level,
    ignore_cache,
    clear_cache,
    keep_directory,
    workers,
    notebook_workers,
    plantuml_workers,
    drawio_workers,
    notebook_image,
    output_mode,
    no_progress,
    no_color,
    verbose_logging,
    language,
    speaker_only,
    targets,
    force_execute,
    image_mode,
):
    """Build a course from a spec file."""
    cache_db_path = ctx.obj["CACHE_DB_PATH"]
    jobs_db_path = ctx.obj["JOBS_DB_PATH"]

    shutdown_requested = False

    def shutdown_handler(signum, frame):
        nonlocal shutdown_requested

        if shutdown_requested:
            sys.exit(1)

        shutdown_requested = True
        raise KeyboardInterrupt(f"Shutdown signal {signum} received")

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    asyncio.run(
        main_build(
            ctx,
            spec_file,
            data_dir,
            output_dir,
            watch,
            watch_mode,
            debounce,
            print_correlation_ids,
            log_level,
            cache_db_path,
            jobs_db_path,
            ignore_cache,
            clear_cache,
            keep_directory,
            workers,
            notebook_workers,
            plantuml_workers,
            drawio_workers,
            notebook_image,
            output_mode,
            no_progress,
            no_color,
            verbose_logging,
            language,
            speaker_only,
            targets,
            force_execute,
            image_mode,
        )
    )


@click.command(name="targets")
@click.argument(
    "spec-file",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
def list_targets(spec_file, output_format):
    """List output targets defined in a course spec file.

    Shows all output targets with their paths, kinds, formats, and languages.

    Examples:
        clx targets course.xml
        clx targets course.xml --format=json
    """
    try:
        spec = CourseSpec.from_file(spec_file)
    except CourseSpecError as e:
        if output_format == "json":
            import json

            error_output = {
                "status": "error",
                "error_type": "spec_parsing",
                "file": str(spec_file),
                "message": str(e),
            }
            print(json.dumps(error_output, indent=2))
            raise SystemExit(1) from None
        else:
            click.echo(f"Error: {e}", err=True)
            raise SystemExit(1) from None

    if not spec.output_targets:
        click.echo("No output targets defined in spec file.")
        click.echo("Using default behavior (all outputs to --output-dir).")
        return 0

    if output_format == "json":
        import json

        data = [
            {
                "name": t.name,
                "path": t.path,
                "kinds": t.kinds or ["all"],
                "formats": t.formats or ["all"],
                "languages": t.languages or ["all"],
            }
            for t in spec.output_targets
        ]
        click.echo(json.dumps(data, indent=2))
    else:
        click.echo("Output Targets:")
        click.echo("=" * 80)
        click.echo("")

        for target in spec.output_targets:
            kinds_str = ", ".join(target.kinds) if target.kinds else "all"
            formats_str = ", ".join(target.formats) if target.formats else "all"
            languages_str = ", ".join(target.languages) if target.languages else "all"

            click.echo(f"  {target.name}")
            click.echo(f"    Path:      {target.path}")
            click.echo(f"    Kinds:     [{kinds_str}]")
            click.echo(f"    Formats:   [{formats_str}]")
            click.echo(f"    Languages: [{languages_str}]")
            click.echo("")

    return 0
