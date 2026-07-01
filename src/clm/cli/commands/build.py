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

from clm.cli.build_data_classes import BuildSummary
from clm.cli.build_reporter import BuildReporter

# Import shared logging setup
from clm.cli.commands.shared import LOG_LEVELS, cli_console, get_logger, setup_logging
from clm.cli.file_event_handler import FileEventHandler
from clm.cli.git_dir_mover import git_dir_mover
from clm.cli.output_formatter import (
    DefaultOutputFormatter,
    JSONOutputFormatter,
    OutputFormatter,
    QuietOutputFormatter,
    VerboseOutputFormatter,
)
from clm.core.course import Course
from clm.core.course_paths import resolve_course_paths
from clm.core.course_spec import (
    CourseSpec,
    CourseSpecError,
    SectionSelection,
)
from clm.infrastructure.backend import JobsPendingTimeoutError
from clm.infrastructure.backends.sqlite_backend import SqliteBackend
from clm.infrastructure.database.db_operations import DatabaseManager
from clm.infrastructure.messaging.correlation_ids import all_correlation_ids
from clm.infrastructure.utils.path_utils import output_path_for

logger = get_logger(__name__)


VALID_HTTP_REPLAY_MODES = ("replay", "once", "new-episodes", "refresh", "disabled")


def _resolve_http_replay_mode(cli_value: str | None) -> str:
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
            raise click.UsageError(
                f"Invalid CLM_HTTP_REPLAY_MODE={env_value!r}. "
                f"Valid values: {list(VALID_HTTP_REPLAY_MODES)}."
            )
        return normalized
    ci_value = os.environ.get("CI", "").strip().lower()
    if ci_value in ("1", "true", "yes"):
        return "replay"
    return "new-episodes"


def _resolve_http_replay_transport() -> str:
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
        raise click.UsageError(
            "The vcrpy HTTP-replay transport was removed (issue #355); "
            "CLM_HTTP_REPLAY_TRANSPORT=vcrpy is no longer supported. Unset the "
            "variable to use the mitmproxy transport, and re-record any "
            "cassettes still recorded under vcrpy with --http-replay=refresh "
            "(see 'clm info migration')."
        )
    return "mitmproxy"


def _resolve_fail_on_error(cli_value: bool | None, resolved_http_replay_mode: str) -> bool:
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
        raise click.UsageError(
            f"Invalid CLM_FAIL_ON_ERROR={env_value!r}. Valid values: 1/true/yes/0/false/no."
        )
    return resolved_http_replay_mode == "replay"


