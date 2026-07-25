"""Browser-facing containment for CLM's local web apps.

Its one caller today is the **recordings dashboard** (``clm recordings
serve``), which has no login at all: it treated "the request arrived on the
socket" as "the user asked for this". A web page open in the same browser can
send a request to ``http://127.0.0.1:8008`` without any cooperation from the
app, so that assumption is wrong twice over:

**CSRF.** A page on ``https://evil.example`` can auto-submit a form at any of
the dashboard's ``POST`` routes. No token, no cookie, no user interaction —
form posts are not subject to the same-origin *response* restriction, and the
attacker does not need to read the response to cause the side effect.

**DNS rebinding.** The origin check alone is not enough: an attacker who
controls DNS for ``evil.example`` can point it at ``127.0.0.1``, at which
point their page's requests to ``http://evil.example:8008`` are *genuinely
same-origin* and pass every origin check. What they cannot fake is the
``Host`` header, which still reads ``evil.example`` — so the host allowlist
is what closes this door.

Hence the two middlewares here, which are meant to be installed together:

- :class:`TrustedHostMiddleware` — the request's ``Host`` must name this
  server (loopback by default, plus whatever the operator bound or allowed).
- :class:`OriginGuardMiddleware` — a *mutating* request must come from this
  app's own origin, judged by ``Sec-Fetch-Site`` first and ``Origin`` second.

Both are deliberately zero-friction: no token, no per-form nonce, nothing for
the user to configure in the common case. That is the point — a protection
that costs the single-user local workflow something would be turned off.

**What this does not do.** It does not protect against another process, or
another local user, on the same machine: they can set any header they like.
That is an accepted limit (decision D4 of the 2026-07-24 adversarial review),
not an oversight. Requests carrying *neither* ``Origin`` nor
``Sec-Fetch-Site`` are allowed through for the same reason — that is what
``curl``, a script, or a test client looks like, and none of them are the
threat model. Every browser CLM's dashboards support sends ``Origin`` on a
cross-origin form post.

Written to be app-agnostic because ``clm serve`` needs the same two guards
(its dashboard API is unauthenticated, and its WebSocket accepts anyone) — but
until that is wired, nothing here should be read as a claim about it. CLM's
third HTTP app, the Worker API in :mod:`clm.infrastructure.api`, protects
itself differently: a per-build bearer token on every route.

Why hand-rolled rather than Starlette's ``TrustedHostMiddleware``: that one
derives the host as ``header.split(":")[0]``, which turns ``[::1]:8000`` into
``"["`` and rejects anyone browsing over IPv6 loopback. Since the whole point
here is a *default-on* check, a default that breaks a legitimate local URL is
not acceptable.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

#: HTTP methods that cannot change server state, and so are exempt from the
#: origin check. ``OPTIONS`` is included because a CORS preflight must be
#: answerable — a rejected preflight tells the browser nothing useful, and the
#: actual request behind it is still checked.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

#: Host names that always name this machine.
LOOPBACK_HOSTS: tuple[str, ...] = ("localhost", "127.0.0.1", "::1")

#: Default scheme ports, stripped when comparing an ``Origin`` to a ``Host``.
_DEFAULT_PORTS = {"http": "80", "https": "443", "ws": "80", "wss": "443"}


def extract_host(host_header: str) -> str:
    """Return the lower-cased host from a ``Host`` header, without the port.

    Handles the bracketed IPv6 form (``[::1]:8000`` → ``::1``) that Starlette's
    own splitter mangles, and tolerates a missing port.

    Args:
        host_header: Raw ``Host`` header value (may be empty).

    Returns:
        The bare host, lower-cased. Empty when the header was empty.
    """
    value = host_header.strip()
    if not value:
        return ""
    if value.startswith("["):
        end = value.find("]")
        if end != -1:
            return value[1:end].lower()
        return value.lower()
    if value.count(":") == 1:
        host = value.rsplit(":", 1)[0].lower()
    else:
        # No port, or a bare (invalid) IPv6 literal — either way, use it verbatim.
        host = value.lower()
    return _strip_trailing_dot(host)


def _strip_trailing_dot(host: str) -> str:
    """Drop one trailing dot from ``host``.

    ``http://localhost./`` is a legal URL (an explicitly-absolute FQDN) and the
    browser sends the dot through, so treating it as a different host would
    400 somebody who typed a legitimate address. Exactly one dot is stripped,
    so ``localhost..`` — which names nothing — stays refused.
    """
    return host[:-1] if host.endswith(".") and len(host) > 1 else host


def normalize_host_pattern(pattern: str) -> str:
    """Return ``pattern`` in the form :func:`extract_host` produces.

    Lets an operator write ``[::1]``, ``::1``, or ``localhost:8008`` and have
    all of them mean what they obviously mean.
    """
    return extract_host(pattern)


def default_allowed_hosts(bind_host: str | None = None, extra: Iterable[str] = ()) -> list[str]:
    """Return the host allowlist for a server bound at ``bind_host``.

    Args:
        bind_host: The address passed to ``--host``. A wildcard bind
            (``0.0.0.0`` / ``::``) contributes nothing by itself — it names
            every interface, not a host somebody types — so an operator who
            binds wide and reaches the app under a LAN name or Tailscale
            hostname has to name that host via ``extra``.
        extra: Additional allowed hosts (``--allowed-host``). A single ``"*"``
            disables the check entirely.

    Returns:
        De-duplicated, normalized host patterns, loopback first.
    """
    hosts: list[str] = list(LOOPBACK_HOSTS)
    candidates = [bind_host] if bind_host else []
    candidates.extend(extra)
    for candidate in candidates:
        if not candidate:
            continue
        if candidate == "*":
            return ["*"]
        normalized = normalize_host_pattern(candidate)
        if normalized in ("0.0.0.0", "::", ""):
            continue
        if normalized not in hosts:
            hosts.append(normalized)
    return hosts


def remote_access_warning(bind_host: str | None, extra: Iterable[str] = ()) -> str | None:
    """Return a warning when the bind address promises more reach than the allowlist.

    Binding ``0.0.0.0`` says "I want other machines to reach this", but the
    host allowlist has no way to learn the *name* those machines will use, so
    every one of their requests gets ``400 Invalid host header``. Without this
    warning the symptom is a dashboard that appears to start normally and then
    refuses everything remote, with the explanation only in a server log the
    user is not watching.

    Args:
        bind_host: The address about to be bound.
        extra: Operator-supplied allowed hosts.

    Returns:
        A message to print, or ``None`` when the configuration is coherent.
    """
    extras = [e for e in extra if e]
    if extras:
        return None
    if not bind_host:
        return None
    host = normalize_host_pattern(bind_host)
    if host in LOOPBACK_HOSTS:
        return None
    if host in ("0.0.0.0", "::"):
        return (
            f"Binding {bind_host} exposes this server to the network, but it will "
            f"only answer to {', '.join(LOOPBACK_HOSTS)} — every request from "
            f"another machine gets '400 Invalid host header'. Add "
            f"--allowed-host <name-or-ip> for each address you will use to reach it "
            f"(or --allowed-host '*' to accept any)."
        )
    return (
        f"This server will answer to {host} and loopback only. If you reach it "
        f"under a different name (a hostname rather than the bare address), add "
        f"--allowed-host <name>."
    )


def host_is_allowed(host: str, allowed: Sequence[str]) -> bool:
    """True when ``host`` matches one of the ``allowed`` patterns.

    Supports a leading-``*.`` wildcard (``*.ts.net``) and the bare ``*``
    escape hatch, matching the vocabulary Starlette established.
    """
    if "*" in allowed:
        return True
    for pattern in allowed:
        if pattern.startswith("*.") and host.endswith(pattern[1:]):
            return True
        if host == pattern:
            return True
    return False


def normalize_origin(origin: str) -> str | None:
    """Return ``origin`` as ``scheme://host[:port]`` with default ports dropped.

    Returns ``None`` for ``"null"`` and anything unparseable — both of which
    are *not* this app's origin, which is the only question being asked.
    """
    value = origin.strip()
    if not value or value.lower() == "null":
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        # ``http://[::1`` and friends. An unparseable Origin is not this app's
        # origin, which is the only question — but letting the ValueError out
        # would turn a 403 into a 500 from inside the security middleware.
        return None
    if not parts.scheme or not parts.hostname:
        return None
    scheme = parts.scheme.lower()
    host = _strip_trailing_dot(parts.hostname.lower())
    if ":" in host:
        # ``urlsplit`` strips the brackets off an IPv6 literal; putting them
        # back keeps the result a parseable authority rather than the
        # ambiguous ``http://::1:8008``.
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        return None
    if port is None or str(port) == _DEFAULT_PORTS.get(scheme):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def _origin_matches_host(origin: str, host_header: str) -> bool:
    """True when ``origin``'s authority is the one the request was sent to.

    Compares host and port, deliberately ignoring the scheme: a reverse proxy
    terminating TLS (``tailscale serve``) forwards ``Origin: https://…`` to a
    plain-HTTP upstream, and rejecting that would break the documented remote
    access path for no security gain — the check that matters is *which
    authority the browser thinks it is talking to*.
    """
    try:
        parts = urlsplit(origin.strip())
    except ValueError:
        return False
    if not parts.hostname:
        return False
    try:
        origin_port = parts.port
    except ValueError:
        return False
    # Same trailing-dot normalization the host side does, so a request made
    # through the legal FQDN form ``http://localhost./`` is not refused by one
    # guard after being accepted by the other.
    origin_host = _strip_trailing_dot(parts.hostname.lower())
    if origin_port is None:
        origin_port_str = _DEFAULT_PORTS.get(parts.scheme.lower(), "")
    else:
        origin_port_str = str(origin_port)

    target = host_header.strip()
    target_host = extract_host(target)
    if target.startswith("["):
        end = target.find("]")
        rest = target[end + 1 :] if end != -1 else ""
        target_port_str = rest[1:] if rest.startswith(":") else ""
    elif target.count(":") == 1:
        target_port_str = target.rsplit(":", 1)[1]
    else:
        target_port_str = ""

    if origin_host != target_host:
        return False
    if not target_port_str:
        # No port in Host means the client used the default port for whatever
        # scheme it spoke, and we cannot see the scheme through a proxy. Accept
        # only an origin that is *itself* on a default port: that keeps the
        # ``tailscale serve`` case (Origin https://box.ts.net / Host box.ts.net)
        # working, while refusing a dev server on localhost:3000 driving a
        # dashboard reached as plain ``localhost``.
        return origin_port is None
    return origin_port_str == target_port_str


def check_request_origin(
    headers: Headers,
    *,
    allowed_origins: Sequence[str] = (),
) -> str | None:
    """Decide whether a mutating request may proceed.

    Args:
        headers: The request headers.
        allowed_origins: Extra origins to accept verbatim (``--allowed-origin``),
            already normalized by :func:`normalize_origin`.

    Returns:
        ``None`` when the request may proceed, or a short human-readable reason
        it was refused (safe to log and to return in the 403 body — it echoes
        only what the *client* sent).
    """
    fetch_site = headers.get("sec-fetch-site")
    if fetch_site:
        site = fetch_site.strip().lower()
        # "none" means a user-initiated navigation (typed URL, bookmark);
        # "same-origin" is the app's own fetch/HTMX traffic. Everything else —
        # "same-site" included, since a different port is a different app —
        # is somebody else's page driving this one.
        if site in ("same-origin", "none"):
            return None
        return f"cross-origin request (Sec-Fetch-Site: {site})"

    origin = headers.get("origin")
    if not origin:
        # No Origin and no Sec-Fetch-Site: not a browser. See module docstring.
        return None

    normalized = normalize_origin(origin)
    if normalized is not None and normalized in allowed_origins:
        return None
    if _origin_matches_host(origin, headers.get("host", "")):
        return None
    return f"cross-origin request (Origin: {origin})"


class TrustedHostMiddleware:
    """Reject requests whose ``Host`` header does not name this server.

    Closes the DNS-rebinding path described in the module docstring. Applies
    to WebSocket handshakes as well as HTTP, because a rebound page can open a
    WebSocket just as easily as it can POST.
    """

    def __init__(self, app: ASGIApp, *, allowed_hosts: Sequence[str]) -> None:
        self.app = app
        self.allowed_hosts = [normalize_host_pattern(h) if h != "*" else "*" for h in allowed_hosts]
        self.allow_any = "*" in self.allowed_hosts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.allow_any or scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        host = extract_host(headers.get("host", ""))
        if host_is_allowed(host, self.allowed_hosts):
            await self.app(scope, receive, send)
            return

        logger.warning(
            "Refused a request with Host %r: not in the allowed hosts %s. "
            "If you reach this server under that name on purpose, pass it with "
            "--allowed-host.",
            host,
            self.allowed_hosts,
        )
        # The remediation belongs in the *body*: the person hitting this is
        # looking at a browser, not at the server's log. The allowlist itself
        # stays in the log — under DNS rebinding, the page reading this body is
        # the attacker's, and the operator's Tailscale or LAN names are not
        # something to hand it.
        await _reject(
            scope,
            receive,
            send,
            status_code=400,
            detail=(
                f"Invalid host header: {host!r}.\n"
                f"Restart the server with --allowed-host {host or '<name>'} "
                f"to accept this name."
            ),
        )


class OriginGuardMiddleware:
    """Reject mutating requests that come from another origin.

    Read-only requests (:data:`SAFE_METHODS`) pass untouched — the browser's
    own same-origin policy already keeps their *responses* away from an
    attacker page, and blocking them would break ordinary navigation.

    Implemented as raw ASGI rather than ``BaseHTTPMiddleware`` on purpose: the
    recordings dashboard streams Server-Sent Events, and wrapping a long-lived
    streaming response in ``BaseHTTPMiddleware`` is exactly the case that
    middleware handles badly.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        allowed_origins: Sequence[str] = (),
    ) -> None:
        self.app = app
        normalized = [normalize_origin(o) for o in allowed_origins]
        self.allowed_origins = [o for o in normalized if o is not None]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type = scope["type"]
        if scope_type == "http":
            if scope.get("method", "GET").upper() in SAFE_METHODS:
                await self.app(scope, receive, send)
                return
        elif scope_type != "websocket":
            await self.app(scope, receive, send)
            return

        reason = check_request_origin(Headers(scope=scope), allowed_origins=self.allowed_origins)
        if reason is None:
            await self.app(scope, receive, send)
            return

        logger.warning(
            "Refused %s %s: %s. Only this app's own pages may drive it; "
            "pass --allowed-origin if another origin should be trusted.",
            scope.get("method", scope_type),
            scope.get("path", ""),
            reason,
        )
        await _reject(scope, receive, send, status_code=403, detail=f"Refused {reason}")


