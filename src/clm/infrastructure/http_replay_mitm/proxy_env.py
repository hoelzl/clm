"""Proxy environment bookkeeping for the replay proxy (issue #1309).

While a build runs the replay proxy, ``build._maybe_start_mitmproxy_transport``
points the whole host environment at it (Direct workers inherit
``os.environ``), and ``worker_executor._mitmproxy_docker_env`` does the same
per Docker container. Only notebooks whose topic opted into ``http-replay``
get the cassette-routing tag bootstrap, though, so a kernel of any *other*
topic would reach the proxy untagged: its traffic is recorded into the
machine-local catch-all in recording modes and refused with a
``clm_replay_miss`` 404 in strict modes.

The fix keeps those kernels off the proxy. Whoever sets the proxy env also
records, in :data:`PREPROXY_ENV_VAR`, what each overridden variable held
before (``null`` = unset). The notebook worker then launches a kernel of a
non-opted-in topic with :func:`preproxy_kernel_env`, which puts every one of
those variables back exactly as it was — so such a deck sees the network
exactly as it would without the proxy (a user's own corporate proxy
included).

Pure stdlib: imported by the build engine, the Docker executor and the
notebook worker.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping

logger = logging.getLogger(__name__)

#: Marker env var holding a JSON object ``{name: original value | null}`` for
#: every variable the replay-proxy setup overrode. Its presence means "the
#: proxy env is active in this process".
PREPROXY_ENV_VAR = "CLM_HTTP_REPLAY_PREPROXY_ENV"

#: The variables the host-side setup (``build._maybe_start_mitmproxy_transport``)
#: overrides in ``os.environ``.
HOST_PROXY_ENV_KEYS: tuple[str, ...] = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "http_proxy",
    "https_proxy",
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "PIP_DISABLE_PIP_VERSION_CHECK",
)


def encode_preproxy_env(environ: Mapping[str, str], keys: Iterable[str]) -> str:
    """Snapshot the current values of ``keys`` in ``environ`` as marker JSON."""
    return json.dumps({key: environ.get(key) for key in keys}, sort_keys=True)


def preproxy_kernel_env(environ: Mapping[str, str]) -> dict[str, str] | None:
    """The environment for a kernel that must NOT use the replay proxy.

    Returns ``None`` when ``environ`` carries no :data:`PREPROXY_ENV_VAR`
    marker (no proxy is active, so the kernel may simply inherit). Otherwise
    returns a copy of ``environ`` with every recorded variable restored to its
    pre-proxy value (removed if it was unset) and the marker itself dropped.

    A malformed marker is logged and treated as "every recorded key was
    unset" for the keys we know about: failing safe here means removing the
    proxy, never keeping it.
    """
    raw = environ.get(PREPROXY_ENV_VAR)
    if raw is None:
        return None
    try:
        originals = json.loads(raw)
        if not isinstance(originals, dict):
            raise ValueError("marker is not a JSON object")
    except ValueError as exc:
        logger.warning(
            "Malformed %s (%s); launching the kernel with the replay-proxy variables removed",
            PREPROXY_ENV_VAR,
            exc,
        )
        originals = dict.fromkeys(HOST_PROXY_ENV_KEYS)

    env = dict(environ)
    env.pop(PREPROXY_ENV_VAR, None)
    for key, value in originals.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = str(value)
    return env
