"""The build engine: programmatic course building (Phase 8 A4, #802).

This module is the callable equivalent of ``clm build`` — the orchestration
that was historically embedded in ``clm.cli.commands.build``. Entry point:

    from clm.build import BuildConfig, run_build
    summary = await run_build(config)

``run_build`` initializes the databases, records worker identities, starts
worker pools (and the mitmproxy replay transport when the course uses
HTTP replay), drives ``Course.process_all`` against a ``SqliteBackend``,
and returns the :class:`BuildSummary` — exit-code policy is the caller's
job. The Click command remains a thin adapter: it parses/resolves flags
into a :class:`BuildConfig`, injects its watch-mode runner, and applies
exit-code policy to the returned summary.

Layering: this package sits above ``clm.workers`` and below nothing —
it must never import ``clm.cli`` or the extension packages (enforced by
import-linter). Option/spec failures therefore raise the typed errors in
:mod:`clm.build.errors`, not Click exceptions. Two deliberate warts remain
(documented, pre-existing behavior): spec-parse failures in JSON mode print
and raise ``SystemExit``, and the engine mutates ``os.environ`` so worker
subprocesses inherit the replay-transport settings.
"""

import logging
import shutil
import sys
from pathlib import Path
from time import time
from typing import Any, Literal

from attrs import evolve
from rich.console import Console

from clm.build.config import (
    BuildConfig,
    resolve_http_replay_mode,
    resolve_http_replay_transport,
)
from clm.build.errors import SpecValidationFailure
from clm.build.git_dir_mover import git_dir_mover
from clm.build.output_formatter import (
    DefaultOutputFormatter,
    JSONOutputFormatter,
    OutputFormatter,
    QuietOutputFormatter,
    VerboseOutputFormatter,
)
from clm.build.reporter import BuildReporter
from clm.core.backend import JobsPendingTimeoutError
from clm.core.build_data_classes import BuildSummary
from clm.core.course import Course
from clm.core.course_paths import resolve_course_paths
from clm.core.course_spec import (
    CourseSpec,
    CourseSpecError,
    SectionSelection,
)
from clm.core.messaging.correlation_ids import all_correlation_ids
from clm.core.utils.path_utils import output_path_for
from clm.infrastructure.backends.sqlite_backend import SqliteBackend
from clm.infrastructure.database.db_operations import DatabaseManager

logger = logging.getLogger(__name__)

