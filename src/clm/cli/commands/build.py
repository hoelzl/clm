"""The ``clm build`` command: a thin Click adapter over the build engine.

The orchestration itself lives in :mod:`clm.build` (Phase 8 A4, #802) —
``BuildConfig`` in, ``BuildSummary`` out, callable without Click. This
module keeps what is genuinely CLI: option parsing and env-default
resolution (converting :class:`clm.build.errors.BuildOptionError` to
``click.UsageError``), ``.env`` loading, signal handling, logging setup,
the watchdog-based watch runner, exit-code policy, and the
``--snapshot`` / ``--verify-against`` wiring.
"""

import asyncio
import importlib.util
import signal
import sys
from pathlib import Path

import click
from attrs import evolve

from clm.build import (
    VALID_HTTP_REPLAY_MODES,
    BuildConfig,
    BuildOptionError,
    SpecValidationFailure,
    UnownedOutputRootError,
    resolve_explain_rebuilds,
    resolve_fail_on_error,
    resolve_fail_on_missing_xref,
    resolve_http_replay_mode,
    resolve_http_replay_transport,
    resolve_log_level,
    resolve_write_provenance_manifest,
    run_build,
)
from clm.build.engine import format_exit_failure
from clm.cli.commands.shared import LOG_LEVELS, get_logger, setup_logging
from clm.core.build_data_classes import BuildSummary
from clm.core.course import Course
from clm.core.course_paths import resolve_course_paths
from clm.core.course_spec import CourseSpec
from clm.infrastructure.workers.worker_executor import WholeVolumeMountError

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# CLI adapters over the engine's option resolvers: same resolution logic
# (flag > CLM_* env var > default), with invalid values surfaced as Click
# usage errors instead of the engine's typed BuildOptionError.
# ---------------------------------------------------------------------------


def _resolve_http_replay_mode(cli_value: str | None) -> str:
    """CLI adapter over :func:`clm.build.resolve_http_replay_mode`."""
    try:
        return resolve_http_replay_mode(cli_value)
    except BuildOptionError as e:
        raise click.UsageError(str(e)) from None


def _resolve_http_replay_transport() -> str:
    """CLI adapter over :func:`clm.build.resolve_http_replay_transport`."""
    try:
        return resolve_http_replay_transport()
    except BuildOptionError as e:
        raise click.UsageError(str(e)) from None


def _resolve_fail_on_error(cli_value: bool | None, resolved_http_replay_mode: str) -> bool:
    """CLI adapter over :func:`clm.build.resolve_fail_on_error`."""
    try:
        return resolve_fail_on_error(cli_value, resolved_http_replay_mode)
    except BuildOptionError as e:
        raise click.UsageError(str(e)) from None


def _resolve_fail_on_missing_xref(cli_value: bool | None, resolved_http_replay_mode: str) -> bool:
    """CLI adapter over :func:`clm.build.resolve_fail_on_missing_xref`."""
    try:
        return resolve_fail_on_missing_xref(cli_value, resolved_http_replay_mode)
    except BuildOptionError as e:
        raise click.UsageError(str(e)) from None


def _resolve_explain_rebuilds(cli_flag: bool) -> bool:
    """CLI adapter over :func:`clm.build.resolve_explain_rebuilds`."""
    try:
        return resolve_explain_rebuilds(cli_flag)
    except BuildOptionError as e:
        raise click.UsageError(str(e)) from None


def _resolve_log_level(cli_log_level: str | None) -> str:
    """CLI alias for :func:`clm.build.resolve_log_level` (cannot fail)."""
    return resolve_log_level(cli_log_level)


def _resolve_write_provenance_manifest(
    *, requested: bool, is_snapshot: bool, verify_against_dir: Path | None
) -> bool:
    """CLI alias for :func:`clm.build.resolve_write_provenance_manifest`."""
    return resolve_write_provenance_manifest(
        requested=requested,
        is_snapshot=is_snapshot,
        verify_against_dir=verify_against_dir,
    )


def _find_env_file(start_dir: Path) -> Path | None:
    """Walk up from start_dir looking for a .env file.

    Returns the path to the first .env file found, or None. Thin wrapper over
    the shared :func:`clm.cli.env_loading.find_env_file` (kept under this name
    for the build command's existing callers and tests).
    """
    from clm.cli.env_loading import find_env_file

    return find_env_file(start_dir)


