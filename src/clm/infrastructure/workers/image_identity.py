"""Effective worker-image identity for cache keys (issue #744).

The cache key must describe the environment that actually executes a job
(issue #321 class 5). Two things used to break that for images:

- **CLI overrides were invisible**: ``load_worker_config`` applies
  ``--notebook-image``/``--plantuml-image``/``--drawio-image`` to a deep
  *copy* of the config (the deliberate #223 copy), while the identity
  computation read the untouched global singleton — a build executing on
  the override image keyed its cache as the default image.
- **Diagram payloads had no image component at all** — a rebuilt converter
  image replayed the old image's bytes for every unchanged source.

``clm build`` records the post-override identities here
(:func:`set_effective_worker_identities`); the per-type accessor falls back
to the global singleton for callers outside a build (tests, tools), which
matches the pre-#744 behavior when no override exists.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clm.infrastructure.config import WorkersManagementConfig

logger = logging.getLogger(__name__)

_WORKER_TYPES = ("notebook", "plantuml", "drawio")

#: Post-override identity per worker type, recorded by ``clm build``.
_effective: dict[str, str] = {}


def worker_image_identity_for(
    execution_mode: str, image: str | None, worker_type: str = "notebook"
) -> str:
    """The environment identity string for one worker type.

    - ``direct`` mode → ``"direct"`` for notebooks (the host environment's
      version/template content is already covered by
      ``compute_template_fingerprint``); for the diagram types
      ``"direct:<binary fingerprint>"`` (#747) — a PlantUML-JAR or Draw.io
      upgrade must invalidate the diagram caches the same way a Docker
      image switch does. An unlocatable binary degrades to plain
      ``"direct"`` (the build then fails at worker startup anyway).
    - ``docker`` mode → ``"docker:<image>"`` with the same effective-image
      resolution the pool starter uses (per-type override, else the bundled
      default) — the key must describe the image that actually executes.
    """
    if execution_mode != "docker":
        if worker_type in ("plantuml", "drawio"):
            fingerprint = _direct_binary_fingerprint(worker_type)
            if fingerprint:
                return f"direct:{fingerprint}"
        return "direct"
    from clm.infrastructure.config import DEFAULT_WORKER_IMAGES

    effective = image or DEFAULT_WORKER_IMAGES.get(worker_type, "")
    return f"docker:{effective}"


def _direct_binary_fingerprint(worker_type: str) -> str:
    """A cheap host-side fingerprint of the direct-mode diagram binary.

    Path + size + mtime_ns, digested — no execution, two stat calls.
    Resolution follows the worker executor's injection precedence
    (``external_tools`` config with the env vars folded over it, then the
    workers' own default resolution via
    :mod:`clm.workers.diagram_tools`), so the fingerprint describes the
    binary that will actually render — including one configured only in a
    config file's ``[external_tools]`` section (#747 review F1). Residue:
    a binary replaced in place with identical size and mtime keeps the
    key (the same trade every size+mtime scheme makes); the bare DEFAULT
    Draw.io name is which()-resolved for statting while the spawn
    resolves at exec time — a PATHEXT shim shadowing the real .exe can
    make the two diverge (spurious re-render at worst) — and an env- or
    config-set bare name is stat'd directly, fails, and degrades to
    identity-less keying (a knowingly-open corner). Defensive ``""``
    on any error — identity degrades to ``"direct"``.
    """
    import hashlib
    import os

    try:
        # The executor injects PLANTUML_JAR/DRAWIO_EXECUTABLE into direct
        # workers from get_config().external_tools (env folded over the
        # config file) — mirror that exactly, then fall back to the
        # workers' own default resolution.
        from clm.infrastructure.config import get_config, resolve_setting
        from clm.workers.diagram_tools import (
            locate_drawio_executable,
            locate_plantuml_jar,
        )

        external_tools = get_config().external_tools
        if worker_type == "plantuml":
            configured = resolve_setting(None, config_value=external_tools.plantuml_jar, default="")
            located = configured or locate_plantuml_jar()
        else:
            configured = resolve_setting(
                None, config_value=external_tools.drawio_executable, default=""
            )
            located = configured or locate_drawio_executable()
        if not located:
            return ""
        stat = os.stat(located)
        raw = f"{located}:{stat.st_size}:{stat.st_mtime_ns}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    except Exception:  # noqa: BLE001 - identity must never fail a build
        return ""


def set_effective_worker_identities(worker_config: WorkersManagementConfig) -> None:
    """Record the post-override execution identity of every worker type.

    Called by ``clm build`` right after ``load_worker_config`` resolved the
    CLI overrides into its config copy — from here on the cache keys see
    the images that will actually execute, not the singleton's view.
    Wholesale replacement: one build, one set of identities.
    """
    for worker_type in _WORKER_TYPES:
        type_config = getattr(worker_config, worker_type)
        execution_mode = type_config.execution_mode or worker_config.default_execution_mode
        _effective[worker_type] = worker_image_identity_for(
            execution_mode, type_config.image, worker_type
        )
    logger.debug(f"Effective worker image identities: {_effective}")


def reset_effective_worker_identities() -> None:
    """Drop recorded identities (tests) — accessors fall back to the singleton."""
    _effective.clear()


def effective_worker_image_identity(worker_type: str) -> str:
    """Identity of the environment ``worker_type`` jobs will run in.

    Computed HOST-side at payload construction and folded into the cache
    keys. Prefers the identities a build recorded via
    :func:`set_effective_worker_identities`; falls back to the global
    config singleton otherwise.

    Limitation: this is the configured image *reference*, not a content
    digest — a mutable tag (``:latest``) re-pulled to a new image does NOT
    change the key; pin worker images to versioned tags or digests for the
    invalidation to be exact.

    Defensive ``""`` on any config error: a payload must never fail to
    build because worker config is unreadable; an empty identity merely
    reverts that build to identity-less keying for this component.
    """
    recorded = _effective.get(worker_type)
    if recorded is not None:
        return recorded
    try:
        from clm.infrastructure.config import get_config

        worker_management = get_config().worker_management
        type_config = getattr(worker_management, worker_type)
        execution_mode = type_config.execution_mode or worker_management.default_execution_mode
        return worker_image_identity_for(execution_mode, type_config.image, worker_type)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"Could not resolve worker image identity for cache key: {exc}")
        return ""