#: Engine-level console for user-facing diagnostics, matching the CLI's
#: stderr console (``clm.cli.commands.shared.cli_console``) so output is
#: byte-identical whether a build runs via Click or programmatically.
_console = Console(file=sys.stderr)


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

    from clm.core.build_data_classes import BuildError

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
        _console.print(
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
    _console.rule("[cyan]Correlation IDs[/cyan]", characters="-")
    _console.print(f"Created {len(all_correlation_ids)} Correlation IDs")
    for cid, data in all_correlation_ids.items():
        _console.print(f"  {cid}: {data.format_dependencies()}")


def initialize_paths_and_course(config: BuildConfig) -> tuple[Course, list[Path], Path]:
    """Initialize paths, load course spec, and create course object.

    Logging setup is the caller's concern: the ``clm build`` command calls
    ``setup_logging`` before invoking the engine; programmatic callers
    configure (or deliberately skip) logging themselves.

    Raises :class:`SpecValidationFailure` when the spec fails validation
    (after rendering the errors via :func:`report_validation_errors`);
    spec-*parse* failures keep the pre-existing render-and-``SystemExit``
    behavior.
    """
    spec_file = config.spec_file.absolute()

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
            raise SpecValidationFailure(
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
    if config.plantuml_image is not None:
        cli_overrides["plantuml_image"] = config.plantuml_image
    if config.drawio_image is not None:
        cli_overrides["drawio_image"] = config.drawio_image

    worker_config = load_worker_config(cli_overrides)
    # The cache keys must see the images that will actually execute (issue
    # #744): record the post-override identity per worker type — the
    # override lives only in this config copy, never in the singleton the
    # identity fallback reads.
    from clm.infrastructure.workers.image_identity import set_effective_worker_identities

    set_effective_worker_identities(worker_config)
    return worker_config


def enable_jupyterlite_workers_if_needed(course, worker_config) -> None:
    """Auto-enable a JupyterLite worker when any target opts into the format.

    The ``jupyterlite`` WorkerTypeConfig defaults to ``count=None`` so the
    worker is **not** started for courses that never request JupyterLite
    output. When a course does request it, we bump the count to 1 (unless
    the operator already set a higher count via CLI/config) so the build's
    lifecycle manager spins up one jupyterlite worker alongside the
    notebook/plantuml/drawio workers. This keeps the opt-in contract tight:
    the jupyterlite worker (which shells out to an isolated ``uvx`` tool env)
    never starts until a course actually uses the format.
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
    from clm.core.build_data_classes import BuildWarning

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

    from clm.core.build_data_classes import BuildError

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
    from clm.core.build_data_classes import BuildError, BuildWarning

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
    from clm.core.build_data_classes import BuildError, BuildWarning
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
    from clm.core.utils.path_utils import output_specs
    from clm.core.utils.text_utils import sanitize_file_name

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
    from clm.build.output_sweep import sweep_stray_files

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
    watch_runner=None,
) -> BuildSummary | None:
    """Process course and optionally watch for changes.

    Returns the :class:`BuildSummary` produced by the final
    ``finish_build()`` call so the caller (and the Click entry point)
    can inspect ``summary.errors`` to decide the process exit code.
    Returns ``None`` in watch mode — long-running watch builds do not
    drive exit-code policy.

    ``watch_runner`` is the injected watch-mode loop
    (``async (course, backend, config) -> None``); the CLI passes its
    watchdog-based implementation. Required when ``config.watch`` is set.
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
            from clm.infrastructure.http_replay_mitm.cassette_staging import (
                sweep_orphan_cassette_staging_files,
            )

            swept = sweep_orphan_cassette_staging_files(course.http_replay_canonical_paths())
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
                    # Issue #596: the finally block below still renders the
                    # summary while this exception propagates. Without an
                    # explicit abort mark it printed "✓ Build completed
                    # successfully" (with 0 errors) for a failed build, and
                    # the stale-output sweep ran against an incomplete write
                    # registry. mark_aborted flips the summary to a failure
                    # and records a fatal error, which also makes the sweep
                    # skip itself.
                    build_reporter.mark_aborted(exc)
                    raise

        finally:
            # Drain the backend's OutputWriteRegistry into the summary
            # before finish_build serializes it. This is the single
            # call site for the registry → reporter bridge so the
            # totals (and any output_path_conflict warnings) appear
            # exactly once per build.
            build_reporter.report_output_writes(backend.output_write_registry)
            summary = build_reporter.finish_build()
            # Run the sweep after finish_build: show_summary stops the
            # Rich live progress display, so the "Sweeping stale output
            # files..." notice prints below the summary instead of being
            # pushed above the still-active progress bar.
            _maybe_run_sweep(
                config=config,
                root_dirs=root_dirs,
                backend=backend,
                build_reporter=build_reporter,
                only_sections_mode=only_sections_mode,
            )
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
        if watch_runner is None:
            raise RuntimeError(
                "config.watch is set but no watch_runner was provided; watch "
                "mode is CLI-only (the clm build command injects its "
                "watchdog-based runner)."
            )
        await watch_runner(course, backend, config)
        # Watch builds run a loop; their per-iteration summaries are
        # not consumed by the entry-point exit policy.
        return None

    return summary


def _record_teardown_orphans(summary: BuildSummary, orphans: list[dict[str, Any]]) -> None:
    """Fold pool-teardown orphan jobs into an already-finalized build summary.

    ``JobQueue.mark_orphaned_jobs_failed`` runs at pool teardown — after
    ``finish_build`` has rendered the summary — so its orphans would otherwise
    never influence the exit policy, and the build could exit 0 with a
    silently-incomplete output tree. Each orphan becomes an infrastructure
    ``BuildError`` and the summary is marked timed-out, giving the same
    unconditional non-zero exit a per-stage job timeout gets (issue #617).
    """
    from clm.core.build_data_classes import BuildError

    for orphan in orphans:
        summary.errors.append(
            BuildError(
                error_type="infrastructure",
                category="orphaned_job",
                severity="error",
                file_path=str(orphan.get("input_file", "unknown")),
                message=(
                    "Worker died mid-job and the job was orphaned when the "
                    "worker pool stopped; the output for this file was not "
                    "produced."
                ),
                actionable_guidance=(
                    "A worker process stopped before finishing this job (a "
                    "mid-build crash or a shutdown race), so the job never "
                    "completed. Re-run the build; the input file is not at "
                    "fault. See issue #617."
                ),
                job_id=orphan.get("id"),
            )
        )
    # Orphaned jobs mean the output tree is incomplete — force an unconditional
    # non-zero exit, matching how a per-stage worker-job timeout is treated.
    summary.timed_out = True


def _format_exit_failure(summary: BuildSummary) -> str:
    """Compose the exit-time failure message for a ``timed_out`` summary.

    ``summary.timed_out`` is set by two distinct paths: a genuine per-stage
    worker-job timeout (issue #143), and :func:`_record_teardown_orphans`,
    which reuses the flag as its exit-forcing mechanism (issue #617). Orphans
    are appended *after* finish_build rendered the summary, so for them the
    generic "timed out … see the error summary above" message is wrong on
    both counts — nothing about them appears above, and they did not time
    out. Name the orphaned inputs directly instead.
    """
    orphans = [e for e in summary.errors if e.category == "orphaned_job"]
    if orphans:
        files = ", ".join(e.file_path for e in orphans)
        return (
            f"\nBuild failed: {len(orphans)} worker job(s) were orphaned at "
            f"pool shutdown (worker died mid-job) and produced no output: "
            f"{files}. The output tree is incomplete; re-run the build. "
            f"See issue #617."
        )
    return (
        "\nBuild failed: one or more worker jobs timed out and did "
        "not complete. The output tree is incomplete. See the error "
        "summary above."
    )


async def run_build(
    config: BuildConfig,
    *,
    output_formatter: OutputFormatter | None = None,
    build_reporter: BuildReporter | None = None,
    watch_runner=None,
) -> BuildSummary | None:
    """Run a course build from an already-constructed :class:`BuildConfig`.

    This is the programmatic equivalent of ``clm build``: it pins the
    HTTP-replay transport env for worker subprocesses, loads the course,
    initializes the databases, records worker identities, starts worker
    pools (and the mitmproxy transport when the course uses HTTP replay),
    drives the stage loop against a :class:`SqliteBackend`, and emits the
    provenance manifest and CMake exports.

    Returns the :class:`BuildSummary` so the caller can apply exit-code
    policy based on ``summary.errors`` (issue #90). Returns ``None`` in
    watch mode.

    The engine does **not** configure logging — the CLI calls
    ``setup_logging`` before invoking it; programmatic callers configure
    logging themselves. ``output_formatter`` / ``build_reporter`` default
    to the config-derived formatter (JSON/quiet/verbose/default) and a
    fresh :class:`BuildReporter` over it. ``watch_runner`` is required
    when ``config.watch`` is set (the CLI injects its watchdog loop).
    """
    start_time = time()

    # Resolve the effective HTTP replay mode (idempotent when the caller —
    # e.g. the CLI — already resolved it) and pin it into the config so
    # every downstream read agrees.
    resolved_http_replay_mode = resolve_http_replay_mode(config.http_replay_mode)
    config.http_replay_mode = resolved_http_replay_mode

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
    _os.environ["CLM_HTTP_REPLAY_TRANSPORT"] = resolve_http_replay_transport()

    # Forensic HTTP-replay trace harness. When CLM_HTTP_REPLAY_TRACE=1 is
    # set on the host, create a per-invocation trace directory and pin it
    # so subsequent get_writer("host") / get_invocation_dir() calls land
    # in the right place. The directory path is also exported via env so
    # Direct workers inherit it through os.environ.copy(); the Docker
    # executor needs an explicit allowlist entry (see worker_executor.py).
    # Off by default — when CLM_HTTP_REPLAY_TRACE is unset, this is a
    # no-op and no trace directory is created.
    from clm.core.http_replay_trace import is_enabled as _trace_is_enabled
    from clm.core.http_replay_trace import make_invocation_dir as _trace_make_invocation_dir
    from clm.core.http_replay_trace import set_invocation_dir as _trace_set_invocation_dir
    from clm.core.http_replay_trace import write_manifest as _trace_write_manifest

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
        print(f"HTTP-replay trace active: {_trace_invocation_dir}")

    # Create output formatter early to show startup messages
    if output_formatter is None:
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

    if build_reporter is None:
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

    # Resolve the Direct-mode notebook-kernel interpreter (Wave 2b): env
    # CLM_NOTEBOOK_KERNEL_PYTHON > course spec <kernel-python> > clm.toml
    # [jupyter].kernel_python > "" (clm's own env). "" leaves today's behaviour
    # untouched. Docker mode ignores it (a host interpreter is meaningless in a
    # container). Resolved here where the spec is in scope; the executor just
    # provisions + injects JUPYTER_PATH.
    from clm.infrastructure.workers.kernel_env import (
        resolve_kernel_interpreter,
        resolve_notebook_kernel_python,
    )

    # First pick the winning tier (env > spec > clm.toml), then normalise it to
    # an absolute interpreter: a value may be a venv *directory* (resolved to the
    # platform interpreter inside it) and a relative value is anchored to the
    # project root — so a single committed <kernel-python> works cross-platform
    # and regardless of the invocation cwd.
    notebook_kernel_python = resolve_kernel_interpreter(
        resolve_notebook_kernel_python(course.spec.kernel_python)
    )

    lifecycle_manager = WorkerLifecycleManager(
        config=worker_config,
        db_path=config.jobs_db_path,
        workspace_path=worker_workspace_path,
        cache_db_path=config.cache_db_path,
        data_dir=data_dir,
        notebook_kernel_python=notebook_kernel_python,
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
                # Tag every submitted job with the execution mode this build
                # resolved for its worker type, so only matching-mode workers
                # claim it. Without this, a Direct worker from a concurrent
                # build sharing the jobs DB could take e.g. a C++ notebook
                # job and fail with NoSuchKernel (xcpp20 lives only in the
                # Docker image).
                worker_execution_modes={
                    c.worker_type: c.execution_mode for c in worker_config.get_all_worker_configs()
                },
                # Scope the activation-timeout dead-marking to workers this
                # build's lifecycle session owns (issue #597) — a timeout on
                # our own workers must not condemn a concurrent build's
                # still-starting pre-registrations in a shared jobs DB.
                worker_session_id=lifecycle_manager.session_id,
            )

            async with backend:
                summary = await process_course_with_backend(
                    course=course,
                    root_dirs=root_dirs,
                    backend=backend,
                    config=config,
                    start_time=start_time,
                    build_reporter=build_reporter,
                    watch_runner=watch_runner,
                )
    except KeyboardInterrupt:
        logger.info("Build interrupted, cleaning up...")
        raise
    finally:
        if started_workers and worker_config.auto_stop:
            output_formatter.show_startup_message("Stopping workers...")
            logger.info("Stopping managed workers...")
            try:
                orphaned_jobs = lifecycle_manager.stop_managed_workers(started_workers)
                logger.info(f"Stopped {len(started_workers)} worker(s)")
                # Orphans are discovered only after the pool stops — i.e. after
                # finish_build already rendered the summary. Fold them into the
                # summary here so they still drive the exit policy instead of
                # being silently banked in the jobs DB (issue #617). Orphaned
                # jobs mean the output tree is incomplete, so mark the summary
                # timed-out for an unconditional non-zero exit, matching the
                # per-stage-timeout policy.
                if orphaned_jobs and summary is not None:
                    _record_teardown_orphans(summary, orphaned_jobs)
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
                from clm.infrastructure.http_replay_mitm.cassette_staging import (
                    merge_mitmproxy_cassette_staging,
                )

                merge_mitmproxy_cassette_staging(
                    course.http_replay_canonical_paths(),
                    mitm_manager.build_id,
                    mode=config.http_replay_mode,
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