async def _reject(
    scope: Scope,
    receive: Receive,
    send: Send,
    *,
    status_code: int,
    detail: str,
) -> None:
    """Answer ``scope`` with a refusal, in whichever protocol it speaks.

    ``status_code`` and ``detail`` apply to HTTP only. A refused handshake has
    no equivalent: both refusals close with 1008 (policy violation), so a
    WebSocket client cannot tell a bad ``Host`` from a bad ``Origin``. That is
    deliberate — the distinction is in the server log, and the client is by
    definition one we are refusing to talk to.
    """
    if scope["type"] == "websocket":
        # ASGI lets a handshake be refused by closing before accepting; uvicorn
        # turns that into an HTTP 403. The connect message has to be consumed
        # first or the server has nothing to close.
        try:
            await receive()
        except Exception:  # pragma: no cover - client vanished mid-handshake
            return
        await send({"type": "websocket.close", "code": 1008})
        return
    response = PlainTextResponse(detail, status_code=status_code)
    await response(scope, receive, send)


def install_web_security(
    app,
    *,
    bind_host: str | None = None,
    allowed_hosts: Iterable[str] | None = None,
    allowed_origins: Iterable[str] = (),
) -> list[str]:
    """Install both guards on ``app`` and return the effective host allowlist.

    Args:
        app: The FastAPI/Starlette application.
        bind_host: The address the server binds, folded into the allowlist.
        allowed_hosts: Operator-supplied additions (``--allowed-host``). ``["*"]``
            disables the host check.
        allowed_origins: Operator-supplied additional origins
            (``--allowed-origin``).

    Returns:
        The host allowlist actually installed, for logging.
    """
    hosts = default_allowed_hosts(bind_host, allowed_hosts or ())
    # add_middleware prepends, so the host check ends up outermost: a rebound
    # request is refused before anything else looks at it.
    app.add_middleware(OriginGuardMiddleware, allowed_origins=list(allowed_origins))
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    return hosts