async def watch_and_rebuild(course: Course, backend, config: BuildConfig):
    """Watch for file changes and automatically rebuild course."""
    # Lazy: watchdog comes from the [watch] extra (#802 A12); main_build
    # pre-flights its availability so --watch fails with the pip hint
    # before any build work starts.
    from watchdog.observers import Observer

    from clm.cli.file_event_handler import FileEventHandler

    if config.watch_mode == "fast":
        logger.info("Watch mode enabled with fast processing (notebooks only, no HTML)")
        for section in course.sections:
            for i, topic in enumerate(section.topics):
                section.topics[i] = evolve(topic, skip_html=True)
    else:
        logger.info("Watch mode enabled with normal processing (all formats)")

    logger.info(f"File change debounce delay: {config.debounce}s")
    loop = asyncio.get_running_loop()

    # In --only-sections watch mode, compute the set of source directories
    # that belong to the selected sections. New-file events outside this
    # set are ignored by `FileEventHandler`. Modification events already
    # filter themselves via `course.find_course_file` against the
    # already-filtered `course.files`, so no extra work is needed there.
    selected_section_source_dirs: set[Path] | None = None
    if config.resolved_section_selection is not None:
        selected_section_source_dirs = set()
        for section in course.sections:
            for topic in section.topics:
                selected_section_source_dirs.add(Path(topic.path))
        logger.info(
            f"--only-sections: watch mode will react only to events under "
            f"{len(selected_section_source_dirs)} source "
            f"directories (sections: "
            f"{[s.name.en for s in course.sections]})."
        )

    event_handler = FileEventHandler(
        course=course,
        backend=backend,
        data_dir=config.data_dir,
        loop=loop,
        debounce_delay=config.debounce,
        patterns=["*"],
        selected_section_source_dirs=selected_section_source_dirs,
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
    clean,
    incremental,
    no_sweep,
    only_sections,
    workers,
    notebook_workers,
    plantuml_workers,
    drawio_workers,
    max_workers,
    notebook_image,
    plantuml_image,
    drawio_image,
    output_mode,
    no_progress,
    no_color,
    verbose_logging,
    language,
    speaker_only,
    targets,
    force_execute,
    http_replay,
    image_mode,
    image_format,
    inline_images,
    fail_on_missing_xref=False,
    provenance_manifest=True,
    telemetry_db_path: Path | None = None,
    no_html: bool = False,
    no_diagrams: bool = False,
    explain_rebuilds: bool = False,
    allow_unowned_output: bool = False,
) -> BuildSummary | None:
    """Adapt the ``clm build`` invocation onto the engine's ``run_build``.

    Parses the raw selector options, resolves the env-var-backed defaults,
    constructs the :class:`BuildConfig`, sets up logging, and delegates to
    :func:`clm.build.run_build` with the CLI's watch runner. Kept with its
    historical signature (including the unused ``ctx``) because the Click
    command passes every option positionally, and tests drive it directly.

    Returns the :class:`BuildSummary` from the build pipeline so the
    Click entry point can apply exit-code policy based on
    ``summary.errors`` (issue #90). Returns ``None`` in watch mode.
    """
    selected_targets = [t.strip() for t in targets.split(",") if t.strip()] if targets else None

    # Parse --only-sections tokens. The option is repeatable (issue #616:
    # repeated flags used to silently keep only the last occurrence), so
    # `only_sections` is a tuple of raw values whose comma-separated tokens
    # all accumulate. An empty value after stripping is an error, not a
    # silent fallthrough to full build. Resolution happens in
    # initialize_paths_and_course once the spec has been loaded.
    selected_sections: list[str] | None = None
    if only_sections:
        tokens = [t.strip() for value in only_sections for t in value.split(",")]
        if not any(tokens) or not all(tokens):
            raise click.UsageError(
                "--only-sections received an empty or whitespace-only value. "
                "Pass at least one selector token, e.g. --only-sections w03."
            )
        selected_sections = tokens

    # Resolve effective HTTP replay mode: CLI > env var > CI-aware default.
    # ``run_build`` re-resolves harmlessly (the resolver returns a non-None
    # argument unchanged) and pins the mode + transport env for worker
    # subprocesses; an invalid CLM_HTTP_REPLAY_MODE still fails here, at the
    # entry point, as a usage error.
    resolved_http_replay_mode = _resolve_http_replay_mode(http_replay)

    # Sweep is on by default. ``--no-sweep`` opts out; ``--incremental``
    # implies ``--no-sweep`` because incremental users explicitly trust
    # the on-disk state and a sweep would delete files the cache replay
    # decided not to re-emit.
    effective_sweep = (not no_sweep) and (not incremental)

    config = BuildConfig(
        spec_file=spec_file,
        data_dir=data_dir,
        output_dir=output_dir,
        log_level=log_level,
        cache_db_path=cache_db_path,
        jobs_db_path=jobs_db_path,
        ignore_cache=ignore_cache,
        clear_cache=clear_cache,
        watch=watch,
        watch_mode=watch_mode,
        debounce=debounce,
        print_correlation_ids=print_correlation_ids,
        workers=workers,
        notebook_workers=notebook_workers,
        plantuml_workers=plantuml_workers,
        drawio_workers=drawio_workers,
        max_workers=max_workers,
        notebook_image=notebook_image,
        plantuml_image=plantuml_image,
        drawio_image=drawio_image,
        output_mode=output_mode,
        no_progress=no_progress,
        no_color=no_color,
        verbose_logging=verbose_logging,
        language=language,
        speaker_only=speaker_only,
        selected_targets=selected_targets,
        no_html=no_html,
        no_diagrams=no_diagrams,
        force_execute=force_execute,
        http_replay_mode=resolved_http_replay_mode,
        image_mode=image_mode,
        image_format=image_format,
        inline_images=inline_images,
        incremental=incremental,
        clean=clean,
        allow_unowned_output=allow_unowned_output,
        sweep=effective_sweep,
        selected_sections=selected_sections,
        fail_on_missing_xref=fail_on_missing_xref,
        write_provenance_manifest=provenance_manifest,
        telemetry_db_path=telemetry_db_path,
        explain_rebuilds=explain_rebuilds,
    )

    # Fail fast, before any build work, when --watch is requested without
    # watchdog installed (it left the core install in #802 A12).
    if config.watch and importlib.util.find_spec("watchdog") is None:
        raise click.UsageError(
            "--watch requires the [watch] extra: "
            'pip install "coding-academy-lecture-manager[watch]"'
        )

    # Logging is the CLI's concern — the engine never configures logging
    # (programmatic callers bring their own configuration).
    setup_logging(_resolve_log_level(config.log_level), console_logging=config.verbose_logging)

    try:
        return await run_build(config, watch_runner=watch_and_rebuild)
    except SpecValidationFailure as e:
        raise click.ClickException(str(e)) from None
    except UnownedOutputRootError as e:
        # The refusal message names the directory and the remedy; a raw
        # traceback would bury both (finding S11, #798).
        raise click.ClickException(str(e)) from None
    except WholeVolumeMountError as e:
        # Same reasoning for the mount-root refusal (finding D7, #798):
        # the whole point of raising it instead of logging and coming up
        # with zero workers is that the user sees which directory is wrong.
        raise click.ClickException(str(e)) from None
    except BuildOptionError as e:
        raise click.UsageError(str(e)) from None


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
    help=(
        "Override where build output is written. For specs with "
        "<output-targets>, each target is re-rooted to "
        "<DIR>/<target.name>/ (matching the snapshot/verify layout). "
        "For specs without output-targets, DIR becomes a single "
        "collapsed output tree."
    ),
)
@click.option(
    "--snapshot",
    "snapshot_dir",
    type=click.Path(exists=False, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help=(
        "Capture build output to DIR as a verification baseline. "
        "Identical layout to --output-dir DIR (each spec output-target "
        "re-rooted to <DIR>/<target.name>/) plus three safety guards: "
        "DIR must not exist or be empty, mutually exclusive with "
        "--output-dir and --verify-against, and prints a confirmation "
        "line after the build."
    ),
)
@click.option(
    "--verify-against",
    "verify_against_dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help=(
        "After build, compare the output tree against the snapshot at "
        "DIR. Exits non-zero on any diff. By default .html files are "
        "skipped because they include live-kernel execution output; "
        "see --include-html and --strict."
    ),
)
@click.option(
    "--include-html",
    is_flag=True,
    help=(
        "With --verify-against: include .html in the comparison, with "
        "hex memory addresses normalized to a sentinel. Has no effect "
        "without --verify-against."
    ),
)
@click.option(
    "--strict-verify",
    is_flag=True,
    help=(
        "With --verify-against: byte-compare every file with no "
        "normalization and no skipping. Implies --include-html. Has no "
        "effect without --verify-against."
    ),
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
    default=None,
    help=(
        "Set the logging level. Overrides [logging] log_level / "
        "CLM_LOGGING__LOG_LEVEL; defaults to INFO when unset."
    ),
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
    "--clean",
    is_flag=True,
    help=(
        "Wipe each output root and regenerate from scratch (legacy "
        "behavior). Nested .git/ directories are preserved across the "
        "wipe. The default build flow no longer wipes — it relies on "
        "hash-aware writes plus a post-build sweep. Use --clean for "
        "emergency recovery from a corrupted output tree."
    ),
)
@click.option(
    "--allow-unowned-output",
    is_flag=True,
    help=(
        "Allow the destructive output operations (--clean's wipe and the "
        "post-build stray-file sweep) to run in an output root clm cannot "
        "prove it owns — one that already held files at build start and "
        "carries no .clm-manifest.json from an earlier build. Without this "
        "flag such a root is refused and nothing under it is deleted."
    ),
)
@click.option(
    "--incremental",
    is_flag=True,
    help="Incremental build: only write newly processed files (skip cached ones). Implies --no-sweep.",
)
@click.option(
    "--no-sweep",
    is_flag=True,
    help=(
        "Disable the post-build stray-file sweep. The sweep removes files "
        "under each output root that the build did not write (e.g. orphans "
        "from a renamed section). Useful when iterating on a single "
        "section and you do not want orphans from other sections deleted."
    ),
)
@click.option(
    "--only-sections",
    type=str,
    multiple=True,
    help=(
        "Comma-separated selector tokens; rebuilds only those sections "
        "and leaves unselected section output directories untouched. "
        "May be given multiple times; all occurrences accumulate. "
        "Bare tokens try id → 1-based index → case-insensitive substring "
        "match on either the German or English name. Use 'id:', 'idx:', "
        "or 'name:' prefixes to force a specific strategy. "
        "Dir-group processing is skipped in this mode."
    ),
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
    "--max-workers",
    type=int,
    help=(
        "Hard cap on effective worker count per type. Applied on top of "
        "automatic CPU/RAM-derived caps from "
        "clm.infrastructure.workers.pool_size_cap. Also settable via the "
        "CLM_MAX_WORKERS environment variable. Use to keep an oversized "
        "spec file (e.g. an 18-worker course override) from saturating a "
        "small dev laptop."
    ),
)
@click.option(
    "--notebook-image",
    type=str,
    help="Docker image for notebook workers. Can be full image name or just a tag (e.g., 'lite', 'full'). Default is :latest which uses the lite variant. Only used with --workers=docker. Cache keys follow the image (issue #744); lingering reused workers do not — stop them when switching images.",
)
@click.option(
    "--plantuml-image",
    type=str,
    help="Docker image for PlantUML workers. Full image name or just a tag "
    "(expands to docker.io/mhoelzl/clm-plantuml-converter:<tag>). Only used "
    "with --workers=docker (issue #690). Cache keys follow the image "
    "(issue #744); lingering reused workers do not — stop them when "
    "switching images.",
)
@click.option(
    "--drawio-image",
    type=str,
    help="Docker image for Draw.io workers. Full image name or just a tag "
    "(expands to docker.io/mhoelzl/clm-drawio-converter:<tag>). Only used "
    "with --workers=docker (issue #690). Cache keys follow the image "
    "(issue #744); lingering reused workers do not — stop them when "
    "switching images.",
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
    "--no-html",
    is_flag=True,
    help=(
        "Skip HTML generation for every topic (as if each carried "
        'html="no" in the spec). HTML is the only output format whose '
        "generation executes notebooks, so a --no-html build needs no "
        "Jupyter kernel — intended for the code-export compile CI and "
        "other kernel-free environments."
    ),
)
@click.option(
    "--no-diagrams",
    is_flag=True,
    help=(
        "Skip DrawIO and PlantUML processing entirely: diagram sources "
        "are excluded from the build, so no conversion jobs are "
        "scheduled and no plantuml/drawio workers are started. Rendered "
        "images committed next to the sources (slides/**/img/) still "
        "ship as ordinary image files. Intended for machines without "
        "the diagram binaries, e.g. the code-export compile CI."
    ),
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
    "--http-replay",
    type=click.Choice(list(VALID_HTTP_REPLAY_MODES), case_sensitive=False),
    default=None,
    help=(
        "HTTP replay record mode for topics with http-replay='yes' in the "
        "spec. 'replay' requires a cassette (strict, CI default); 'once' "
        "records on first run, replays thereafter (strict on new requests); "
        "'new-episodes' replays recorded requests and records any new ones "
        "into the existing cassette (local default); 'refresh' re-records "
        "every run; 'disabled' bypasses replay. Defaults to 'replay' when "
        "CI=true, else 'new-episodes'. Also settable via "
        "CLM_HTTP_REPLAY_MODE."
    ),
)
@click.option(
    "--fail-on-error/--no-fail-on-error",
    default=None,
    help=(
        "Exit with non-zero status if any cell or notebook error is "
        "reported during the build. Defaults to on under "
        "--http-replay=replay (the CI-strict default) and off under all "
        "other replay modes. Override via "
        "CLM_FAIL_ON_ERROR={1,true,yes,0,false,no}."
    ),
)
@click.option(
    "--fail-on-missing-xref/--no-fail-on-missing-xref",
    default=None,
    help=(
        "Exit with non-zero status if a 'clm:' cross-reference points at a "
        "topic not included in the build (issue #17). Defaults to on under "
        "--http-replay=replay (the CI-strict default) and off under all other "
        "replay modes — locally, a missing target is a warning and the link is "
        "dropped (text kept). Override via "
        "CLM_FAIL_ON_MISSING_XREF={1,true,yes,0,false,no}."
    ),
)
@click.option(
    "--explain-rebuilds",
    is_flag=True,
    default=False,
    help=(
        "Log why each deck missed the build cache and is being rebuilt "
        "(no cache entry / content hash changed / new output target). Off "
        "by default so a normal build pays nothing; reasons go to the log "
        "file, and to the console under --output-mode verbose. Also "
        "settable via CLM_EXPLAIN_REBUILDS={1,true,yes,0,false,no}."
    ),
)
@click.option(
    "--image-mode",
    type=click.Choice(["duplicated", "shared"], case_sensitive=False),
    default="duplicated",
    help="Image storage: 'duplicated' (default) copies to each output variant, 'shared' stores once centrally.",
)
@click.option(
    "--image-format",
    type=click.Choice(["png", "svg"], case_sensitive=False),
    default="png",
    help="Image output format for DrawIO/PlantUML: 'png' (default) or 'svg'.",
)
@click.option(
    "--inline-images",
    is_flag=True,
    help="Embed images as base64 data URLs in notebook output.",
)
@click.option(
    "--env-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to .env file to load before building. By default, loads .env from spec file directory if present.",
)
@click.option(
    "--no-env-file",
    is_flag=True,
    help="Disable automatic .env file loading.",
)
@click.option(
    "--provenance-manifest/--no-provenance-manifest",
    default=True,
    help=(
        "Write a .clm-manifest.json provenance index into each output root "
        "after a successful build, mapping every output file to its source "
        "commit and owning section/topic (issue #208). On by default; "
        "`clm git` excludes it from distributed repos. Pass "
        "--no-provenance-manifest to skip it. Always suppressed under "
        "--snapshot / --verify-against (it embeds a timestamp + commit)."
    ),
)
@click.pass_context
def build(
    ctx,
    spec_file,
    data_dir,
    output_dir,
    snapshot_dir,
    verify_against_dir,
    include_html,
    strict_verify,
    watch,
    watch_mode,
    debounce,
    print_correlation_ids,
    log_level,
    ignore_cache,
    clear_cache,
    clean,
    allow_unowned_output,
    incremental,
    no_sweep,
    only_sections,
    workers,
    notebook_workers,
    plantuml_workers,
    drawio_workers,
    max_workers,
    notebook_image,
    plantuml_image,
    drawio_image,
    output_mode,
    no_progress,
    no_color,
    verbose_logging,
    language,
    speaker_only,
    no_html,
    no_diagrams,
    targets,
    force_execute,
    http_replay,
    fail_on_error,
    fail_on_missing_xref,
    explain_rebuilds,
    image_mode,
    image_format,
    inline_images,
    env_file,
    no_env_file,
    provenance_manifest,
):
    """Build a course from a spec file."""
    # ------------------------------------------------------------------
    # Snapshot / verify wiring (Phase 1 of slide-format-redesign track).
    # --snapshot DIR and --verify-against DIR can both be combined with
    # the normal build, but --snapshot is mutually exclusive with
    # --output-dir (it is an explicit output-dir override) and with
    # --verify-against (different intents).
    # ------------------------------------------------------------------
    is_snapshot = snapshot_dir is not None
    if is_snapshot:
        if output_dir is not None:
            raise click.UsageError(
                "--snapshot and --output-dir are mutually exclusive; "
                "--snapshot already specifies where build output goes."
            )
        if verify_against_dir is not None:
            raise click.UsageError(
                "--snapshot and --verify-against are mutually exclusive; "
                "snapshot captures a baseline, verify compares against one."
            )
        if snapshot_dir.exists() and any(snapshot_dir.iterdir()):
            raise click.UsageError(
                f"--snapshot target is not empty: {snapshot_dir}. "
                "Pick a fresh path or remove the existing contents."
            )
        # ``--snapshot`` and ``--output-dir`` now share the same
        # downstream plumbing (both re-root the spec's
        # ``<output-targets>`` under ``<DIR>/<target.name>/``). The only
        # CLI-level differences are the safety guards above (empty-dir
        # check, mutex with ``--verify-against``) and the post-build
        # confirmation print.
        output_dir = snapshot_dir

    if (include_html or strict_verify) and verify_against_dir is None:
        # Surface the no-op rather than silently ignoring it.
        raise click.UsageError(
            "--include-html / --strict-verify have no effect without --verify-against."
        )
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

    # Load .env file into os.environ before spawning workers.
    # Workers inherit os.environ, so this makes .env variables available
    # to worker subprocesses and notebook kernels.
    if not no_env_file:
        from dotenv import load_dotenv

        if env_file is not None:
            loaded = load_dotenv(env_file, override=False)
            if loaded:
                # Emitted at DEBUG, not INFO: this runs before ``setup_logging``
                # has replaced the bootstrap ``basicConfig`` console handler
                # (installed in ``cli/main.py``) with the real, file-routed
                # handler set. An INFO call here leaks to the terminal in the
                # bootstrap format even when console logging is off.
                logger.debug(f"Loaded environment from {env_file}")
            else:
                logger.warning(f"Could not load environment from {env_file}")
        else:
            # Auto-detect .env by walking up from the spec file's directory.
            # The spec file is often in a subdirectory (e.g., course-specs/)
            # while .env sits at the project root.
            dotenv_path = _find_env_file(spec_file.resolve().parent)
            if dotenv_path:
                load_dotenv(dotenv_path, override=False)
                logger.debug(f"Loaded environment from {dotenv_path}")

    # Resolve the effective HTTP replay mode once at the entry point so
    # the exit-policy resolver below can see it without re-implementing
    # the precedence logic. ``main_build`` re-resolves harmlessly (the
    # resolver returns its CLI argument unchanged when not ``None``).
    resolved_http_replay_mode = _resolve_http_replay_mode(http_replay)
    resolved_fail_on_missing_xref = _resolve_fail_on_missing_xref(
        fail_on_missing_xref, resolved_http_replay_mode
    )
    resolved_explain_rebuilds = _resolve_explain_rebuilds(explain_rebuilds)

    effective_provenance_manifest = _resolve_write_provenance_manifest(
        requested=provenance_manifest,
        is_snapshot=is_snapshot,
        verify_against_dir=verify_against_dir,
    )

    summary = asyncio.run(
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
            clean,
            incremental,
            no_sweep,
            only_sections,
            workers,
            notebook_workers,
            plantuml_workers,
            drawio_workers,
            max_workers,
            notebook_image,
            plantuml_image,
            drawio_image,
            output_mode,
            no_progress,
            no_color,
            verbose_logging,
            language,
            speaker_only,
            targets,
            force_execute,
            resolved_http_replay_mode,
            image_mode,
            image_format,
            inline_images,
            resolved_fail_on_missing_xref,
            effective_provenance_manifest,
            telemetry_db_path=ctx.obj.get("TELEMETRY_DB_PATH") if ctx.obj else None,
            no_html=no_html,
            no_diagrams=no_diagrams,
            explain_rebuilds=resolved_explain_rebuilds,
            allow_unowned_output=allow_unowned_output,
        )
    )

    # ------------------------------------------------------------------
    # Issue #90: exit non-zero when the build summary reports errors.
    # Runs BEFORE the --verify-against block so CI logs show the cell
    # error as the cause rather than a downstream verification diff.
    # ``summary is None`` covers watch mode (which never drives exit
    # policy) and any early-exit path that did not reach finish_build.
    # ------------------------------------------------------------------
    # Issue #143 (sub-bug A): a worker-job timeout always exits non-zero,
    # independent of --fail-on-error. Pending jobs mean the output tree is
    # incomplete, so the build must never look successful. This is checked
    # before the --fail-on-error gate because it is unconditional.
    # Teardown orphans reuse the timed_out flag as their exit-forcing
    # mechanism but need a different message — format_exit_failure
    # distinguishes the two cases (issue #617 follow-up).
    if summary is not None and summary.timed_out:
        click.echo(format_exit_failure(summary), err=True)
        sys.exit(1)

    resolved_fail_on_error = _resolve_fail_on_error(fail_on_error, resolved_http_replay_mode)
    if resolved_fail_on_error and summary is not None and len(summary.errors) > 0:
        click.echo(
            f"\nBuild failed: {len(summary.errors)} error(s) reported "
            f"during build. See summary above.",
            err=True,
        )
        sys.exit(1)

    # ------------------------------------------------------------------
    # Post-build: --snapshot and --verify-against
    # ------------------------------------------------------------------
    # Resolve the effective output path that main_build actually wrote
    # to. main_build does not return it, but resolve_course_paths is
    # the single source of truth used inside the build pipeline too.
    _, default_output = resolve_course_paths(spec_file, data_dir)
    effective_output = output_dir if output_dir is not None else default_output

    if is_snapshot:
        # The build report already covers what was written; print a short
        # confirmation so scripts can grep for the snapshot location.
        click.echo(f"\nSnapshot saved to: {effective_output.resolve()}")

    if verify_against_dir is not None:
        from clm.snapshot import verify_against, verify_against_targets

        # When the spec defines ``<output-targets>`` the regular build
        # writes per-target — either to each target's spec-declared path
        # (no ``--output-dir``) or to ``<output_dir>/<target.name>/``
        # (with ``--output-dir DIR``). The snapshot must be compared
        # per-target — ``<snap>/<target.name>/`` against the
        # corresponding output root — instead of as one monolithic
        # pair, otherwise the entire snapshot looks "extra" because
        # the toplevel prefixes differ. Regression for issue #95 (B).
        verify_spec = CourseSpec.from_file(spec_file.absolute())
        # The build always writes per-target now — explicit ``<output-targets>``
        # or the default shared/trainer/speaker structure (#383) — so verify
        # per-target in both cases.
        if verify_spec.effective_output_targets:
            verify_course_root, _ = resolve_course_paths(spec_file, data_dir)
            target_pairs = []
            for t in verify_spec.effective_output_targets:
                if output_dir is not None:
                    # ``--output-dir DIR`` re-roots each target to
                    # ``<DIR>/<target.name>/`` (matching what
                    # ``Course.from_spec`` produces).
                    target_pairs.append((t.name, (output_dir / t.name).resolve()))
                else:
                    target_path = Path(t.path)
                    if not target_path.is_absolute():
                        target_path = verify_course_root / target_path
                    target_pairs.append((t.name, target_path.resolve()))
            report = verify_against_targets(
                snapshot_dir=verify_against_dir,
                targets=target_pairs,
                include_html=include_html or strict_verify,
                strict=strict_verify,
            )
        else:
            report = verify_against(
                snapshot_dir=verify_against_dir,
                output_dir=effective_output,
                include_html=include_html or strict_verify,
                strict=strict_verify,
            )
        click.echo("\nVerification report")
        click.echo(report.format_text())
        if report.has_diffs:
            click.echo("\nVerification failed: build output diverges from snapshot.")
            sys.exit(1)
        click.echo("\nVerification passed: build output matches snapshot.")
