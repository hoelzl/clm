"""Smoke tests for the mitmproxy-based HTTP-replay prototype.

These tests exercise the proxy manager + addon round-trip against a
local HTTP server, validating:

* the proxy starts, accepts connections, and routes traffic through to
  upstream (record mode);
* responses are persisted to the cassette and served from it on a
  subsequent run (replay mode);
* strict ``replay`` mode returns a deterministic 599 on cache miss
  instead of escaping to the network.

mitmproxy runs out-of-process (settled production model: ``uv tool install
mitmproxy``), so it is NOT imported in-process here — these tests only need
``mitmdump`` reachable as a subprocess and ``requests`` in the test env. The
module is skipped when ``mitmdump`` can't be located (see ``_locate_mitmdump``
guard below) or ``requests`` isn't installed.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

requests = pytest.importorskip("requests")

from clm.infrastructure.http_replay_mitm import MitmproxyManager
from clm.infrastructure.http_replay_mitm.proxy_manager import (
    MitmproxyError,
    _locate_mitmdump,
)

# Skip the whole module if mitmdump isn't reachable — the [mitmproxy]
# extra installs the Python package but the executable lookup may still
# fail on some environments.
try:
    _locate_mitmdump()
except MitmproxyError:
    pytest.skip("mitmdump executable not available", allow_module_level=True)


class _CountingHandler(BaseHTTPRequestHandler):
    """Tiny HTTP handler that counts hits and echoes a fixed body.

    The class-level counter lets the test distinguish "request reached
    the upstream server" from "request was served from cassette".
    """

    upstream_hits = 0

    def do_GET(self) -> None:  # noqa: N802 — required by BaseHTTPRequestHandler
        type(self).upstream_hits += 1
        body = json.dumps({"path": self.path, "hit": type(self).upstream_hits}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args, **_kwargs) -> None:  # silence stderr noise
        return


@pytest.fixture
def upstream_server() -> Iterator[str]:
    """Run a localhost HTTP server in a thread for the duration of one test."""
    _CountingHandler.upstream_hits = 0
    server = HTTPServer(("127.0.0.1", 0), _CountingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[0], server.server_address[1]
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


@pytest.fixture
def cassette_path(tmp_path: Path) -> Path:
    return tmp_path / "smoke.http-cassette.yaml"


def _get_via_proxy(url: str, proxy_url: str) -> requests.Response:
    return requests.get(
        url,
        proxies={"http": proxy_url, "https": proxy_url},
        # mitmproxy intercepts HTTP cleanly; HTTPS would need CA trust
        # set up. The smoke test stays on HTTP for simplicity — see the
        # design doc for the HTTPS story.
        timeout=10.0,
    )


def test_proxy_starts_and_routes_traffic(
    upstream_server: str, cassette_path: Path, tmp_path: Path
) -> None:
    """Record-mode: traffic flows upstream and is persisted to the cassette."""
    confdir = tmp_path / "mitm-confdir"
    with MitmproxyManager(
        cassette_path=cassette_path, mode="new-episodes", confdir=confdir
    ) as proxy:
        response = _get_via_proxy(f"{upstream_server}/hello", proxy.proxy_url)

    assert response.status_code == 200
    assert response.json()["path"] == "/hello"
    assert _CountingHandler.upstream_hits == 1
    assert cassette_path.exists()
    assert cassette_path.stat().st_size > 0


def test_replay_serves_from_cassette_without_upstream_hit(
    upstream_server: str, cassette_path: Path, tmp_path: Path
) -> None:
    """Record once, then a fresh replay-mode proxy serves the request without
    touching the upstream server."""
    confdir = tmp_path / "mitm-confdir"
    target = f"{upstream_server}/cached"

    # 1. Record.
    with MitmproxyManager(
        cassette_path=cassette_path, mode="new-episodes", confdir=confdir
    ) as proxy:
        first = _get_via_proxy(target, proxy.proxy_url)
    assert first.status_code == 200
    assert _CountingHandler.upstream_hits == 1

    # 2. Replay against the same cassette. No upstream hits should
    # occur; the response payload should match what we recorded.
    with MitmproxyManager(cassette_path=cassette_path, mode="replay", confdir=confdir) as proxy:
        replayed = _get_via_proxy(target, proxy.proxy_url)
    assert replayed.status_code == 200
    assert replayed.json() == first.json()
    assert _CountingHandler.upstream_hits == 1, "replay-mode request should not reach upstream"


def test_strict_replay_miss_returns_599(
    upstream_server: str, cassette_path: Path, tmp_path: Path
) -> None:
    """A request not in the cassette returns the addon's diagnostic 599
    rather than escaping to upstream."""
    confdir = tmp_path / "mitm-confdir"

    # Seed the cassette with one URL.
    with MitmproxyManager(
        cassette_path=cassette_path, mode="new-episodes", confdir=confdir
    ) as proxy:
        _get_via_proxy(f"{upstream_server}/recorded", proxy.proxy_url)
    assert _CountingHandler.upstream_hits == 1

    # Now request a DIFFERENT URL in strict replay mode. The addon
    # should synthesize a 599 and the upstream counter should NOT
    # advance.
    with MitmproxyManager(cassette_path=cassette_path, mode="replay", confdir=confdir) as proxy:
        response = _get_via_proxy(f"{upstream_server}/never-recorded", proxy.proxy_url)

    assert response.status_code == 599
    payload = response.json()
    assert payload["error"] == "clm_replay_miss"
    assert payload["method"] == "GET"
    assert payload["url"].endswith("/never-recorded")
    assert _CountingHandler.upstream_hits == 1, (
        "strict-replay miss must not fall through to upstream"
    )


def test_env_vars_exposes_proxy_url(cassette_path: Path, tmp_path: Path) -> None:
    """The env_vars dict has the four HTTP_PROXY variants workers need."""
    confdir = tmp_path / "mitm-confdir"
    with MitmproxyManager(
        cassette_path=cassette_path, mode="new-episodes", confdir=confdir
    ) as proxy:
        env = proxy.env_vars()
    assert env["HTTP_PROXY"] == proxy.proxy_url
    assert env["HTTPS_PROXY"] == proxy.proxy_url
    assert env["http_proxy"] == proxy.proxy_url
    assert env["https_proxy"] == proxy.proxy_url
    assert "SSL_CERT_FILE" not in env

    env_with_ca = proxy.env_vars(include_ca=True)
    assert "SSL_CERT_FILE" in env_with_ca
    assert "REQUESTS_CA_BUNDLE" in env_with_ca
