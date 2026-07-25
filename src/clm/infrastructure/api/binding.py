"""Bind-address and port policy for the Worker API.

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

**Which port.** The port is not part of any contract between the host and its
containers: the host tells each container where to call back through
``CLM_API_URL``, so whatever port it ends up on is the port the workers use.
:data:`DEFAULT_PORT` is therefore a convenience — a predictable place to point
``curl`` — not a requirement, and when it is already taken CLM moves to an
OS-assigned one rather than failing or, worse, sharing. Naming a port
explicitly (constructor argument or :data:`PORT_ENV_VAR`) pins it: that is a
request, so a collision is an error instead of something to route around. See
:func:`resolve_port` and :func:`bind_socket_with_fallback`.
"""

from __future__ import annotations

import errno
import ipaddress
import logging
import os
import socket
import sys

logger = logging.getLogger(__name__)

#: The address bound in local mode on every platform.
LOOPBACK_HOST = "127.0.0.1"

#: DNS alias every container uses to reach the host (via ``extra_hosts``
#: host-gateway on Linux, Docker Desktop's own proxy elsewhere). Defined here so
#: that the API URL handed to containers, and the ``NO_PROXY`` entry that keeps
#: that traffic away from the replay proxy, cannot drift apart — see
#: :func:`clm.infrastructure.workers.worker_executor._mitmproxy_docker_env`.
DOCKER_HOST_ALIAS = "host.docker.internal"

#: The port bound when nothing asks for a specific one.
DEFAULT_PORT = 8765

#: Passed as a port, this asks the OS for any free port.
EPHEMERAL_PORT = 0

#: Environment variable that opts into coordinator mode by naming a bind
#: address (e.g. ``0.0.0.0`` or a specific interface IP).
HOST_ENV_VAR = "CLM_WORKER_API_HOST"

#: Environment variable that pins the port. ``0`` asks the OS for a free one,
#: which is what the test suite uses to give every test its own server.
PORT_ENV_VAR = "CLM_WORKER_API_PORT"

#: Errno values meaning "that address:port is already bound". Windows reports
#: ``WSAEADDRINUSE`` (10048) rather than the POSIX value, and Python does not
#: translate it, so both have to be recognised.
_ADDRESS_IN_USE = {errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", errno.EADDRINUSE)}


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


def resolve_port(explicit_port: int | None) -> tuple[int, bool]:
    """Return ``(port, pinned)`` for the requested port.

    Args:
        explicit_port: A caller-supplied port, or ``None`` to consult
            :data:`PORT_ENV_VAR` and then :data:`DEFAULT_PORT`.

    Returns:
        The port to bind, and whether it was *pinned* — i.e. somebody named it,
        so failing to get it is an error rather than something to work around.
        :data:`DEFAULT_PORT` reached by default is not pinned.

    Raises:
        ValueError: If :data:`PORT_ENV_VAR` is set to something that is not a
            port number. Silently ignoring it would start a server somewhere
            other than where the operator asked for it.
    """
    if explicit_port is not None:
        return explicit_port, True

    raw = (os.environ.get(PORT_ENV_VAR) or "").strip()
    if not raw:
        return DEFAULT_PORT, False

    try:
        port = int(raw)
    except ValueError:
        raise ValueError(
            f"{PORT_ENV_VAR}={raw!r} is not a port number. Set it to a port to pin the "
            f"Worker API there, to 0 to let the OS pick a free one, or unset it for the "
            f"default {DEFAULT_PORT}."
        ) from None
    if not 0 <= port <= 65535:
        raise ValueError(
            f"{PORT_ENV_VAR}={raw!r} is out of range: a port is 0-65535 (0 = OS-assigned)."
        )
    return port, True


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

    ``SO_REUSEADDR`` is set on POSIX only, and that asymmetry is deliberate.
    The two platforms give the option different meanings: on POSIX it lets a
    listener rebind an address still in ``TIME_WAIT``, while on Windows it lets
    a *second* listener bind an address a first one is still listening on,
    after which which socket receives a given connection is undefined.
    Measured on Windows 11: two sockets bound ``127.0.0.1:18765`` successfully
    with the option set, and the second bind failed with ``WSAEADDRINUSE``
    without it. Since the Worker API hands out jobs, a silent second listener
    means containers registering with a server that owns a different jobs
    database — so on Windows we take the honest error.

    Raises:
        OSError: If the address cannot be bound.
    """
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        if sys.platform != "win32":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(2048)
        sock.set_inheritable(True)
    except OSError:
        sock.close()
        raise
    return sock


def is_address_in_use(error: OSError) -> bool:
    """True when ``error`` means the address:port is already bound."""
    return error.errno in _ADDRESS_IN_USE


def bind_socket_with_fallback(host: str, port: int, *, allow_fallback: bool) -> socket.socket:
    """Bind ``(host, port)``, falling back to an OS-assigned port if allowed.

    Args:
        host: Address to bind.
        port: Preferred port.
        allow_fallback: Whether an already-taken ``port`` may be swapped for an
            OS-assigned one. False when the port was pinned — somebody asked
            for that port, and quietly serving on another one would leave them
            pointing ``curl``, a firewall rule, or a second machine at nothing.

    Returns:
        The bound socket. Call ``getsockname()[1]`` for the port actually taken;
        after a fallback it is not the requested one.

    Raises:
        OSError: If the port is taken and fallback is not allowed, or the bind
            fails for any other reason (a permission problem or an address that
            does not exist on this host is not something another port fixes).
    """
    try:
        return bind_socket(host, port)
    except OSError as e:
        if port == EPHEMERAL_PORT or not allow_fallback or not is_address_in_use(e):
            raise
    sock = bind_socket(host, EPHEMERAL_PORT)
    logger.warning(
        f"Worker API port {port} is already in use — another CLM build, or something "
        f"else, is listening on it. Using {host}:{sock.getsockname()[1]} for this build "
        f"instead; workers are told where to call back, so nothing needs the default "
        f"port. Set {PORT_ENV_VAR} to require a specific one."
    )
    return sock