def _resolve_fail_on_missing_xref(cli_value: bool | None, resolved_http_replay_mode: str) -> bool:
    """Resolve whether an unresolved ``clm:`` cross-reference target fails the
    build (issue #17).

    Precedence mirrors ``_resolve_fail_on_error`` exactly: explicit CLI flag >
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
        raise click.UsageError(
            f"Invalid CLM_FAIL_ON_MISSING_XREF={env_value!r}. Valid values: 1/true/yes/0/false/no."
        )
    return resolved_http_replay_mode == "replay"


def _resolve_explain_rebuilds(cli_flag: bool) -> bool:
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
        raise click.UsageError(
            f"Invalid CLM_EXPLAIN_REBUILDS={env_value!r}. Valid values: 1/true/yes/0/false/no."
        )
    return False


def _resolve_log_level(cli_log_level: str | None) -> str:
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


def _resolve_write_provenance_manifest(
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


def _build_has_docker_notebook_worker(worker_config: object | None) -> bool:
    """True when this build will start a Docker-mode **notebook** worker.

    Only the notebook worker makes the LLM HTTP traffic the replay proxy
    intercepts (plantuml/drawio/jupyterlite never touch it). A ``127.0.0.1``
    proxy is unreachable from inside a container, so a Docker notebook worker
    forces the mitmproxy transport to bind a wildcard address (``0.0.0.0``)
    that the container reaches via ``host.docker.internal`` (issue #165 P4).

    Scoping to the notebook worker keeps the wider ``0.0.0.0`` bind (and its
    LAN-exposure window — see ``_maybe_start_mitmproxy_transport``) off builds
    whose only Docker workers are diagram converters that never use the proxy.
    ``None`` worker_config (older callers / tests) is treated as Direct-only.
    """
    if worker_config is None:
        return False
    try:
        return any(
            c.worker_type == "notebook" and c.execution_mode == "docker" and c.count > 0
            for c in worker_config.get_all_worker_configs()  # type: ignore[attr-defined]
        )
    except Exception:  # noqa: BLE001 — detection must never break the build
        logger.debug("Could not resolve worker execution modes; assuming Direct-only")
        return False


def _resolve_worker_workspace_path(course: Course, worker_config: object | None) -> Path:
    """Host directory to mount at the worker /workspace, and the backend base.

    Docker workers bind-mount a single host directory at ``/workspace`` and the
    notebook worker converts absolute host output paths relative to it. With
    multiple ``<output-targets>`` the mount must therefore cover **all** target
    roots, not just the legacy "primary" ``output_root`` (= first target) — the
    bug behind issue #384, where every non-primary target's container-written
    output failed path conversion and was dropped.

    Only Docker notebook workers write under ``/workspace`` (diagram workers
    write into ``/source``), so the wider ``course.workspace_root`` — which may
    raise if the targets share no mountable common parent — is required *only*
    then. Direct-mode builds never translate paths, so they keep the historical
    ``output_root`` and are unaffected by the multi-target validation.
    """
    if _build_has_docker_notebook_worker(worker_config):
        return course.workspace_root
    return course.output_root


def _maybe_start_mitmproxy_transport(
    mode: str | None, jobs_db_path: Path, worker_config: object | None = None
):
    """Start the out-of-process mitmproxy HTTP-replay proxy.

    mitmproxy is the **only** transport (issue #165; the legacy in-process
    vcrpy transport was removed in #355): it matches repeated and concurrent
    identical requests that the in-kernel vcrpy path mishandled, and the
    kernel's real httpx/httpcore is never patched — the structural fix for
    the issue #143 connection-pool deadlock. This helper is a no-op (returns
    ``None``) when the replay mode is disabled; the caller additionally
    skips it entirely when the course has no http-replay notebook, so a
    replay-free build never starts ``mitmdump``. Returns the running
    :class:`MitmproxyManager` (so the caller can stop it) or ``None``.

    When active it (1) starts one ``mitmdump`` for the whole build, and
    (2) sets ``HTTP(S)_PROXY`` + a ``certifi`` + proxy-CA bundle in
    ``os.environ`` so Direct workers inherit them via ``os.environ.copy()``.

    One shared proxy serves the whole build; each worker tags its requests
    with the destination cassette (P2), so the addon demuxes them into
    per-(topic,language,kind) staging files folded into their canonicals
    after the proxy stops (see ``Course.merge_mitmproxy_cassette_staging``).
    The ``transport.http-cassette.yaml`` here is only the catch-all for any
    untagged traffic.

    **Docker (P4):** when ``worker_config`` reports any Docker-mode worker the
    proxy binds ``0.0.0.0`` so containers can reach it via
    ``host.docker.internal``; the ``os.environ`` proxy URL stays a loopback
    address (``MitmproxyManager.proxy_url``) for Direct workers and the
    readiness poll, while the Docker executor rewrites the host and mounts the
    CA per container. Direct-only builds keep binding ``127.0.0.1`` unchanged.
    """
    if not mode or mode == "disabled":
        return None

    import os as _os
    import time as _time

    import certifi

    from clm.infrastructure.http_replay_mitm import MitmproxyManager
    from clm.workers.notebook.notebook_processor import resolve_http_replay_ignore_hosts

    base = Path(jobs_db_path).resolve().parent / "mitm"
    base.mkdir(parents=True, exist_ok=True)
    cassette = base / "transport.http-cassette.yaml"
    confdir = base / "confdir"
    # Bind a wildcard address only when a Docker notebook worker must reach us
    # via host.docker.internal; Direct-only (and diagram-only-Docker) builds keep
    # the loopback bind so the replay proxy is never exposed beyond the host.
    # NOTE (issue #165 P4 hardening follow-up): a 0.0.0.0 bind makes the proxy an
    # unauthenticated listener on the LAN for the build's duration; in
    # record-capable modes it can relay/record arbitrary traffic. This mirrors the
    # existing 0.0.0.0 WorkerApiServer and is gated to opt-in Docker builds, but a
    # future hardening should bind the docker-bridge gateway IP or add
    # mitmdump --proxyauth with a per-build credential.
    listen_host = "0.0.0.0" if _build_has_docker_notebook_worker(worker_config) else "127.0.0.1"
    # Telemetry-suppression policy: LangSmith by default, overridable via
    # CLM_HTTP_REPLAY_IGNORE_HOSTS. The addon forwards these hosts but never
    # records them into a cassette.
    ignore_hosts = resolve_http_replay_ignore_hosts()
    # Forward the forensic trace dir (issue #165 P5) so the addon can write the
    # per-flow ``proxy`` stream alongside the worker ``socket`` stream. The host
    # pins this env earlier (when CLM_HTTP_REPLAY_TRACE=1); unset → no tracing.
    trace_inv = _os.environ.get("CLM_HTTP_REPLAY_TRACE_INVOCATION_DIR", "").strip()
    trace_dir = Path(trace_inv) if trace_inv else None
    manager = MitmproxyManager(
        cassette_path=cassette,
        mode=mode,
        listen_host=listen_host,
        confdir=confdir,
        ignore_hosts=ignore_hosts,
        trace_dir=trace_dir,
    )
    manager.start()

    # mitmdump writes its CA during startup; the manager only polls the port,
    # so wait briefly for the CA file too before splicing it.
    ca = manager.ca_cert_path
    deadline = _time.monotonic() + 5.0
    while not ca.exists() and _time.monotonic() < deadline:
        _time.sleep(0.05)
    if not ca.exists():
        manager.stop()
        raise RuntimeError(f"mitmproxy CA cert not generated at {ca}")

    # Combined bundle: real roots (certifi) + the proxy CA, so both proxy-forged
    # certs (kernel->proxy) and ignore_hosts direct traffic validate. httpx 0.28
    # honors SSL_CERT_FILE; requests honors REQUESTS_CA_BUNDLE (Phase-0 verified).
    bundle = base / "ca-bundle.pem"
    bundle.write_bytes(Path(certifi.where()).read_bytes() + b"\n" + ca.read_bytes())

    proxy = manager.proxy_url
    _os.environ.update(
        {
            "HTTP_PROXY": proxy,
            "HTTPS_PROXY": proxy,
            "http_proxy": proxy,
            "https_proxy": proxy,
            "SSL_CERT_FILE": str(bundle),
            "REQUESTS_CA_BUNDLE": str(bundle),
            "CURL_CA_BUNDLE": str(bundle),
        }
    )
    logger.info(
        "mitmproxy transport active: proxy=%s mode=%s cassette=%s ca_bundle=%s",
        proxy,
        mode,
        cassette,
        bundle,
    )
    return manager


def _find_env_file(start_dir: Path) -> Path | None:
    """Walk up from start_dir looking for a .env file.

    Returns the path to the first .env file found, or None. Thin wrapper over
    the shared :func:`clm.cli.env_loading.find_env_file` (kept under this name
    for the build command's existing callers and tests).
    """
    from clm.cli.env_loading import find_env_file

    return find_env_file(start_dir)


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
    # ``_resolve_explain_rebuilds``. Off by default so the per-miss diagnostic
    # probe never runs on a normal build; the reasons go to the log file and,
    # under ``--output-mode verbose``, to the console.
    explain_rebuilds: bool = False


def _should_emit_provenance_manifest(summary: BuildSummary | None, config: BuildConfig) -> bool:
    """Whether to write the ``.clm-manifest.json`` after a finished build.

    Beyond the resolved request flag (``config.write_provenance_manifest``), the
    manifest is written only for a **whole-course** build — mirroring the
    post-build sweep's conservative skips, because the manifest is a full
    overwrite of the prior index:

    - ``--watch``: long-running rebuilds populate only the changed file.
    - ``--only-sections``: a section selection would overwrite the full manifest
      with a partial index that silently drops every unselected section's
      provenance (the release engine's join key). The sweep skips this mode for
      the same cross-section-damage reason.
    - timed-out builds: pending jobs mean an unknown set of topics never ran,
      so no honest manifest can be written at all.

    A build with **errors** is no longer an outright skip (issue #295): when
    every error attributes to a topic (see :func:`_failed_topic_ids`), the
    manifest is written for the cleanly-built subset with the failed topics
    excluded and recorded — one flaky deck must not block releasing every
    other topic. (The non-zero exit still happens later, in the ``build``
    entry point.)
    """
    return (
        summary is not None
        and config.write_provenance_manifest
        and not config.watch
        and config.resolved_section_selection is None
        and not summary.timed_out
    )


def _failed_topic_ids(summary: BuildSummary, course) -> set[str] | None:
    """Attribute the build's errors to topics, for the partial manifest (#295).

    Returns the set of topic ids owning at least one errored source file —
    empty when the build was clean. Returns ``None`` when the errors cannot
    all be pinned to topics, in which case the caller must skip the manifest
    entirely (the pre-#295 strict behavior), because an unattributable error
    leaves unknown parts of the output tree suspect:

    - any ``fatal``-severity error (stage-level breakage, e.g. no workers);
    - an error without a ``file_path``;
    - a ``file_path`` that matches no course file (e.g. the spec itself).
    """
    relevant = [e for e in summary.errors if e.severity in ("error", "fatal")]
    if not relevant:
        return set()
    if any(e.severity == "fatal" for e in relevant):
        return None

    topic_by_path: dict[str, str] = {}
    for file in course.files:
        try:
            topic_by_path[str(Path(file.path).resolve())] = file.topic.id
        except OSError:  # pragma: no cover - unresolvable paths are just skipped
            continue

    failed: set[str] = set()
    for error in relevant:
        if not error.file_path:
            return None
        try:
            topic_id = topic_by_path.get(str(Path(error.file_path).resolve()))
        except OSError:
            topic_id = None
        if topic_id is None:
            logger.info(
                "Provenance manifest: error on %r is not attributable to a topic; "
                "falling back to the strict whole-course gate.",
                error.file_path,
            )
            return None
        failed.add(topic_id)
    return failed


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

    from clm.cli.build_data_classes import BuildError

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
            from clm.infrastructure.logging.log_paths import get_log_dir

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
    setup_logging(_resolve_log_level(config.log_level), console_logging=config.verbose_logging)

    # Resolve course paths using centralized helper
    data_dir, default_output = resolve_course_paths(spec_file, config.data_dir)
    logger.debug(f"Data directory set to {data_dir}")
    assert data_dir.exists(), f"Data directory {data_dir} does not exist."

    # Load course specification first to check for output targets.
    #
    # When `--only-sections` is active we need the disabled-inclusive
    # section list so selector indices match the authoring order and
    # disabled-section detection works for the "entire selection disabled"
    # check and the "skip with warning" mixed case. The disabled entries
    # are filtered back out inside `Course.from_spec` via the resolved
    # `SectionSelection`, so the runtime `Course` never sees them.
    keep_disabled = bool(config.selected_sections)
    try:
        spec = CourseSpec.from_file(spec_file, keep_disabled=keep_disabled)
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

    # Determine output_dir behavior. When ``output_dir`` is set with a
    # spec that has ``<output-targets>``, ``Course.from_spec`` re-roots
    # each target under ``<output_dir>/<target.name>/`` (the per-target
    # layout the snapshot/verify flow depends on). With no
    # ``<output-targets>`` ``output_dir`` collapses into a single output
    # tree. When ``output_dir`` is ``None`` and the spec has no
    # targets, fall back to the default ``<course_root>/output``.
    output_dir = config.output_dir
    if output_dir is None and not spec.output_targets:
        output_dir = default_output
        output_dir.mkdir(exist_ok=True)
        logger.debug(f"Output directory set to {output_dir}")

    if output_dir is not None and spec.output_targets:
        target_names = [t.name for t in spec.output_targets]
        logger.info(
            f"Processing course from {spec_file.name} in {data_dir} to "
            f"{output_dir} with targets: {target_names}"
        )
    elif output_dir is not None:
        logger.info(f"Processing course from {spec_file.name} in {data_dir} to {output_dir}")
    elif spec.output_targets:
        target_names = [t.name for t in spec.output_targets]
        logger.info(
            f"Processing course from {spec_file.name} in {data_dir} with targets: {target_names}"
        )

    # Convert CLI options to filter parameters. ``--speaker-only`` is
    # preserved as a CLI flag for backwards compatibility but now selects
    # both private kinds (``trainer`` and ``recording``) so it remains
    # meaningful — narrowing it to ``recording`` alone would silently drop
    # the trainer deck.
    output_languages = [config.language] if config.language else None
    output_kinds = ["trainer", "recording"] if config.speaker_only else None

    if output_languages:
        logger.info(f"Generating output for language(s): {output_languages}")
    if output_kinds:
        logger.info(f"Generating output for kind(s): {output_kinds}")
    if config.selected_targets:
        logger.info(f"Building only targets: {config.selected_targets}")

    # Merge spec file image options with CLI flags (CLI takes priority)
    # CLI defaults are "png" and False; spec file may override these
    effective_image_format = config.image_format
    effective_inline_images = config.inline_images
    if spec.image_options.format != "png" and config.image_format == "png":
        effective_image_format = spec.image_options.format
    if spec.image_options.inline and not config.inline_images:
        effective_inline_images = spec.image_options.inline

    # Resolve --only-sections selectors, if any. This happens *before*
    # Course.from_spec so we can pass the resolved SectionSelection in and
    # skip building the rest of the course.
    section_selection: SectionSelection | None = None
    if config.selected_sections:
        try:
            section_selection = spec.resolve_section_selectors(config.selected_sections)
        except CourseSpecError as e:
            logger.error(f"--only-sections error: {e}")
            console = Console(file=sys.stderr, force_terminal=not config.no_color)
            console.print("\n[bold red]--only-sections error[/bold red]\n")
            console.print(str(e))
            raise SystemExit(1) from None

        # Store the resolved selection so process_course_with_backend can
        # reuse it for the section-level cleanup logic.
        config.resolved_section_selection = section_selection

        # Surface skipped-disabled warnings. We want them in both the log
        # file and stderr — users iterating on a section need to know that
        # a section in their token list was silently dropped.
        for skipped_label in section_selection.skipped_disabled:
            msg = (
                f"Warning: skipping disabled section '{skipped_label}' "
                f'(enabled="false"). Re-enable it in the spec if you '
                f"want to build it."
            )
            logger.warning(msg)
            console = Console(file=sys.stderr, force_terminal=not config.no_color)
            console.print(f"[yellow]{msg}[/yellow]")

        logger.info(
            f"--only-sections mode: building "
            f"{len(section_selection.resolved_indices)} of "
            f"{len(spec.sections)} section(s) declared in the spec. "
            f"Unselected sections' output directories will be left "
            f"untouched and dir-group processing will be skipped."
        )

    # ``--no-html``: drop the HTML format from every topic before the
    # course is constructed, so every downstream derivation (course
    # files, output specs, the CMake export, provenance) agrees that
    # HTML does not exist in this build. Same mechanism watch fast mode
    # applies per-rebuild, but spec-level so the initial build sees it.
    if config.no_html:
        logger.info("--no-html: skipping HTML generation for all topics")
        for section_spec in spec.sections:
            for i, topic_spec in enumerate(section_spec.topics):
                section_spec.topics[i] = evolve(topic_spec, skip_html=True)

    # ``--no-diagrams``: exclude DrawIO/PlantUML sources from every
    # topic's file map at course-construction time, so no conversion
    # jobs are ever scheduled (issue #353). Committed rendered images
    # are ordinary image files and still ship.
    if config.no_diagrams:
        logger.info("--no-diagrams: skipping DrawIO/PlantUML processing for all topics")

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
        image_format=effective_image_format,
        inline_images=effective_inline_images,
        section_selection=section_selection,
        http_replay_mode=config.http_replay_mode,
        no_diagrams=config.no_diagrams,
    )
    # Cross-reference policy (Issue #17): propagate the resolved fail-on-missing
    # decision so payload-time rewrite and build-time validation agree.
    course.fail_on_missing_xref = config.fail_on_missing_xref

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
                            course.output_dir_name[lang],
                            skip_toplevel=target.is_explicit,
                        )
                    )
                if target.kinds & {"trainer", "recording", "speaker"}:
                    root_dirs.append(
                        output_path_for(
                            target.output_root,
                            True,
                            lang,
                            course.output_dir_name[lang],
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
                    output_path_for(
                        course.output_root, is_speaker, language, course.output_dir_name[language]
                    )
                )

    return course, root_dirs, data_dir


def configure_workers(config: BuildConfig):
    """Load worker configuration with CLI overrides."""
    from clm.infrastructure.workers.config_loader import load_worker_config

    cli_overrides: dict[str, str | int | bool] = {}

    if config.workers:
        cli_overrides["default_execution_mode"] = config.workers
    if config.notebook_workers is not None:
        cli_overrides["notebook_count"] = config.notebook_workers
    if config.plantuml_workers is not None:
        cli_overrides["plantuml_count"] = config.plantuml_workers
    if config.drawio_workers is not None:
        cli_overrides["drawio_count"] = config.drawio_workers
    if config.max_workers is not None:
        cli_overrides["max_workers"] = config.max_workers
    if config.notebook_image is not None:
        cli_overrides["notebook_image"] = config.notebook_image

    return load_worker_config(cli_overrides)


def enable_jupyterlite_workers_if_needed(course, worker_config) -> None:
    """Auto-enable a JupyterLite worker when any target opts into the format.

    The ``jupyterlite`` WorkerTypeConfig defaults to ``count=None`` so the
    worker is **not** started for courses that never request JupyterLite
    output. When a course does request it, we bump the count to 1 (unless
    the operator already set a higher count via CLI/config) so the build's
    lifecycle manager spins up one jupyterlite worker alongside the
    notebook/plantuml/drawio workers. This keeps the opt-in contract tight:
    installing the ``[jupyterlite]`` extra has no effect until a course
    actually uses the format.
    """
    wants_jl = any(t.includes_format("jupyterlite") for t in course.output_targets)
    if not wants_jl:
        return
    if worker_config.jupyterlite.count is None:
        worker_config.jupyterlite.count = 1
        logger.info(
            "Enabling 1 jupyterlite worker: course has at least one target "
            "that requests 'jupyterlite' output."
        )


def disable_diagram_workers_if_requested(config: BuildConfig, worker_config) -> None:
    """Zero out the plantuml/drawio worker counts under ``--no-diagrams``.

    With diagram sources excluded from the course file map (issue #353),
    no conversion job can ever be scheduled, so starting the diagram
    workers would only waste startup time — or fail noisily on machines
    without the binaries (the code-export compile CI). ``count=0``
    passes through ``compute_pool_size_cap`` unchanged: zero means "do
    not run any workers of this type". This deliberately overrides an
    explicit ``--plantuml-workers``/``--drawio-workers`` value — a
    diagram worker can do nothing in a build with no diagram jobs.
    """
    if not config.no_diagrams:
        return
    worker_config.plantuml.count = 0
    worker_config.drawio.count = 0
    logger.info("--no-diagrams: not starting plantuml/drawio workers.")


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
    from clm.cli.build_data_classes import BuildWarning

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

    from clm.cli.build_data_classes import BuildError

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
    from clm.cli.build_data_classes import BuildError, BuildWarning

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
        elif category == "split_slide_dual_format":
            error_type = "user"
            guidance = (
                "Remove either the bilingual file or its '.de.py' / '.en.py' "
                "split companions; both formats cannot coexist for the same "
                "slide family. Use `clm slides unify` to merge split files "
                "back, or `clm slides split` to convert the bilingual file."
            )
        elif category == "split_slide_half_pair":
            error_type = "user"
            guidance = (
                "Add the missing '.de.py' or '.en.py' companion. Both halves "
                "of a split slide pair must be present for the build to route "
                "them correctly."
            )
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


def _report_cross_reference_issues(course: Course, build_reporter: BuildReporter) -> None:
    """Validate notebook cross-references and report findings (Issue #17).

    Missing targets are errors when ``course.fail_on_missing_xref`` is set
    (CI-strict), otherwise warnings (the link is dropped at rewrite time).
    Ambiguous multi-notebook targets are always warnings. Honors the active
    ``--section`` selection because the resolver is built from the already
    filtered ``course.sections``.
    """
    from clm.cli.build_data_classes import BuildError, BuildWarning
    from clm.core.cross_references import validate_cross_references

    findings = validate_cross_references(course, fail_on_missing=course.fail_on_missing_xref)
    for finding in findings:
        if finding.severity == "error":
            build_reporter.report_error(
                BuildError(
                    error_type="user",
                    category=finding.type,
                    severity="error",
                    message=finding.message,
                    file_path=finding.source_file,
                    actionable_guidance=(
                        "Add the referenced topic to the course spec (or the "
                        "selected sections), fix the topic id, or pass "
                        "--no-fail-on-missing-xref to downgrade this to a warning."
                    ),
                )
            )
        else:
            build_reporter.report_warning(
                BuildWarning(
                    category=finding.type,
                    message=finding.message,
                    severity="high",
                    file_path=finding.source_file,
                )
            )


def _compute_section_dirs_for_cleanup(course: Course) -> list[Path]:
    """Return the full set of per-section output directories for the
    current (already filtered) ``course.sections``.

    Used only by ``--only-sections`` mode: the cleanup scope is exactly
    the expected section subdirectories of the selected sections across
    every ``(target, language, kind)`` tuple. The base output roots are
    intentionally **not** included — they must stay intact so unselected
    sections survive.
    """
    from clm.core.utils.text_utils import sanitize_file_name
    from clm.infrastructure.utils.path_utils import output_specs

    directories: list[Path] = []
    seen: set[Path] = set()
    for target in course.output_targets:
        for output_spec in output_specs(
            course,
            target.output_root,
            skip_html=False,
            target=target,
        ):
            lang = output_spec.language
            output_dir = output_spec.output_dir
            for section in course.sections:
                section_dir = output_dir / sanitize_file_name(section.name[lang])
                if section_dir not in seen:
                    directories.append(section_dir)
                    seen.add(section_dir)
    return directories


def _maybe_run_sweep(
    *,
    config: BuildConfig,
    root_dirs: list[Path],
    backend,
    build_reporter: BuildReporter,
    only_sections_mode: bool,
) -> None:
    """Invoke the stray-file sweep when the build config opts in.

    The sweep is deliberately conservative — it skips itself whenever
    correctness would be at risk:

    - ``config.sweep`` is False (``--no-sweep`` or ``--incremental``).
    - ``--clean`` mode: the legacy wipe-and-restore flow already
      regenerates the entire tree from scratch, so there is nothing to
      sweep.
    - ``--only-sections`` mode is active: that mode has its own narrower
      cleanup scope (section subdirs only); a full-root sweep would
      delete files for unselected sections.
    - Watch mode (``--watch``): event-driven rebuilds populate only the
      changed files; the sweep would delete everything else.
    - The build recorded fatal errors: the registry is missing entries
      for writes that never happened, so sweeping would remove valid
      files from prior successful builds.
    """
    from clm.cli.output_sweep import sweep_stray_files

    if not config.sweep:
        return

    skip_reason: str | None = None
    if config.clean:
        skip_reason = "--clean already regenerates the whole tree"
    elif only_sections_mode:
        skip_reason = "--only-sections mode has its own cleanup scope"
    elif config.watch:
        skip_reason = "watch mode populates only changed files"
    elif build_reporter.errors:
        skip_reason = (
            f"build recorded {len(build_reporter.errors)} error(s); "
            f"sweep skipped to avoid removing files from prior successful builds"
        )

    if skip_reason is None:
        # The sweep walks every output root; on big courses that is a
        # noticeable pause after the last stage, so tell the user.
        build_reporter.formatter.show_startup_message("Sweeping stale output files...")

    report = sweep_stray_files(
        root_dirs,
        backend.output_write_registry,
        image_registry=getattr(backend, "image_registry", None),
        skip_reason=skip_reason,
    )

    if report.skipped:
        logger.info(f"Stray-file sweep skipped: {report.skip_reason}")
        return

    if report.deleted_files or report.removed_dirs:
        logger.info(
            f"Stray-file sweep removed {len(report.deleted_files)} file(s) "
            f"and {len(report.removed_dirs)} empty directory/ies"
        )
        for path in report.deleted_files:
            logger.debug(f"Sweep deleted file: {path}")
        for path in report.removed_dirs:
            logger.debug(f"Sweep removed empty dir: {path}")
    else:
        logger.debug("Stray-file sweep found no orphans")


def _contains_jobs_pending_timeout(exc: BaseException) -> bool:
    """Return True if ``exc`` is (or wraps) a :class:`JobsPendingTimeoutError`.

    Job submission and completion polling run inside ``asyncio.TaskGroup``
    (see :meth:`Course.process_stage_for_target`), so a timeout raised by
    ``wait_for_completion`` reaches the build orchestration wrapped in a
    ``BaseExceptionGroup``. This unwraps one level of grouping (recursively)
    so the timeout is recognised regardless of nesting.
    """
    if isinstance(exc, JobsPendingTimeoutError):
        return True
    # ``BaseExceptionGroup`` is a builtin on the supported runtimes
    # (requires-python >= 3.11) but ruff's py310 target flags the bare
    # name, so reference it via ``builtins`` to stay lint-clean.
    import builtins

    group_type = getattr(builtins, "BaseExceptionGroup", None)
    if group_type is not None and isinstance(exc, group_type):
        return any(_contains_jobs_pending_timeout(sub) for sub in exc.exceptions)
    return False


async def process_course_with_backend(
    course: Course,
    root_dirs: list[Path],
    backend,
    config: BuildConfig,
    start_time: float,
    build_reporter: BuildReporter,
) -> BuildSummary | None:
    """Process course and optionally watch for changes.

    Returns the :class:`BuildSummary` produced by the final
    ``finish_build()`` call so the caller (and the Click entry point)
    can inspect ``summary.errors`` to decide the process exit code.
    Returns ``None`` in watch mode — long-running watch builds do not
    drive exit-code policy.
    """
    from clm.core.utils.execution_utils import (
        JUPYTERLITE_STAGE,
        NUM_EXECUTION_STAGES,
        execution_stages,
        get_stage_name,
    )

    only_sections_mode = config.resolved_section_selection is not None

    # JupyterLite runs as its own phase after the per-file stages so the
    # progress bar doesn't overrun the HTML stage total. It is skipped in
    # `--only-sections` mode and when no target opts in.
    jupyterlite_job_count = 0 if only_sections_mode else course.count_jupyterlite_operations()
    has_jupyterlite_phase = jupyterlite_job_count > 0
    total_stages = NUM_EXECUTION_STAGES + (1 if has_jupyterlite_phase else 0)

    async def _run_stages() -> BuildSummary | None:
        _report_duplicate_file_warnings(course, build_reporter)
        _report_loading_issues(course, build_reporter)
        _report_cross_reference_issues(course, build_reporter)

        if _report_image_collisions(course, build_reporter):
            build_reporter.finish_build()
            build_reporter.cleanup()
            raise SystemExit("Build failed: image filename collisions detected")

        # Phase 6: refuse to start workers when a split-slide source
        # routing error is recorded. The errors were already pushed into
        # ``summary.errors`` by ``_report_loading_issues`` above, so the
        # final exit policy still surfaces them — but we abort *before*
        # any worker job runs so authors see the routing problem rather
        # than a half-finished build.
        split_routing_categories = {
            "split_slide_dual_format",
            "split_slide_half_pair",
        }
        if any(
            error.get("category") in split_routing_categories for error in course.loading_errors
        ):
            build_reporter.finish_build()
            build_reporter.cleanup()
            raise SystemExit("Build failed: split-slide routing error")

        # Sweep orphan HTTP-replay staging cassettes from prior killed
        # builds before any worker spawns. Without this, partial-chain
        # recordings from aborted sessions stay on disk indefinitely
        # (issue #145). The sweep is a no-op when no topic uses
        # http-replay or when no orphans exist. ``process_all`` and
        # ``process_file`` already call this for their own entry points;
        # the per-stage build path used by ``clm build`` previously did
        # not, leaving the sweep unreachable in normal use.
        try:
            swept = course._sweep_orphan_cassette_staging_files()
        except Exception as exc:  # noqa: BLE001 — defensive: sweep failure must not block build
            logger.warning(
                f"Pre-build orphan cassette sweep raised "
                f"{type(exc).__name__}: {exc}; continuing without sweep."
            )
            swept = 0
        if swept:
            logger.info(f"Pre-build orphan cassette sweep: merged {swept} canonical cassette(s).")

        summary: BuildSummary | None = None
        try:
            try:
                for stage in execution_stages():
                    num_jobs = await course.count_stage_operations(stage)
                    stage_name = get_stage_name(stage)

                    # Always show stage header, even if there are 0 worker
                    # jobs (there may still be cached operations or the
                    # stage may complete instantly).
                    build_reporter.start_stage(stage_name, num_jobs)

                    await course.process_stage(stage, backend)

                # Dir-groups produce the final shipping state of a course.
                # `--only-sections` is a dev-time iteration tool, so we skip
                # dir-group processing entirely in that mode — users who
                # need dir-groups run a full build.
                if not only_sections_mode:
                    await course.process_dir_group(backend)
                    if has_jupyterlite_phase:
                        build_reporter.start_stage(
                            get_stage_name(JUPYTERLITE_STAGE),
                            jupyterlite_job_count,
                        )
                    await course.process_jupyterlite_for_targets(backend)
            except BaseException as exc:  # noqa: BLE001
                # Issue #143 (sub-bug A): a worker-job timeout means the
                # build did not finish — jobs are still pending and the
                # output tree is incomplete. Previously this raised a bare
                # TimeoutError that escaped after the summary was generated,
                # so the build could report "completed successfully" and
                # exit 0. Mark the summary as timed-out (forces a non-zero
                # exit independent of --fail-on-error) and swallow the
                # timeout so the finally block can still produce a summary
                # that lists the stuck jobs. Any other exception re-raises
                # unchanged.
                if _contains_jobs_pending_timeout(exc):
                    build_reporter.mark_timed_out()
                    logger.error(
                        "Build aborted: one or more worker jobs did not "
                        "complete within the per-build timeout. The output "
                        "tree is incomplete; see the error summary."
                    )
                else:
                    raise

        finally:
            # Drain the backend's OutputWriteRegistry into the summary
            # before finish_build serializes it. This is the single
            # call site for the registry → reporter bridge so the
            # totals (and any output_path_conflict warnings) appear
            # exactly once per build.
            build_reporter.report_output_writes(backend.output_write_registry)
            _maybe_run_sweep(
                config=config,
                root_dirs=root_dirs,
                backend=backend,
                build_reporter=build_reporter,
                only_sections_mode=only_sections_mode,
            )
            summary = build_reporter.finish_build()
            build_reporter.cleanup()
        return summary

    summary: BuildSummary | None = None

    if only_sections_mode:
        # `--only-sections` has its own cleanup scope: only the selected
        # sections' per-(target, lang, kind) subdirectories. We do NOT
        # enter `git_dir_mover` — the top-level root dirs (and any `.git`
        # inside them) are untouched, so there is nothing to preserve.
        section_dirs = _compute_section_dirs_for_cleanup(course)
        for section_dir in section_dirs:
            if section_dir.exists():
                logger.info(f"--only-sections: removing section directory {section_dir}")
                shutil.rmtree(section_dir, ignore_errors=True)
            else:
                logger.warning(
                    f"Section '{section_dir.name}' has no existing output "
                    f"directory at {section_dir} — this is normal on the "
                    f"first build of this section or if it was recently "
                    f"renamed. Run a full build to clean up stale "
                    f"directories from old names."
                )

        # Pre-create all output directories before processing starts.
        # This is still idempotent and still needed for Docker workers.
        course.precreate_output_directories()

        total_files = len(course.files)
        output_dir_names = sorted({d.name for d in root_dirs})
        build_reporter.start_build(
            course_name=course.name.en,
            total_files=total_files,
            total_stages=total_stages,
            output_dirs=output_dir_names,
        )

        summary = await _run_stages()

        if config.print_correlation_ids:
            await print_all_correlation_ids()
    elif config.clean:
        # Legacy / emergency-recovery path. Wipes each output root,
        # preserves nested ``.git/`` directories via ``git_dir_mover``,
        # and regenerates everything from scratch. Strictly slower than
        # the default and invalidates git's stat-cache for the entire
        # tree; useful when the on-disk state is corrupt or when an
        # external script relies on a clean rebuild.
        with git_dir_mover(root_dirs):
            for root_dir in root_dirs:
                logger.info(f"Clean build: removing root directory {root_dir}")
                shutil.rmtree(root_dir, ignore_errors=True)

            # Pre-create all output directories before processing starts.
            # This is necessary for Docker workers which may have bind mount
            # visibility issues when directories are created concurrently.
            course.precreate_output_directories()

            total_files = len(course.files)
            output_dir_names = sorted({d.name for d in root_dirs})
            build_reporter.start_build(
                course_name=course.name.en,
                total_files=total_files,
                total_stages=total_stages,
                output_dirs=output_dir_names,
            )

            summary = await _run_stages()

            if config.print_correlation_ids:
                await print_all_correlation_ids()
    else:
        # Default flow: do not wipe, do not move ``.git/``. Hash-aware
        # writes (D1) skip the disk write when content is identical to
        # what's already on disk, preserving mtimes so git's stat-cache
        # stays valid. The post-build stray-file sweep (D2) removes any
        # files left from renamed or removed sections.
        course.precreate_output_directories()

        total_files = len(course.files)
        output_dir_names = sorted({d.name for d in root_dirs})
        build_reporter.start_build(
            course_name=course.name.en,
            total_files=total_files,
            total_stages=total_stages,
            output_dirs=output_dir_names,
        )

        summary = await _run_stages()

        if config.print_correlation_ids:
            await print_all_correlation_ids()

    if config.watch:
        await watch_and_rebuild(course, backend, config)
        # Watch builds run a loop; their per-iteration summaries are
        # not consumed by the entry-point exit policy.
        return None

    return summary


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
) -> BuildSummary | None:
    """Main orchestration function for course building.

    Returns the :class:`BuildSummary` from the build pipeline so the
    Click entry point can apply exit-code policy based on
    ``summary.errors`` (issue #90). Returns ``None`` in watch mode.
    """
    start_time = time()

    selected_targets = [t.strip() for t in targets.split(",") if t.strip()] if targets else None

    # Parse --only-sections tokens. An empty value after stripping is an
    # error, not a silent fallthrough to full build. Resolution happens in
    # initialize_paths_and_course once the spec has been loaded.
    selected_sections: list[str] | None = None
    if only_sections is not None:
        tokens = [t.strip() for t in only_sections.split(",")]
        if not any(tokens) or not all(tokens):
            raise click.UsageError(
                "--only-sections received an empty or whitespace-only value. "
                "Pass at least one selector token, e.g. --only-sections w03."
            )
        selected_sections = tokens

    # Resolve effective HTTP replay mode: CLI > env var > CI-aware default.
    resolved_http_replay_mode = _resolve_http_replay_mode(http_replay)
    # Propagate to child worker processes via env so they see the same mode
    # even if a cassette is packaged into the payload later.
    import os as _os

    _os.environ["CLM_HTTP_REPLAY_MODE"] = resolved_http_replay_mode

    # Validate and pin the HTTP-replay transport (always "mitmproxy"; a
    # leftover CLM_HTTP_REPLAY_TRANSPORT=vcrpy fails loudly — issue #355).
    # The env var is still pinned before workers spawn: an in-container CLM
    # older than the vcrpy-transport removal selects its injection path by
    # this value, so passing it keeps mixed-version Docker images on the tag
    # bootstrap instead of silently reviving their bundled vcrpy bootstrap.
    # (Starting the proxy is still gated on the course actually using
    # http-replay — see below — so a course with no replay topics never
    # spawns mitmdump.)
    _os.environ["CLM_HTTP_REPLAY_TRANSPORT"] = _resolve_http_replay_transport()

    # Forensic HTTP-replay trace harness. When CLM_HTTP_REPLAY_TRACE=1 is
    # set on the host, create a per-invocation trace directory and pin it
    # so subsequent get_writer("host") / get_invocation_dir() calls land
    # in the right place. The directory path is also exported via env so
    # Direct workers inherit it through os.environ.copy(); the Docker
    # executor needs an explicit allowlist entry (see worker_executor.py).
    # Off by default — when CLM_HTTP_REPLAY_TRACE is unset, this is a
    # no-op and no trace directory is created.
    from clm.workers.notebook.http_replay_trace import (
        is_enabled as _trace_is_enabled,
    )
    from clm.workers.notebook.http_replay_trace import (
        make_invocation_dir as _trace_make_invocation_dir,
    )
    from clm.workers.notebook.http_replay_trace import (
        set_invocation_dir as _trace_set_invocation_dir,
    )
    from clm.workers.notebook.http_replay_trace import (
        write_manifest as _trace_write_manifest,
    )

    if _trace_is_enabled():
        _trace_invocation_dir = _trace_make_invocation_dir()
        _trace_set_invocation_dir(_trace_invocation_dir)
        # Record the transport in the manifest so the analyzer picks the
        # proxy-stream bypass model (issue #165 P5). Always "mitmproxy" now;
        # the analyzer keeps its legacy "vcrpy"/missing-key branch only to
        # read trace bundles produced by older CLM versions.
        _trace_write_manifest(
            _trace_invocation_dir,
            http_replay_mode=resolved_http_replay_mode,
            extra={"transport": "mitmproxy"},
        )
        _os.environ["CLM_HTTP_REPLAY_TRACE_INVOCATION_DIR"] = str(_trace_invocation_dir)
        click.echo(f"HTTP-replay trace active: {_trace_invocation_dir}")

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
        sweep=effective_sweep,
        selected_sections=selected_sections,
        fail_on_missing_xref=fail_on_missing_xref,
        write_provenance_manifest=provenance_manifest,
        telemetry_db_path=telemetry_db_path,
        explain_rebuilds=explain_rebuilds,
    )

    # Create output formatter early to show startup messages
    output_formatter = create_output_formatter(config)

    # Show startup progress for loading course
    output_formatter.show_startup_message("Loading course specification...")
    course, root_dirs, data_dir = initialize_paths_and_course(config)
    output_formatter.show_startup_message(
        f"Loaded {len(course.files)} files from {len(course.sections)} sections"
    )
    if course.output_targets:
        output_formatter.show_startup_message(
            f"Output targets: {', '.join(t.name for t in course.output_targets)}"
        )

    build_reporter = BuildReporter(output_formatter)

    worker_config = configure_workers(config)
    enable_jupyterlite_workers_if_needed(course, worker_config)
    disable_diagram_workers_if_requested(config, worker_config)

    from clm.infrastructure.database.schema import init_database
    from clm.infrastructure.workers.lifecycle_manager import WorkerLifecycleManager

    output_formatter.show_startup_message("Initializing databases...")
    logger.debug(f"Initializing job queue database: {config.jobs_db_path}")
    init_database(config.jobs_db_path)

    # In Docker mode this is the common ancestor of all target roots so the
    # /workspace bind-mount reaches every target's writes (issue #384); in
    # Direct mode it stays the legacy primary ``output_root``.
    worker_workspace_path = _resolve_worker_workspace_path(course, worker_config)

    lifecycle_manager = WorkerLifecycleManager(
        config=worker_config,
        db_path=config.jobs_db_path,
        workspace_path=worker_workspace_path,
        cache_db_path=config.cache_db_path,
        data_dir=data_dir,
    )

    # Out-of-process HTTP-replay proxy (issue #165). Must run BEFORE workers
    # spawn so they inherit HTTP(S)_PROXY + the CA bundle via
    # os.environ.copy() (Direct) or the per-container injection (Docker, P4).
    # No-op unless this course actually has an http-replay notebook — a course
    # with no replay topics never needs the proxy (and so never requires
    # mitmdump). ``worker_config`` lets it bind 0.0.0.0 when Docker workers
    # will reach it via host.docker.internal.
    course_uses_http_replay = any(getattr(f, "http_replay", False) for f in course.files)
    mitm_manager = (
        _maybe_start_mitmproxy_transport(
            config.http_replay_mode, config.jobs_db_path, worker_config=worker_config
        )
        if course_uses_http_replay
        else None
    )

    output_formatter.show_startup_message("Starting workers...")
    started_workers = start_managed_workers(lifecycle_manager, worker_config)
    if started_workers:
        output_formatter.show_startup_message(f"Started {len(started_workers)} worker(s)")

    # Persistent kernel crash/flake telemetry (issue #330). Lives next to
    # the cache db by default but is its own file so cache clears never
    # erase the history; the store opens connections lazily per write.
    from clm.infrastructure.database.execution_telemetry import (
        ExecutionTelemetryStore,
        default_telemetry_db_path,
    )

    telemetry_store = ExecutionTelemetryStore(
        config.telemetry_db_path or default_telemetry_db_path(config.cache_db_path)
    )

    summary: BuildSummary | None = None
    try:
        with DatabaseManager(config.cache_db_path, force_init=config.clear_cache) as db_manager:
            backend = SqliteBackend(
                db_path=config.jobs_db_path,
                # Match the worker mount root so any relative output path the
                # backend may resolve agrees with the container's view (#384).
                workspace_path=worker_workspace_path,
                db_manager=db_manager,
                ignore_db=config.ignore_cache,
                build_reporter=build_reporter,
                incremental=config.incremental,
                explain_rebuilds=config.explain_rebuilds,
                image_registry=course.image_registry,
                telemetry_store=telemetry_store,
            )

            async with backend:
                summary = await process_course_with_backend(
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
            output_formatter.show_startup_message("Stopping workers...")
            logger.info("Stopping managed workers...")
            try:
                lifecycle_manager.stop_managed_workers(started_workers)
                logger.info(f"Stopped {len(started_workers)} worker(s)")
            except Exception as e:
                logger.error(f"Failed to stop workers: {e}", exc_info=True)
        if mitm_manager is not None:
            output_formatter.show_startup_message("Merging HTTP replay cassettes...")
            logger.info("Stopping mitmproxy transport...")
            try:
                mitm_manager.stop()
            except Exception as e:
                logger.error(f"Failed to stop mitmproxy: {e}", exc_info=True)
            # The addon wrote per-(topic,language,kind) staging cassettes as it
            # recorded; now that the proxy has flushed and exited, mark this
            # build's staging files complete and fold them into their canonical
            # cassettes (issue #165 P2). Reaching here is the build-completion
            # signal, so partial recordings from a force-killed build (which
            # never reaches this point) stay markerless and are discarded by the
            # next build's pre-build sweep.
            try:
                course.merge_mitmproxy_cassette_staging(
                    mitm_manager.build_id, mode=config.http_replay_mode
                )
            except Exception as e:
                logger.error(f"Failed to merge mitmproxy cassettes: {e}", exc_info=True)

    # Provenance manifests: one .clm-manifest.json per output root (issue #208).
    # On by default since step 3d (and suppressed for --snapshot / --verify-against
    # at the entry point). Only written for a whole-course build — see
    # _should_emit_provenance_manifest, which mirrors the post-build sweep's
    # conservative skips. A build with topic-attributable errors writes a
    # *partial* manifest that excludes and records the failed topics (issue
    # #295) so unrelated topics stay releasable. Capturing the source commit
    # and writing the manifest must never fail an otherwise successful build,
    # so any error here is logged and swallowed.
    if summary is not None and _should_emit_provenance_manifest(summary, config):
        from datetime import datetime, timezone

        from clm.core.git_info import get_git_info
        from clm.core.provenance_manifest import write_provenance_manifests

        try:
            failed_topics = _failed_topic_ids(summary, course)
            if failed_topics is None:
                logger.warning(
                    "Skipping provenance manifest(s): the build reported errors "
                    "that cannot be attributed to specific topics."
                )
            else:
                if failed_topics:
                    logger.warning(
                        "Writing partial provenance manifest(s): %d failed topic(s) "
                        "excluded and recorded (%s).",
                        len(failed_topics),
                        ", ".join(sorted(failed_topics)),
                    )
                output_formatter.show_startup_message("Writing provenance manifests...")
                git = get_git_info(course.course_root)
                written = write_provenance_manifests(
                    course,
                    source_commit=git["commit"],
                    source_dirty=git["dirty"],
                    built_at=datetime.now(timezone.utc).isoformat(),
                    spec_name=config.spec_file.name,
                    failed_topics=failed_topics,
                )
                if written:
                    logger.info("Wrote %d provenance manifest(s)", len(written))
        except Exception as e:
            logger.warning("Failed to write provenance manifest(s): %s", e, exc_info=True)

    # CMake projects for the C++ code export (issue #333, phase 2): one
    # CMakeLists.txt per built code-output directory, one executable target
    # per deck. Regenerable convenience files — like the provenance manifest,
    # this must never fail an otherwise successful build. Skipped for
    # --snapshot / --verify-against builds via the same gate as the manifest
    # so verification trees aren't polluted with extra files.
    if summary is not None and _should_emit_provenance_manifest(summary, config):
        from clm.core.cmake_export import write_cmake_projects

        try:
            written_cmake = write_cmake_projects(course)
            if written_cmake:
                logger.info("Wrote %d CMake project file(s)", len(written_cmake))
        except Exception as e:
            logger.warning("Failed to write CMake project file(s): %s", e, exc_info=True)

    return summary


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
    default=None,
    help=(
        "Comma-separated selector tokens; rebuilds only those sections "
        "and leaves unselected section output directories untouched. "
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
    incremental,
    no_sweep,
    only_sections,
    workers,
    notebook_workers,
    plantuml_workers,
    drawio_workers,
    max_workers,
    notebook_image,
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
    if summary is not None and summary.timed_out:
        click.echo(
            "\nBuild failed: one or more worker jobs timed out and did "
            "not complete. The output tree is incomplete. See the error "
            "summary above.",
            err=True,
        )
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
