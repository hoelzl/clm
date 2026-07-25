"""Bind-address policy for the Worker API.

The Worker API used to bind ``0.0.0.0`` unconditionally, "for Docker access".
That made an unauthenticated, build-driving service reachable from the whole
LAN on every Docker-mode build. This module decides what it binds instead.

The policy has two modes:

**Local mode (the default).** Bind the loopback address, plus — on Linux only
— the gateway addresses of the local Docker bridge networks. Both halves are
needed and neither is redundant:

- On Docker Desktop (Windows/macOS) containers reach ``host.docker.internal``
  through Docker's own network proxy, which forwards to the *host's loopback*.
  Loopback alone is therefore sufficient there, and the bridge gateway is an
  address inside the Docker VM that the host cannot bind at all.
- On Linux there is no such proxy. ``--add-host=host.docker.internal:host-gateway``
  (which :mod:`clm.infrastructure.workers.worker_executor` passes) resolves to
  the bridge gateway, an address on the host's ``docker0``-style interface.
  A service on loopback is invisible to containers; the gateway bind is what
  keeps Docker mode working.

  A bridge gateway is host-local and not routed from the LAN, so this is not a
  way back to the old all-interfaces posture.

**Coordinator mode (explicit opt-in).** An operator who genuinely wants other
machines to reach this queue — the long-term cross-machine story, where one
machine owns the jobs DB and everyone else goes through this API — sets a bind
address explicitly. That path additionally *requires* a pinned token, because
a generated per-build token cannot be handed to another machine. See
:func:`classify_hosts`.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import sys

logger = logging.getLogger(__name__)

#: The address bound in local mode on every platform.
LOOPBACK_HOST = "127.0.0.1"

#: Environment variable that opts into coordinator mode by naming a bind
#: address (e.g. ``0.0.0.0`` or a specific interface IP).
HOST_ENV_VAR = "CLM_WORKER_API_HOST"


def _is_loopback(host: str) -> bool:
    """True when ``host`` names a loopback address."""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.lower() in ("localhost",)


def docker_gateway_hosts() -> list[str]:
    """Return bindable Docker bridge gateway addresses on this host.

    Empty everywhere except Linux — see the module docstring for why the
    other platforms neither need nor can bind these.

    Best-effort by construction: Docker may be absent, the SDK may be
    uninstalled, or the daemon may be unreachable. Any of those simply means
    "no extra addresses", and Docker-mode builds fail later with their own
    clearer error rather than here.
    """
    if sys.platform != "linux":
        return []

    try:
        import docker

        # The paired `unused-ignore` is not redundant: mypy resolves this
        # attribute on Windows but not on Linux, so a bare `attr-defined`
        # ignore passes locally and fails CI (and vice versa). Same shape as
        # `pool_manager.py`'s call.
        client = docker.from_env()  # type: ignore[attr-defined, unused-ignore]
        networks = client.networks.list(filters={"driver": "bridge"})
    except Exception as e:  # docker missing, daemon down, permissions…
        logger.debug(f"Could not enumerate Docker bridge networks: {e}")
        return []

    gateways: list[str] = []
    for network in networks:
        for entry in (network.attrs.get("IPAM") or {}).get("Config") or []:
            gateway = entry.get("Gateway")
            if gateway and gateway not in gateways:
                gateways.append(gateway)

    try:
        client.close()
    except Exception:  # pragma: no cover - close is advisory
        pass

    return gateways


def resolve_bind_hosts(
    explicit_host: str | None,
    *,
    gateways: list[str] | None = None,
) -> list[str]:
    """Return the list of addresses the server should bind.

    Args:
        explicit_host: An operator-supplied address (constructor argument or
            :data:`HOST_ENV_VAR`). When given it is used verbatim and alone —
            an operator who names an address means that address.
        gateways: Pre-discovered Docker gateway addresses, for tests. Defaults
            to calling :func:`docker_gateway_hosts`.

    Returns:
        A de-duplicated list, loopback first.
    """
    if explicit_host:
        return [explicit_host]

    discovered = docker_gateway_hosts() if gateways is None else gateways
    hosts = [LOOPBACK_HOST]
    for gateway in discovered:
        if gateway not in hosts:
            hosts.append(gateway)
    return hosts


def classify_hosts(hosts: list[str], *, gateways: list[str]) -> list[str]:
    """Return the subset of ``hosts`` that reaches beyond this machine.

    Loopback and Docker bridge gateways are local: nothing off this machine
    can route to them. Anything else — a LAN interface address, ``0.0.0.0``,
    ``::`` — is an exposure the operator has to ask for by name, and which
    :class:`~clm.infrastructure.api.server.WorkerApiServer` will refuse to
    serve without a pinned token.
    """
    return [h for h in hosts if not _is_loopback(h) and h not in gateways]


def bind_socket(host: str, port: int) -> socket.socket:
    """Bind and listen on ``(host, port)``, returning the ready socket.

    Binding eagerly — on the caller's thread, before uvicorn starts — is what
    makes "the port is taken" a synchronous error the caller can report,
    instead of a background thread dying after ``start()`` already returned
    True.

    Raises:
        OSError: If the address cannot be bound.
    """
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(2048)
        sock.set_inheritable(True)
    except OSError:
        sock.close()
        raise
    return sock
