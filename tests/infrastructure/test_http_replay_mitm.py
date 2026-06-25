"""Smoke tests for the mitmproxy-based HTTP-replay prototype.

These tests exercise the proxy manager + addon round-trip against a
local HTTP server, validating:

* the proxy starts, accepts connections, and routes traffic through to
  upstream (record mode);
* responses are persisted to the cassette and served from it on a
  subsequent run (replay mode);
* strict ``replay`` mode returns a deterministic non-retryable 404 on cache
  miss instead of escaping to the network.

mitmproxy runs out-of-process (settled production model: ``uv tool install
mitmproxy``), so it is NOT imported in-process here — these tests only need
``mitmdump`` reachable as a subprocess and ``requests`` in the test env. The
module is skipped when ``mitmdump`` can't be located (see ``_locate_mitmdump``
guard below) or ``requests`` isn't installed.
"""

from __future__ import annotations

import json
import logging
import threading
import time
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

# Every test here launches a real mitmdump subprocess (proxy startup + port
# bind) against the parked out-of-process mitmproxy replay PROTOTYPE. Two marks:
#   * ``integration`` excludes the module from the per-commit fast suite — ~11
#     real-subprocess round-trips (~30s) of churn that a parked prototype does
#     not need to re-prove on every local commit. CI never installs mitmproxy,
#     so these already only run on a dev box; ``integration`` makes that
#     explicit (run on demand via ``pytest -m integration`` or directly by
#     path), and the #184 mitmdump contention/flakiness surface leaves the
#     commit path entirely.
#   * ``serial("subproc")`` still pins them onto one xdist worker whenever they
#     ARE run in parallel, so the mitmdump spawns don't race each other (they
#     share the ``subproc`` load group with the other subprocess-spawning
#     tests). See the ``serial`` marker in pyproject and its xdist_group mapping
#     in ``tests/conftest.py``.
pytestmark = [pytest.mark.serial("subproc"), pytest.mark.integration]

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
    # Records the (method, path, headers) the upstream actually saw — used by
    # tests that need to assert what reached the network (e.g. the routing tag
    # was stripped before forwarding upstream).
    last_seen: dict | None = None

    def _respond(self, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 — required by BaseHTTPRequestHandler
        type(self).upstream_hits += 1
        type(self).last_seen = {"method": "GET", "path": self.path, "headers": dict(self.headers)}
        # Echo only the path without the query string: a real API never reflects
        # your api_key/token query params back into its response body (and
        # responses are not secret-filtered), so reflecting them here would be
        # an unrealistic test-only secret leak.
        self._respond({"path": self.path.split("?", 1)[0], "hit": type(self).upstream_hits})

    def do_POST(self) -> None:  # noqa: N802 — required by BaseHTTPRequestHandler
        type(self).upstream_hits += 1
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        type(self).last_seen = {
            "method": "POST",
            "path": self.path,
            "headers": dict(self.headers),
            "body": raw.decode("utf-8", errors="replace"),
        }
        self._respond({"path": self.path.split("?", 1)[0], "hit": type(self).upstream_hits})

    def log_message(self, *_args, **_kwargs) -> None:  # silence stderr noise
        return


@pytest.fixture
def upstream_server() -> Iterator[str]:
    """Run a localhost HTTP server in a thread for the duration of one test."""
    _CountingHandler.upstream_hits = 0
    _CountingHandler.last_seen = None
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


def _get_via_proxy(
    url: str,
    proxy_url: str,
    tag: str | None = None,
    extra_headers: dict | None = None,
) -> requests.Response:
    headers = dict(extra_headers or {})
    if tag is not None:
        headers["X-CLM-Cassette"] = tag
    return requests.get(
        url,
        headers=headers or None,
        proxies={"http": proxy_url, "https": proxy_url},
        # mitmproxy intercepts HTTP cleanly; HTTPS would need CA trust
        # set up. The smoke test stays on HTTP for simplicity — see the
        # design doc for the HTTPS story.
        timeout=10.0,
    )


def _post_json_via_proxy(
    url: str,
    proxy_url: str,
    body: bytes,
    tag: str | None = None,
) -> requests.Response:
    headers = {"Content-Type": "application/json"}
    if tag is not None:
        headers["X-CLM-Cassette"] = tag
    return requests.post(
        url,
        data=body,
        headers=headers,
        proxies={"http": proxy_url, "https": proxy_url},
        timeout=10.0,
    )


def _staging_files(canonical: Path) -> list[Path]:
    """Per-build staging files beside ``canonical`` (markers excluded)."""
    return [
        p
        for p in canonical.parent.glob(f"{canonical.name}.staging-mitm-*")
        if not p.name.endswith(".completed")
    ]


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


def test_replay_serves_repeated_identical_requests_non_depleting(
    upstream_server: str, cassette_path: Path, tmp_path: Path
) -> None:
    """A single recorded interaction replays N times without depleting.

    This is the out-of-process equivalent of the in-kernel
    ``allow_playback_repeats=True`` workaround (issue #95-A): the host merge
    dedups by (method, uri, body) so a canonical cassette holds exactly ONE
    entry per fingerprint; a deck that issues the same request N times must
    replay-hit on every call. vcrpy's record_mode="none" consumes each entry
    once and would raise CannotOverwriteExistingCassetteException on call 2..N.
    The addon's serve loop instead scans an append-only ``recorded`` list and
    never pops, so repeats re-serve the same entry for free — this test pins
    that guarantee on the surviving transport (the in-kernel flag has its own
    regression test; the mitmproxy side had none until now).
    """
    confdir = tmp_path / "mitm-confdir"
    target = f"{upstream_server}/repeated"

    # Record exactly one interaction.
    with MitmproxyManager(
        cassette_path=cassette_path, mode="new-episodes", confdir=confdir
    ) as proxy:
        recorded = _get_via_proxy(target, proxy.proxy_url)
    assert recorded.status_code == 200
    assert _CountingHandler.upstream_hits == 1

    # Replay the SAME request three times within ONE proxy lifecycle. Every
    # call must serve the recorded entry (no depletion), and the upstream
    # counter must stay at 1 across all of them.
    with MitmproxyManager(cassette_path=cassette_path, mode="replay", confdir=confdir) as proxy:
        replays = [_get_via_proxy(target, proxy.proxy_url) for _ in range(3)]
    assert [r.status_code for r in replays] == [200, 200, 200]
    assert all(r.json() == recorded.json() for r in replays)
    assert _CountingHandler.upstream_hits == 1, (
        "repeated identical replay requests must all serve from the single "
        "recorded entry, never deplete it or escape to upstream"
    )


def test_strict_replay_miss_returns_nonretryable_404(
    upstream_server: str, cassette_path: Path, tmp_path: Path
) -> None:
    """A request not in the cassette returns the addon's diagnostic miss
    response — a NON-RETRYABLE 404 (issue #165 P3) so the kernel's LLM SDK
    raises on the first attempt instead of retrying it as a 5xx (the old 599
    was retried, amplifying a miss across a deck's batched calls into a build
    timeout). The upstream counter must NOT advance."""
    confdir = tmp_path / "mitm-confdir"

    # Seed the cassette with one URL.
    with MitmproxyManager(
        cassette_path=cassette_path, mode="new-episodes", confdir=confdir
    ) as proxy:
        _get_via_proxy(f"{upstream_server}/recorded", proxy.proxy_url)
    assert _CountingHandler.upstream_hits == 1

    # Now request a DIFFERENT URL in strict replay mode. The addon should
    # synthesize the diagnostic miss and the upstream counter must NOT advance.
    with MitmproxyManager(cassette_path=cassette_path, mode="replay", confdir=confdir) as proxy:
        response = _get_via_proxy(f"{upstream_server}/never-recorded", proxy.proxy_url)

    assert response.status_code == 404, "miss must be a non-retryable 4xx, not a retryable 5xx"
    payload = response.json()
    assert payload["clm_replay_miss"] is True
    # SDK-friendly error envelope so the kernel surfaces a clean NotFoundError.
    assert payload["error"]["type"] == "clm_replay_miss"
    assert "clm_replay_miss" in payload["error"]["message"]
    assert payload["method"] == "GET"
    assert payload["url"].endswith("/never-recorded")
    assert _CountingHandler.upstream_hits == 1, (
        "strict-replay miss must not fall through to upstream"
    )


def test_untagged_flow_warning_reaches_host_log(
    upstream_server: str, cassette_path: Path, tmp_path: Path, caplog
) -> None:
    """An untagged flow (a client stack the tag bootstrap does not patch) must
    produce a loud, host-visible warning: the addon logs it once per build
    inside mitmdump, and the manager's stdout pump relays sentinel-marked
    lines through CLM's own logger. Without this, untagged traffic silently
    records into the catch-all instead of the topic cassette — the failure
    mode that hid the missing ``requests`` patch."""
    confdir = tmp_path / "mitm-confdir"
    sentinel = "CLM-HTTP-REPLAY-UNTAGGED"
    with caplog.at_level(logging.WARNING, logger="clm.infrastructure.http_replay_mitm"):
        with MitmproxyManager(
            cassette_path=cassette_path, mode="new-episodes", confdir=confdir
        ) as proxy:
            response = _get_via_proxy(f"{upstream_server}/untagged", proxy.proxy_url)
            assert response.status_code == 200
            # The warning travels addon -> mitmdump stdout -> pump thread, so
            # poll (generous deadline, no fixed sleep) until it is drained.
            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline:
                if any(sentinel in line for line in list(proxy._output)):
                    break
                time.sleep(0.05)
            else:
                pytest.fail(
                    "untagged-flow sentinel never appeared in mitmdump output:\n"
                    + "\n".join(proxy._output)
                )
    relayed = [r.getMessage() for r in caplog.records if sentinel in r.getMessage()]
    assert relayed, "manager did not relay the addon's untagged-flow warning to the host log"
    # The warning names the offending request so the culprit deck is findable.
    assert any("/untagged" in message for message in relayed)


def test_routing_demuxes_tagged_requests_to_per_cassette_staging(
    upstream_server: str, cassette_path: Path, tmp_path: Path
) -> None:
    """P2: tagged requests route to per-cassette staging; untagged → catch-all.

    Each request carries an ``X-CLM-Cassette`` header naming its
    destination canonical cassette. The addon demuxes flows into one
    ``*.staging-mitm-*`` file per canonical (with a ``.completed`` marker on
    clean shutdown), strips the tag header before recording, and routes
    untagged traffic to the shared catch-all — all without cross-contamination.
    """
    confdir = tmp_path / "mitm-confdir"
    cass_a = tmp_path / "topicA" / "_cassettes" / "slidesA.http-cassette.yaml"
    cass_b = tmp_path / "topicB" / "_cassettes" / "slidesB.http-cassette.yaml"

    with MitmproxyManager(
        cassette_path=cassette_path, mode="new-episodes", confdir=confdir
    ) as proxy:
        ra = _get_via_proxy(f"{upstream_server}/a", proxy.proxy_url, tag=str(cass_a))
        rb = _get_via_proxy(f"{upstream_server}/b", proxy.proxy_url, tag=str(cass_b))
        rc = _get_via_proxy(f"{upstream_server}/c", proxy.proxy_url)  # untagged

    assert ra.status_code == rb.status_code == rc.status_code == 200
    assert _CountingHandler.upstream_hits == 3

    # Each tagged cassette got exactly one staging file with its own request.
    staging_a = _staging_files(cass_a)
    staging_b = _staging_files(cass_b)
    assert len(staging_a) == 1, staging_a
    assert len(staging_b) == 1, staging_b

    text_a = staging_a[0].read_text(encoding="utf-8")
    text_b = staging_b[0].read_text(encoding="utf-8")
    assert "/a" in text_a and "/b" not in text_a  # no cross-contamination
    assert "/b" in text_b and "/a" not in text_b
    # The routing tag header is stripped before recording.
    assert "x-clm-cassette" not in text_a.lower()
    assert "x-clm-cassette" not in text_b.lower()

    # Untagged traffic landed in the catch-all, not in any tagged cassette.
    assert cassette_path.exists()
    assert "/c" in cassette_path.read_text(encoding="utf-8")
    assert "/c" not in text_a and "/c" not in text_b

    # The host writes the .completed marker after the proxy stops and folds
    # the staging into its canonical (issue #165 P2). Drive that real merge
    # path against the proxy-written staging and assert the interaction lands
    # in the canonical cassette.
    from clm.workers.notebook.http_replay_cassette import (
        CassettePaths,
        merge_staging_into_canonical,
        write_completion_marker,
    )

    write_completion_marker(CassettePaths(canonical=cass_a, staging=staging_a[0]))
    folded = merge_staging_into_canonical(CassettePaths(canonical=cass_a, staging=staging_a[0]))
    assert folded == 1
    assert cass_a.exists()
    canonical_text = cass_a.read_text(encoding="utf-8")
    assert "/a" in canonical_text and "/b" not in canonical_text
    assert not staging_a[0].exists()  # folded + consumed


def test_once_mode_starts_without_existing_catchall(
    upstream_server: str, cassette_path: Path, tmp_path: Path
) -> None:
    """Regression (issue #165): ``once`` must not abort the proxy when the
    catch-all cassette does not exist.

    Under P2 the ``clm_cassette_path`` catch-all is created empty on every
    fresh build, so the old Phase-0 ``once`` existence guard wrongly shut the
    proxy down at startup (``MitmproxyError: Mode 'once' requires an existing
    cassette``). Per-target routing means the real cassettes are the tagged
    canonicals, not the catch-all.
    """
    confdir = tmp_path / "mitm-confdir"
    cass = tmp_path / "topicO" / "_cassettes" / "slidesO.http-cassette.yaml"
    assert not cassette_path.exists()  # fresh scratch catch-all

    # Previously raised MitmproxyError during startup; must now start cleanly.
    with MitmproxyManager(cassette_path=cassette_path, mode="once", confdir=confdir) as proxy:
        response = _get_via_proxy(f"{upstream_server}/once", proxy.proxy_url, tag=str(cass))

    assert response.status_code == 200
    # A miss in once mode records into the tagged per-cassette staging file.
    assert _staging_files(cass), "once should record a miss into per-cassette staging"


def _fold_staging_to_canonical(canonical: Path, staging: Path) -> None:
    """Drive the real host marker+merge so the recorded staging lands in the
    canonical cassette (what a subsequent replay-mode proxy loads from)."""
    from clm.workers.notebook.http_replay_cassette import (
        CassettePaths,
        merge_staging_into_canonical,
        write_completion_marker,
    )

    paths = CassettePaths(canonical=canonical, staging=staging)
    write_completion_marker(paths)
    merge_staging_into_canonical(paths)


def test_secret_request_headers_and_params_stripped_from_recording(
    upstream_server: str, cassette_path: Path, tmp_path: Path
) -> None:
    """P3 secret hygiene: ``authorization``/``x-api-key`` headers and
    ``api_key``/``token`` query params never reach the recorded cassette."""
    confdir = tmp_path / "mitm-confdir"
    cass = tmp_path / "topicS" / "_cassettes" / "slidesS.http-cassette.yaml"

    with MitmproxyManager(
        cassette_path=cassette_path, mode="new-episodes", confdir=confdir
    ) as proxy:
        resp = _get_via_proxy(
            f"{upstream_server}/secret?api_key=SHHH&token=TTT&keep=1",
            proxy.proxy_url,
            tag=str(cass),
            extra_headers={"Authorization": "Bearer SUPERSECRET", "X-API-Key": "KKK"},
        )
    assert resp.status_code == 200

    staging = _staging_files(cass)
    assert len(staging) == 1, staging
    text = staging[0].read_text(encoding="utf-8")
    # Secret-bearing headers / params must be absent; the cassette is committed.
    assert "SUPERSECRET" not in text
    assert "authorization" not in text.lower()
    assert "x-api-key" not in text.lower()
    assert "api_key" not in text and "SHHH" not in text
    assert "token" not in text and "TTT" not in text
    # The non-secret query param survives (the request is still recorded).
    assert "keep=1" in text


def test_ignore_hosts_forwarded_but_not_recorded(
    upstream_server: str, cassette_path: Path, tmp_path: Path
) -> None:
    """P3 telemetry hygiene: an ignore_hosts host is forwarded upstream but
    never recorded (the LangSmith case, exercised here against localhost)."""
    confdir = tmp_path / "mitm-confdir"
    cass = tmp_path / "topicI" / "_cassettes" / "slidesI.http-cassette.yaml"

    with MitmproxyManager(
        cassette_path=cassette_path,
        mode="new-episodes",
        confdir=confdir,
        ignore_hosts=("127.0.0.1",),
    ) as proxy:
        resp = _get_via_proxy(f"{upstream_server}/telemetry", proxy.proxy_url, tag=str(cass))

    assert resp.status_code == 200  # forwarded to upstream
    assert _CountingHandler.upstream_hits == 1  # reached the network
    assert not _staging_files(cass)  # but nothing recorded
    # Nor did it land in the catch-all.
    if cassette_path.exists():
        assert "/telemetry" not in cassette_path.read_text(encoding="utf-8")


def test_json_post_replays_with_semantically_equal_body(
    upstream_server: str, cassette_path: Path, tmp_path: Path
) -> None:
    """P3 JSON-match parity: a JSON POST replay-hits even when the live body
    differs from the recorded one only by key order / separators — a byte-exact
    key would spuriously miss every real LLM POST."""
    confdir = tmp_path / "mitm-confdir"
    cass = tmp_path / "topicJ" / "_cassettes" / "slidesJ.http-cassette.yaml"
    target = f"{upstream_server}/chat"

    # 1. Record a JSON POST.
    with MitmproxyManager(
        cassette_path=cassette_path, mode="new-episodes", confdir=confdir
    ) as proxy:
        _post_json_via_proxy(target, proxy.proxy_url, b'{"model":"x","n":1}', tag=str(cass))
    assert _CountingHandler.upstream_hits == 1
    staging = _staging_files(cass)
    assert len(staging) == 1
    _fold_staging_to_canonical(cass, staging[0])

    # 2. Replay with a semantically-identical but textually-different body.
    with MitmproxyManager(cassette_path=cassette_path, mode="replay", confdir=confdir) as proxy:
        replayed = _post_json_via_proxy(
            target, proxy.proxy_url, b'{"n": 1, "model": "x"}', tag=str(cass)
        )
    assert replayed.status_code == 200, replayed.text
    assert _CountingHandler.upstream_hits == 1, "JSON POST must replay-hit, not escape upstream"


def test_refresh_rerecords_while_replay_serves_existing_cassette(
    upstream_server: str, cassette_path: Path, tmp_path: Path
) -> None:
    """P3 once/refresh semantics: ``replay`` serves an existing cassette
    (no upstream hit) while ``refresh`` ignores it and re-hits upstream."""
    confdir = tmp_path / "mitm-confdir"
    cass = tmp_path / "topicR" / "_cassettes" / "slidesR.http-cassette.yaml"
    target = f"{upstream_server}/seeded"

    # Seed a canonical cassette by recording once and folding.
    with MitmproxyManager(
        cassette_path=cassette_path, mode="new-episodes", confdir=confdir
    ) as proxy:
        _get_via_proxy(target, proxy.proxy_url, tag=str(cass))
    _fold_staging_to_canonical(cass, _staging_files(cass)[0])
    assert cass.exists()

    # replay: served from cassette, upstream untouched.
    _CountingHandler.upstream_hits = 0
    with MitmproxyManager(cassette_path=cassette_path, mode="replay", confdir=confdir) as proxy:
        r1 = _get_via_proxy(target, proxy.proxy_url, tag=str(cass))
    assert r1.status_code == 200
    assert _CountingHandler.upstream_hits == 0, "replay must serve the existing cassette"

    # refresh: ignores the existing cassette and re-hits upstream.
    _CountingHandler.upstream_hits = 0
    with MitmproxyManager(cassette_path=cassette_path, mode="refresh", confdir=confdir) as proxy:
        r2 = _get_via_proxy(target, proxy.proxy_url, tag=str(cass))
    assert r2.status_code == 200
    assert _CountingHandler.upstream_hits == 1, "refresh must re-hit upstream, not serve cassette"


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
