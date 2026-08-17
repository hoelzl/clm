"""Build configuration and option resolution (Phase 8 A4, #802).

:class:`BuildConfig` is the parameter object for a programmatic build
(:func:`clm.build.engine.run_build`); the ``resolve_*`` family implements
the flag > ``CLM_*`` env var > default precedence shared by the CLI and
programmatic callers. Invalid values raise
:class:`clm.build.errors.BuildOptionError` — the ``clm build`` command
converts that to ``click.UsageError`` at the entry point.
"""

from dataclasses import dataclass
from pathlib import Path

from clm.build.errors import BuildOptionError
from clm.core.course_spec import SectionSelection

VALID_HTTP_REPLAY_MODES = ("replay", "once", "new-episodes", "refresh", "disabled")


def resolve_http_replay_mode(cli_value: str | None) -> str:
    """Resolve the effective HTTP replay mode for this build.

    Precedence: explicit CLI flag > ``CLM_HTTP_REPLAY_MODE`` env var >
    CI-aware default (``replay`` when ``CI=true``, ``new-episodes``
    otherwise).
    """
    import os

    if cli_value is not None:
        return cli_value
    env_value = os.environ.get("CLM_HTTP_REPLAY_MODE")
    if env_value:
        normalized = env_value.strip().lower()
        if normalized not in VALID_HTTP_REPLAY_MODES:
            raise BuildOptionError(
                f"Invalid CLM_HTTP_REPLAY_MODE={env_value!r}. "
                f"Valid values: {list(VALID_HTTP_REPLAY_MODES)}."
            )
        return normalized
    ci_value = os.environ.get("CI", "").strip().lower()
    if ci_value in ("1", "true", "yes"):
        return "replay"
    return "new-episodes"


def resolve_http_replay_transport() -> str:
    """Resolve the effective HTTP-replay transport: always ``mitmproxy``.

    The legacy in-process vcrpy transport was removed (issue #355) after
    mitmproxy had been the default since 1.10 (issue #165). A leftover
    ``CLM_HTTP_REPLAY_TRANSPORT=vcrpy`` (CI config, shell profile, course
    Makefile) must fail **loudly** here rather than be silently ignored:
    whoever set it was relying on the in-kernel transport and their
    vcrpy-recorded cassettes will not strict-replay through the proxy — the
    actionable fix is to re-record, not to discover cassette misses
    mid-build. Any other value (including unset) resolves to ``mitmproxy``.
    """
    import os

    value = os.environ.get("CLM_HTTP_REPLAY_TRANSPORT", "").strip().lower()
    if value == "vcrpy":
        raise BuildOptionError(
            "The vcrpy HTTP-replay transport was removed (issue #355); "
            "CLM_HTTP_REPLAY_TRANSPORT=vcrpy is no longer supported. Unset the "
            "variable to use the mitmproxy transport, and re-record any "
            "cassettes still recorded under vcrpy with --http-replay=refresh "
            "(see 'clm info migration')."
        )
    return "mitmproxy"


def resolve_fail_on_error(cli_value: bool | None, resolved_http_replay_mode: str) -> bool:
    """Resolve whether ``clm build`` should exit non-zero when the
    build summary reports errors (issue #90).

    Precedence: explicit CLI flag > ``CLM_FAIL_ON_ERROR`` env var >
    replay-mode default. The default policy is **on** under
    ``--http-replay=replay`` (the CI-strict mode) and **off** under all
    other replay modes — local iterative work over partial / transient
    failures must not start exiting non-zero by default.
    """
    import os

    if cli_value is not None:
        return cli_value
    env_value = os.environ.get("CLM_FAIL_ON_ERROR")
    if env_value is not None:
        normalized = env_value.strip().lower()
        if normalized in ("1", "true", "yes"):
            return True
        if normalized in ("0", "false", "no"):
            return False
        raise BuildOptionError(
            f"Invalid CLM_FAIL_ON_ERROR={env_value!r}. Valid values: 1/true/yes/0/false/no."
        )
    return resolved_http_replay_mode == "replay"


def resolve_fail_on_missing_xref(cli_value: bool | None, resolved_http_replay_mode: str) -> bool:
    """Resolve whether an unresolved ``clm:`` cross-reference target fails the
    build (issue #17).

    Precedence mirrors ``resolve_fail_on_error`` exactly: explicit CLI flag >
    ``CLM_FAIL_ON_MISSING_XREF`` env var > replay-mode default. The default is
    **on** under ``--http-replay=replay`` (the CI-strict mode) and **off**
    otherwise, so a developer building a single section locally legitimately
    excludes link targets without the build erroring (the link is dropped with
    a warning instead).
    """
    import os

    if cli_value is not None:
        return cli_value
    env_value = os.environ.get("CLM_FAIL_ON_MISSING_XREF")
    if env_value is not None:
        normalized = env_value.strip().lower()
        if normalized in ("1", "true", "yes"):
            return True
        if normalized in ("0", "false", "no"):
            return False
        raise BuildOptionError(
            f"Invalid CLM_FAIL_ON_MISSING_XREF={env_value!r}. Valid values: 1/true/yes/0/false/no."
        )
    return resolved_http_replay_mode == "replay"


