"""Choose where diagrams render for one build (issue #1143).

The same diagram source renders to different PNGs in the Docker converter image
and on the host: different Draw.io/PlantUML versions and fonts. Rendered PNGs are
committed to course repositories, so a build in the "other" mode rewrites them.
``auto``, the default, therefore renders in Docker when the daemon is reachable
and the converter image is already present, and otherwise falls back to the host
tool. A fallback produces those differing PNGs, so it always comes with a reason
for the build's warnings; see :func:`fallback_warning_message`.

Effective mode per diagram worker type, highest precedence first:

1. ``clm build --diagram-mode direct|docker``.
2. The per-type ``[worker_management.<type>] execution_mode`` (or its env var).
3. ``clm build --diagram-mode auto``.
4. ``clm build --workers``.
5. ``worker_management.diagram_mode`` (or ``CLM_WORKER_MANAGEMENT__DIAGRAM_MODE``).
6. ``default_execution_mode = "docker"``.
7. ``auto``, the default.

Only ``auto`` probes and falls back. Everything else is strict, so a missing
Docker daemon stays the error it always was for ``--workers docker``.

The resolved mode is written to the per-type ``execution_mode`` of the caller's
config copy, so the pool manager, the job tagging and the cache identity all see
one concrete answer and ``auto`` exists nowhere downstream of this module.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection, Iterable, Mapping
from pathlib import Path

from attrs import define, field

from clm.infrastructure.config import DEFAULT_WORKER_IMAGES, WorkersManagementConfig
from clm.infrastructure.workers.worker_executor import (
    WholeVolumeMountError,
    ensure_docker_worker_deps,
    refuse_whole_volume_mount,
)

logger = logging.getLogger(__name__)

DIAGRAM_WORKER_TYPES = ("plantuml", "drawio")

_DISPLAY_NAMES = {"plantuml": "PlantUML", "drawio": "Draw.io"}

#: Upper bound on one daemon round trip. A stopped Docker Desktop fails at once
#: (measured 1 ms: the named pipe does not exist); only an unresponsive daemon
#: or remote ``DOCKER_HOST`` waits this long, and only once per build.
PROBE_TIMEOUT_SECONDS = 5

#: Maps ``{worker type: image}`` to ``{worker type: reason it is unusable, or None}``.
DockerProbe = Callable[[Mapping[str, str]], Mapping[str, str | None]]


@define
class DiagramModeDecision:
    """What ``auto`` decided for this build; explicit modes leave both empty."""

    #: Worker types ``auto`` sent to Docker.
    auto_docker: set[str] = field(factory=set)
    #: Worker type -> why ``auto`` could not use Docker for it.
    fallbacks: dict[str, str] = field(factory=dict)


def probe_docker_images(images: Mapping[str, str]) -> dict[str, str | None]:
    """Report, per worker type, why its image cannot be used right now.

    ``None`` means usable. Costs about 60 ms on a healthy daemon (client
    negotiation, ping, one image inspect per type) and never pulls: a build
    should not start a 1 GB download nobody asked for, and a machine without
    network access must still build. Never raises.
    """
    try:
        ensure_docker_worker_deps()
    except RuntimeError as exc:
        return dict.fromkeys(images, str(exc))

    import docker
    import docker.errors  # type: ignore[import-not-found]

    client = None
    try:
        client = docker.from_env(timeout=PROBE_TIMEOUT_SECONDS)  # type: ignore[attr-defined]
        client.ping()
    except Exception as exc:  # noqa: BLE001 - any failure means "not usable"
        if client is not None:
            client.close()
        return dict.fromkeys(images, f"the Docker daemon is not reachable ({exc})")

    reasons: dict[str, str | None] = {}
    try:
        for worker_type, image in images.items():
            try:
                client.images.get(image)
                reasons[worker_type] = None
            except docker.errors.ImageNotFound:
                reasons[worker_type] = (
                    f"image {image} is not present locally (fetch it once with: docker pull {image})"
                )
            except Exception as exc:  # noqa: BLE001 - same honest fallback
                reasons[worker_type] = f"image {image} could not be inspected ({exc})"
    finally:
        client.close()
    return reasons


def resolve_diagram_execution_modes(
    worker_config: WorkersManagementConfig,
    *,
    types_present: Collection[str],
    cli_diagram_mode: str | None = None,
    cli_workers: str | None = None,
    mount_roots: Iterable[tuple[Path, str]] = (),
    probe: DockerProbe | None = None,
) -> DiagramModeDecision:
    """Write each diagram type's effective mode into ``worker_config``.

    Args:
        worker_config: This build's config copy; mutated in place.
        types_present: Diagram worker types this build may have jobs for. A type
            not listed never touches Docker under ``auto``.
        cli_diagram_mode: The ``--diagram-mode`` value, if given.
        cli_workers: The ``--workers`` value, if given.
        mount_roots: ``(path, label)`` of each directory a container would mount.
        probe: Availability check; defaults to :func:`probe_docker_images`.

    Returns:
        What ``auto`` decided; see :class:`DiagramModeDecision`.
    """
    decision = DiagramModeDecision()
    auto_types: list[str] = []

    for worker_type in DIAGRAM_WORKER_TYPES:
        type_config = getattr(worker_config, worker_type)
        if cli_diagram_mode in ("direct", "docker"):
            type_config.execution_mode = cli_diagram_mode
            continue
        if type_config.execution_mode:
            continue
        requested = cli_diagram_mode or cli_workers or worker_config.diagram_mode
        if requested != "auto":
            type_config.execution_mode = requested
            continue
        # ``default_execution_mode = "docker"`` keeps its strict meaning unless
        # the operator explicitly asked for ``auto`` on this command line.
        if worker_config.default_execution_mode == "docker" and cli_diagram_mode != "auto":
            continue
        # With auto-start off the operator manages the workers, in a mode this
        # build cannot see; jobs tagged differently would never be claimed.
        if not worker_config.auto_start:
            continue
        if worker_type not in types_present:
            continue
        auto_types.append(worker_type)

    if not auto_types:
        return decision

    reasons = _unusable_reasons(worker_config, auto_types, mount_roots, probe)
    for worker_type in auto_types:
        reason = reasons.get(worker_type)
        type_config = getattr(worker_config, worker_type)
        if reason is None:
            type_config.execution_mode = "docker"
            decision.auto_docker.add(worker_type)
        else:
            type_config.execution_mode = "direct"
            decision.fallbacks[worker_type] = reason
    logger.info(
        f"Diagram mode auto: docker={sorted(decision.auto_docker)}, fallbacks={decision.fallbacks}"
    )
    return decision


def _unusable_reasons(
    worker_config: WorkersManagementConfig,
    worker_types: list[str],
    mount_roots: Iterable[tuple[Path, str]],
    probe: DockerProbe | None,
) -> Mapping[str, str | None]:
    # A root Docker mode would refuse works in Direct mode. ``auto`` must not
    # turn it into a failed build, so check before the daemon is even asked.
    for path, label in mount_roots:
        try:
            refuse_whole_volume_mount(path, label=label)
        except WholeVolumeMountError as exc:
            return dict.fromkeys(worker_types, str(exc))
    images = {
        worker_type: getattr(worker_config, worker_type).image or DEFAULT_WORKER_IMAGES[worker_type]
        for worker_type in worker_types
    }
    return (probe or probe_docker_images)(images)


def fallback_warning_message(worker_type: str, reason: str) -> str:
    """The build warning for one ``auto`` type that could not use Docker."""
    name = _DISPLAY_NAMES.get(worker_type, worker_type)
    return (
        f"{name} diagrams are rendered with the local tool instead of Docker: {reason}. "
        f"Local renders differ from the Docker converter image (tool version, fonts, "
        f"margins), so committed PNGs under img-generated/ may show up as modified. "
        f"If local rendering is what you want, say so to silence this warning: "
        f"--diagram-mode direct or CLM_WORKER_MANAGEMENT__DIAGRAM_MODE=direct."
    )
