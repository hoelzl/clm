"""Effective worker-image identity registry (Phase 8 S4, #802).

Core operations stamp the execution-environment identity into worker
payloads at build time — it is a cache-key component (issues #321/#744).
But *computing* an identity is infrastructure work: Docker image
resolution and direct-mode binary fingerprinting over the config
singleton. This module holds only the seam between the two:

- ``clm build`` / ``clm cache explain`` record the post-override
  identities via :mod:`clm.infrastructure.workers.image_identity`, which
  writes them here;
- that same infrastructure module registers itself as the **fallback
  provider** (both at its own import and lazily via
  ``clm.infrastructure.__init__``), so callers outside a build (tests,
  tools) still resolve the singleton-config identity exactly as before
  #802/S4;
- core reads via :func:`effective_worker_image_identity` and never
  imports upward.

With neither a recording nor a provider (a bare-core process that never
touched infrastructure), the accessor degrades to identity-less keying
(``""``) — the same defensive contract the resolver already had for
unreadable worker config.
"""

import logging
from collections.abc import Callable, Mapping

logger = logging.getLogger(__name__)

#: Post-override identity per worker type, recorded by ``clm build`` (and
#: ``clm cache explain``) through the infrastructure resolver.
_effective: dict[str, str] = {}

_fallback_provider: Callable[[str], str] | None = None


def set_effective_worker_identities(identities: Mapping[str, str]) -> None:
    """Wholesale-replace the recorded identities (one build, one set)."""
    _effective.clear()
    _effective.update(identities)
    logger.debug(f"Effective worker image identities: {_effective}")


def reset_effective_worker_identities() -> None:
    """Drop recorded identities (tests) — accessors fall back to the provider."""
    _effective.clear()


def register_fallback_provider(provider: Callable[[str], str]) -> None:
    """Install the resolver used when no identity was recorded.

    Called by infrastructure at import time; last registration wins
    (idempotent in practice — both registration paths install the same
    singleton-config resolver).
    """
    global _fallback_provider
    _fallback_provider = provider


def effective_worker_image_identity(worker_type: str) -> str:
    """Identity of the environment ``worker_type`` jobs will run in.

    Computed HOST-side at payload construction and folded into the cache
    keys. Prefers the identities a build recorded via
    :func:`set_effective_worker_identities`; falls back to the registered
    provider (the singleton-config resolver) otherwise.

    Limitation: this is the configured image *reference*, not a content
    digest — a mutable tag (``:latest``) re-pulled to a new image does NOT
    change the key; pin worker images to versioned tags or digests for the
    invalidation to be exact.

    Defensive ``""`` when nothing is recorded and no provider is
    registered: a payload must never fail to build; an empty identity
    merely reverts that build to identity-less keying for this component.
    """
    recorded = _effective.get(worker_type)
    if recorded is not None:
        return recorded
    if _fallback_provider is None:
        logger.warning(
            f"No worker-identity provider registered (worker_type={worker_type!r}); "
            f"using identity-less cache keying. Import clm.infrastructure to "
            f"register the singleton-config resolver."
        )
        return ""
    return _fallback_provider(worker_type)