def resolve_explain_rebuilds(cli_flag: bool) -> bool:
    """Resolve whether ``clm build`` logs why each deck missed the cache.

    Off by default: the extra per-miss probe only runs when explicitly
    requested, so a normal build pays nothing. Enabled by the
    ``--explain-rebuilds`` flag or ``CLM_EXPLAIN_REBUILDS={1,true,yes}``.
    """
    import os

    if cli_flag:
        return True
    env_value = os.environ.get("CLM_EXPLAIN_REBUILDS")
    if env_value is not None:
        normalized = env_value.strip().lower()
        if normalized in ("1", "true", "yes"):
            return True
        if normalized in ("0", "false", "no"):
            return False
        raise BuildOptionError(
            f"Invalid CLM_EXPLAIN_REBUILDS={env_value!r}. Valid values: 1/true/yes/0/false/no."
        )
    return False


def resolve_log_level(cli_log_level: str | None) -> str:
    """Effective logging level: ``--log-level`` > env/config file > ``INFO``.

    Phase 3 of the config/CLI/env unification: ``--log-level`` now defaults to
    ``None`` (unset), so a ``[logging] log_level`` in ``clm.toml`` — or
    ``CLM_LOGGING__LOG_LEVEL`` — finally takes effect when the flag is absent
    (``ClmConfig.logging.log_level`` already folds env over config file, and
    itself defaults to ``INFO``). The resolved level flows to both host logging
    (``setup_logging``) and, via ``logger.getEffectiveLevel()`` at pool-manager
    creation, to the workers.
    """
    from clm.infrastructure.config import get_config, resolve_setting

    resolved = resolve_setting(
        cli_log_level,
        config_value=get_config().logging.log_level,
        default="INFO",
    )
    # resolve_setting is typed -> Any; the inputs here are always level strings.
    return str(resolved)


def resolve_write_provenance_manifest(
    *, requested: bool, is_snapshot: bool, verify_against_dir: Path | None
) -> bool:
    """Whether this build should write the ``.clm-manifest.json`` provenance index.

    ``requested`` is the resolved ``--provenance-manifest/--no-provenance-manifest``
    value (on by default since issue #208 step 3d). It is always suppressed for
    ``--snapshot`` and ``--verify-against`` builds: the manifest embeds a build
    timestamp and source commit, so it is intentionally non-deterministic and
    must never enter a byte-reproducibility baseline. ``--strict-verify`` skips
    nothing, so a verifier skip-list cannot save it — the only correct place to
    drop it is here, before the build runs.
    """
    return requested and not is_snapshot and verify_against_dir is None


@dataclass
class BuildConfig:
    """Configuration for course build process."""

    spec_file: Path
    data_dir: Path
    output_dir: Path
    log_level: str | None
    cache_db_path: Path
    jobs_db_path: Path
    ignore_cache: bool
    clear_cache: bool
    watch: bool
    print_correlation_ids: bool

    # Worker configuration
    workers: str | None
    notebook_workers: int | None
    plantuml_workers: int | None
    drawio_workers: int | None
    notebook_image: str | None
    plantuml_image: str | None
    drawio_image: str | None

    # Hard cap on effective worker count per type; clamped against CPU/RAM
    # by clm.infrastructure.workers.pool_size_cap. Default ``None`` so
    # older callers that don't know about the cap still construct
    # BuildConfig without breaking.
    max_workers: int | None = None

    # Execution-telemetry database (issue #330). ``None`` resolves to
    # ``clm_telemetry.db`` next to ``cache_db_path``. Kept separate from the
    # cache db so clearing caches never erases the kernel crash/flake
    # history; ``clm kernel-triage`` points this at the REAL telemetry db
    # while building against throwaway cache/jobs dbs.
    telemetry_db_path: Path | None = None

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

    # Skip HTML generation for every topic (``--no-html``), as if each
    # carried ``html="no"`` in the spec. HTML is the only output format
    # whose generation executes notebooks, so a ``--no-html`` build needs
    # no Jupyter kernel — the mode the code-export compile CI uses
    # (issue #333).
    no_html: bool = False

    # Skip DrawIO and PlantUML processing entirely (``--no-diagrams``,
    # issue #353): diagram sources never enter the course file map, so
    # zero conversion jobs are scheduled and the plantuml/drawio workers
    # are not started. Rendered images committed next to the sources
    # (``slides/**/img/``) still ship as ordinary image files — the mode
    # the code-export compile CI uses on runners without the diagram
    # binaries.
    no_diagrams: bool = False

    # Notebook execution mode
    force_execute: bool = False

    # HTTP replay record mode: "replay", "once", "new-episodes", "refresh",
    # "disabled", or None. None means "pick default": ``replay`` in CI
    # (``CI=true``), ``new-episodes`` otherwise. Only affects topics that
    # opt in via ``http-replay="yes"`` in the spec.
    http_replay_mode: str | None = None

    # Image storage mode
    image_mode: str = "duplicated"  # "duplicated" or "shared"

    # Image output format
    image_format: str = "png"  # "png" or "svg"

    # Whether to inline images as data URLs in notebooks
    inline_images: bool = False

    # Incremental build mode
    incremental: bool = False  # Only write newly processed files, skip cached ones

    # Legacy wipe-and-restore output flow (opt-in via ``--clean``). When
    # ``True``, the build moves nested ``.git/`` directories aside, runs
    # ``shutil.rmtree`` over each output root, and regenerates everything
    # from scratch. The default (``False``) preserves the existing output
    # tree, relies on hash-aware writes to skip unchanged files, and
    # cleans up orphans with the post-build sweep. ``--clean`` is intended
    # for emergency recovery from a corrupted output tree.
    clean: bool = False

    # Escape hatch for the output-ownership gate (finding S11, #798).
    # ``--clean`` and the post-build sweep delete files clm did not
    # write, so both refuse to act in an output root that was neither
    # empty at build start nor carries a ``.clm-manifest.json`` from an
    # earlier build. Setting this to ``True`` (CLI:
    # ``--allow-unowned-output``) proceeds anyway — deliberately its own
    # flag, since ``--clean`` is the operation being gated.
    allow_unowned_output: bool = False

    # Stray-file sweep at end of build (Feature D2 of git-friendly output
    # writes). Default ``True`` since the new build flow no longer wipes
    # the output tree, so leftover files from renamed/removed sections
    # need an explicit cleanup pass. ``--no-sweep`` opts out (useful when
    # iterating on a single section). Skipped under ``--incremental``,
    # ``--only-sections``, ``--watch``, and after stage-fatal errors.
    sweep: bool = True

    # --only-sections selector tokens (raw, with prefixes preserved for
    # error messages). None or empty list means full build. Non-empty means
    # the build is section-filtered: the root output directories are left
    # alone, only the selected sections' output subdirectories are wiped
    # and rebuilt, and dir-group processing is skipped.
    selected_sections: list[str] | None = None

    # Resolved section selection, populated by `initialize_paths_and_course`
    # when `selected_sections` is non-empty. Used by `process_course_with_backend`
    # to decide which section directories to clean up and by the watch-mode
    # event handler to filter events.
    resolved_section_selection: SectionSelection | None = None

    # Cross-reference policy (Issue #17). When True, an unresolved ``clm:``
    # cross-reference target fails the build; when False it is a warning and
    # the link is dropped. Resolved from ``--fail-on-missing-xref`` /
    # ``CLM_FAIL_ON_MISSING_XREF`` / the replay-mode default, mirroring
    # ``fail_on_error`` (issue #90).
    fail_on_missing_xref: bool = False

    # Emit a ``.clm-manifest.json`` provenance index per output root after a
    # successful (non-watch) build (issue #208). On by default since step 3d:
    # ``clm git`` now excludes (and self-heals) the manifest from every
    # distributed output/cohort repo, so the per-topic release workflow gets
    # the manifest without an opt-in flag. ``--no-provenance-manifest`` opts
    # out. The manifest is suppressed for ``--snapshot`` / ``--verify-against``
    # builds at the entry point — it embeds a build timestamp and source commit,
    # so it must never enter a byte-reproducibility baseline.
    write_provenance_manifest: bool = True

    # Log why each deck missed the cache and is being rebuilt (issue: many
    # decks rebuilding whose sources should not change). Resolved from
    # ``--explain-rebuilds`` / ``CLM_EXPLAIN_REBUILDS`` by
    # ``resolve_explain_rebuilds``. Off by default so the per-miss diagnostic
    # probe never runs on a normal build; the reasons go to the log file and,
    # under ``--output-mode verbose``, to the console.
    explain_rebuilds: bool = False
