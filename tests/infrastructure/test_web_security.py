"""Tests for the browser-facing guards in :mod:`clm.infrastructure.web_security`.

The properties under test are the ones an attacker's page probes: can it send
a mutating request that the app acts on, and can it reach the app under a name
the app should not answer to (DNS rebinding). The negative cases matter as
much as the positive ones — a guard that also blocks ``curl``, the app's own
HTMX traffic, or IPv6 loopback would be turned off, and then it protects
nothing.
"""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient

from clm.infrastructure.web_security import (
    OriginGuardMiddleware,
    SecurityHeadersMiddleware,
    TrustedHostMiddleware,
    check_request_origin,
    default_allowed_hosts,
    extract_host,
    host_is_allowed,
    install_security_headers,
    install_web_security,
    normalize_origin,
    remote_access_warning,
)


class TestExtractHost:
    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            ("localhost:8008", "localhost"),
            ("localhost", "localhost"),
            ("127.0.0.1:8008", "127.0.0.1"),
            ("EXAMPLE.COM", "example.com"),
            ("  example.com:80  ", "example.com"),
            ("", ""),
        ],
    )
    def test_strips_port_and_case(self, header: str, expected: str):
        assert extract_host(header) == expected

    def test_bracketed_ipv6_keeps_the_address(self):
        """Starlette's own splitter yields ``"["`` here, locking out IPv6 loopback."""
        assert extract_host("[::1]:8000") == "::1"
        assert extract_host("[fe80::1]") == "fe80::1"

    def test_trailing_dot_fqdn_is_the_same_host(self):
        """``http://localhost./`` is a legal URL and the dot reaches the header."""
        assert extract_host("localhost.:8008") == "localhost"
        assert extract_host("box.ts.net.") == "box.ts.net"


class TestDefaultAllowedHosts:
    def test_loopback_is_always_allowed(self):
        hosts = default_allowed_hosts(None)
        assert "localhost" in hosts
        assert "127.0.0.1" in hosts
        assert "::1" in hosts

    def test_bind_host_is_folded_in(self):
        assert "studio.local" in default_allowed_hosts("studio.local")

    def test_wildcard_bind_contributes_nothing(self):
        """``0.0.0.0`` names every interface, not a host anybody types."""
        assert default_allowed_hosts("0.0.0.0") == list(default_allowed_hosts(None))
        assert default_allowed_hosts("::") == list(default_allowed_hosts(None))

    def test_extra_hosts_are_normalized(self):
        hosts = default_allowed_hosts("0.0.0.0", ["box.ts.net:8008"])
        assert "box.ts.net" in hosts

    def test_star_disables_the_check(self):
        assert default_allowed_hosts("0.0.0.0", ["*"]) == ["*"]


class TestHostIsAllowed:
    def test_wildcard_suffix_matches_subdomains(self):
        assert host_is_allowed("box.ts.net", ["*.ts.net"])
        assert not host_is_allowed("boxts.net", ["*.ts.net"])
        assert not host_is_allowed("ts.net", ["*.ts.net"])

    def test_exact_match(self):
        assert host_is_allowed("localhost", ["localhost"])
        assert not host_is_allowed("localhost.evil.example", ["localhost"])


class TestRemoteAccessWarning:
    """A bind address that promises more reach than the allowlist can deliver."""

    def test_loopback_bind_is_silent(self):
        assert remote_access_warning("127.0.0.1") is None
        assert remote_access_warning("localhost") is None

    @pytest.mark.parametrize("host", ["0.0.0.0", "::"])
    def test_wildcard_bind_warns(self, host: str):
        message = remote_access_warning(host)
        assert message is not None
        assert "--allowed-host" in message

    def test_explicit_allowed_host_silences_it(self):
        assert remote_access_warning("0.0.0.0", ["box.ts.net"]) is None

    def test_specific_non_loopback_bind_warns_about_names(self):
        message = remote_access_warning("192.168.1.5")
        assert message is not None
        assert "192.168.1.5" in message


class TestNormalizeOrigin:
    @pytest.mark.parametrize(
        ("origin", "expected"),
        [
            ("http://localhost:80", "http://localhost"),
            ("https://example.com:443", "https://example.com"),
            ("http://example.com:8000", "http://example.com:8000"),
            ("HTTP://Example.COM", "http://example.com"),
        ],
    )
    def test_default_ports_are_dropped(self, origin: str, expected: str):
        assert normalize_origin(origin) == expected

    @pytest.mark.parametrize(
        "origin", ["null", "", "   ", "not-a-url", "http://", "http://[::1", "http://a]b"]
    )
    def test_unusable_origins_are_none(self, origin: str):
        """Includes the forms ``urlsplit`` raises ValueError on."""
        assert normalize_origin(origin) is None

    def test_ipv6_keeps_its_brackets(self):
        """Otherwise the result is the ambiguous ``http://::1:8008``."""
        assert normalize_origin("http://[::1]:8008") == "http://[::1]:8008"
        assert normalize_origin("http://[::1]") == "http://[::1]"


class TestCheckRequestOrigin:
    @staticmethod
    def _headers(**kwargs: str):
        from starlette.datastructures import Headers

        return Headers({k.replace("_", "-"): v for k, v in kwargs.items()})

    def test_no_browser_headers_at_all_is_allowed(self):
        """``curl`` and the test client send neither header; they are not the threat."""
        assert check_request_origin(self._headers(host="127.0.0.1:8008")) is None

    @pytest.mark.parametrize("site", ["same-origin", "none"])
    def test_same_origin_and_user_initiated_pass(self, site: str):
        assert check_request_origin(self._headers(sec_fetch_site=site)) is None

    @pytest.mark.parametrize("site", ["cross-site", "same-site"])
    def test_other_sites_are_refused(self, site: str):
        reason = check_request_origin(self._headers(sec_fetch_site=site))
        assert reason is not None
        assert site in reason

    def test_fetch_metadata_wins_over_an_origin_matching_the_host(self):
        """Matching the ``Host`` is not enough to beat ``Sec-Fetch-Site``.

        Only an origin the *operator* named can do that — see
        ``test_allowlisted_origin_beats_cross_site_fetch_metadata`` below. This
        case passes the default empty allowlist, so a forged ``Origin`` echoing
        the host buys nothing.
        """
        headers = self._headers(
            sec_fetch_site="cross-site",
            origin="http://127.0.0.1:8008",
            host="127.0.0.1:8008",
        )
        assert check_request_origin(headers) is not None

    def test_origin_matching_the_host_passes(self):
        headers = self._headers(origin="http://127.0.0.1:8008", host="127.0.0.1:8008")
        assert check_request_origin(headers) is None

    def test_foreign_origin_is_refused(self):
        headers = self._headers(origin="https://evil.example", host="127.0.0.1:8008")
        reason = check_request_origin(headers)
        assert reason is not None
        assert "evil.example" in reason

    def test_same_host_different_port_is_refused(self):
        """A different port is a different app, even on the same machine."""
        headers = self._headers(origin="http://127.0.0.1:3000", host="127.0.0.1:8008")
        assert check_request_origin(headers) is not None

    def test_https_origin_from_a_tls_terminating_proxy_passes(self):
        """``tailscale serve`` forwards ``https://…`` to a plain-HTTP upstream."""
        headers = self._headers(origin="https://box.ts.net", host="box.ts.net")
        assert check_request_origin(headers) is None

    def test_allowlisted_origin_beats_cross_site_fetch_metadata(self):
        """``--allowed-origin`` has to outrank ``Sec-Fetch-Site`` to mean anything.

        Every current browser sends fetch metadata, so consulting the
        allowlist only in the ``Origin`` fallback made the flag unreachable
        for exactly the clients it exists to authorize: a named front-end got
        a successful CORS preflight and a 403 on the request behind it.
        """
        headers = self._headers(
            sec_fetch_site="cross-site",
            origin="https://front.example",
            host="127.0.0.1:8008",
        )
        assert check_request_origin(headers, allowed_origins=["https://front.example"]) is None

    def test_unlisted_origin_is_still_refused_with_an_allowlist_present(self):
        """Widening for one origin must not widen for its neighbours."""
        headers = self._headers(
            sec_fetch_site="cross-site",
            origin="https://evil.example",
            host="127.0.0.1:8008",
        )
        reason = check_request_origin(headers, allowed_origins=["https://front.example"])
        assert reason is not None

    def test_allowlist_matching_is_normalized_not_textual(self):
        """A default port is not part of an origin a browser sends."""
        headers = self._headers(sec_fetch_site="cross-site", origin="https://front.example:443")
        assert check_request_origin(headers, allowed_origins=["https://front.example"]) is None

    @pytest.mark.parametrize(
        "origin",
        [
            "https://evil.example@front.example",  # userinfo: hostname is front.example
            "https://front.exa\tmple",  # WHATWG strips the tab before resolving
        ],
    )
    def test_origins_that_read_as_one_host_and_parse_as_another_are_refused(self, origin: str):
        """No browser emits these; a hand-rolled client should not benefit from them."""
        from starlette.datastructures import Headers

        headers = Headers(
            {"sec-fetch-site": "cross-site", "host": "127.0.0.1:8008", "origin": origin}
        )
        assert check_request_origin(headers, allowed_origins=["https://front.example"]) is not None

    def test_portless_host_still_refuses_an_origin_on_another_port(self):
        """A dev server on :3000 must not drive a dashboard reached as bare ``localhost``.

        The portless-``Host`` branch exists for the TLS-proxy case above; it
        must not degrade into "any port on this hostname".
        """
        headers = self._headers(origin="http://localhost:3000", host="localhost")
        assert check_request_origin(headers) is not None

    @pytest.mark.parametrize("origin", ["http://[::1", "http://[oops", "http://a]b"])
    def test_a_malformed_origin_is_refused_not_raised(self, origin: str):
        """``urlsplit`` raises ValueError on these; a raise here would be a 500.

        Not a bypass — the handler is never reached either way — but a refusal
        turning into a traceback from inside the security middleware is its own
        defect.
        """
        headers = self._headers(origin=origin, host="127.0.0.1:8008")
        assert check_request_origin(headers) is not None

    def test_trailing_dot_origin_matches_trailing_dot_host(self):
        """The two guards must agree about the FQDN form."""
        headers = self._headers(origin="http://localhost.:8008", host="localhost.:8008")
        assert check_request_origin(headers) is None

    def test_explicitly_allowed_origin_passes(self):
        headers = self._headers(origin="https://studio.example", host="127.0.0.1:8008")
        assert check_request_origin(headers, allowed_origins=["https://studio.example"]) is None


def _guarded_app(**kwargs) -> Starlette:
    """A two-route app carrying both guards, for end-to-end middleware tests."""

    async def read(request):
        return PlainTextResponse("read ok")

    async def write(request):
        return PlainTextResponse("write ok")

    async def socket(websocket):
        await websocket.accept()
        await websocket.send_text("ws ok")
        await websocket.close()

    app = Starlette(
        routes=[
            Route("/read", read, methods=["GET"]),
            Route("/write", write, methods=["POST"]),
            WebSocketRoute("/ws", socket),
        ]
    )
    install_web_security(app, **kwargs)
    return app


class TestUnusableAllowedOriginIsReported:
    """An exemption the operator asked for and did not get must not be silent."""

    def test_scheme_less_allowed_origin_warns(self, caplog):
        import logging

        from clm.infrastructure.web_security import OriginGuardMiddleware

        with caplog.at_level(logging.WARNING, logger="clm.infrastructure.web_security"):
            guard = OriginGuardMiddleware(None, allowed_origins=["box.tail1234.ts.net"])

        assert guard.allowed_origins == []
        assert "Ignoring --allowed-origin" in caplog.text

    def test_usable_allowed_origin_is_kept_quietly(self, caplog):
        import logging

        from clm.infrastructure.web_security import OriginGuardMiddleware

        with caplog.at_level(logging.WARNING, logger="clm.infrastructure.web_security"):
            guard = OriginGuardMiddleware(None, allowed_origins=["https://box.tail1234.ts.net"])

        assert guard.allowed_origins == ["https://box.tail1234.ts.net"]
        assert "Ignoring" not in caplog.text


class TestMiddlewareEndToEnd:
    def test_loopback_host_is_served(self):
        client = TestClient(_guarded_app(), base_url="http://127.0.0.1:8008")
        assert client.get("/read").status_code == 200

    def test_ipv6_loopback_host_is_served(self):
        """Browsing to ``http://[::1]:8008/`` must not 400.

        The header is set by hand because Starlette's own test client cannot
        parse an IPv6 ``base_url`` — which is beside the point here, since the
        header is the only thing the guard looks at.
        """
        client = TestClient(_guarded_app(), base_url="http://127.0.0.1:8008")
        assert client.get("/read", headers={"Host": "[::1]:8008"}).status_code == 200

    def test_rebound_host_is_refused(self):
        """DNS rebinding: the page's origin *is* the app's, but Host is not."""
        client = TestClient(_guarded_app(), base_url="http://evil.example:8008")
        response = client.post("/write", headers={"Origin": "http://evil.example:8008"})
        assert response.status_code == 400
        assert "host" in response.text.lower()

    def test_allowed_host_opt_in_is_honoured(self):
        app = _guarded_app(allowed_hosts=["box.ts.net"])
        client = TestClient(app, base_url="http://box.ts.net")
        assert client.get("/read").status_code == 200

    def test_cross_site_post_is_refused(self):
        client = TestClient(_guarded_app(), base_url="http://127.0.0.1:8008")
        response = client.post("/write", headers={"Sec-Fetch-Site": "cross-site"})
        assert response.status_code == 403

    def test_cross_origin_post_is_refused(self):
        client = TestClient(_guarded_app(), base_url="http://127.0.0.1:8008")
        response = client.post("/write", headers={"Origin": "https://evil.example"})
        assert response.status_code == 403

    def test_same_origin_post_is_served(self):
        client = TestClient(_guarded_app(), base_url="http://127.0.0.1:8008")
        response = client.post(
            "/write",
            headers={"Origin": "http://127.0.0.1:8008", "Sec-Fetch-Site": "same-origin"},
        )
        assert response.status_code == 200

    def test_get_is_never_origin_checked(self):
        """Blocking cross-origin reads would break ordinary navigation."""
        client = TestClient(_guarded_app(), base_url="http://127.0.0.1:8008")
        response = client.get("/read", headers={"Sec-Fetch-Site": "cross-site"})
        assert response.status_code == 200

    def test_websocket_from_a_foreign_origin_is_refused(self):
        """WebSockets are CORS-exempt, so the guard has to cover the handshake."""
        from starlette.websockets import WebSocketDisconnect

        # The test client always sends ``Host: testserver`` on a WS handshake
        # regardless of base_url, so the host check is opted out of here to
        # leave the origin check as the only thing under test.
        client = TestClient(_guarded_app(allowed_hosts=["testserver"]))
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws", headers={"Origin": "https://evil.example"}):
                pass

    def test_websocket_from_the_same_origin_is_served(self):
        client = TestClient(_guarded_app(allowed_hosts=["testserver"]))
        with client.websocket_connect("/ws", headers={"Origin": "http://testserver"}) as ws:
            assert ws.receive_text() == "ws ok"

    def test_websocket_with_a_rebound_host_is_refused(self):
        from starlette.websockets import WebSocketDisconnect

        client = TestClient(_guarded_app())
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws"):
                pass

    def test_middlewares_can_be_disabled_wholesale(self):
        app = _guarded_app(allowed_hosts=["*"])
        client = TestClient(app, base_url="http://anything.example")
        assert client.get("/read").status_code == 200


class TestGuardsAreIndependentlyUsable:
    def test_trusted_host_alone(self):
        async def read(request):
            return PlainTextResponse("ok")

        app = Starlette(routes=[Route("/read", read)])
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=["good.example"])
        assert TestClient(app, base_url="http://good.example").get("/read").status_code == 200
        assert TestClient(app, base_url="http://bad.example").get("/read").status_code == 400

    def test_origin_guard_alone(self):
        async def write(request):
            return PlainTextResponse("ok")

        app = Starlette(routes=[Route("/write", write, methods=["POST"])])
        app.add_middleware(OriginGuardMiddleware, allowed_origins=[])
        client = TestClient(app, base_url="http://127.0.0.1:8008")
        assert client.post("/write").status_code == 200
        assert client.post("/write", headers={"Origin": "https://evil.example"}).status_code == 403


def _headers_app() -> Starlette:
    """A small app behind the security-headers middleware, for end-to-end tests."""

    async def read(request):
        return PlainTextResponse("ok")

    async def docs(request):
        return PlainTextResponse("swagger-ish")

    async def own_csp(request):
        return PlainTextResponse("ok", headers={"Content-Security-Policy": "script-src 'none'"})

    async def socket(websocket):
        await websocket.accept()
        await websocket.send_text("ws ok")
        await websocket.close()

    app = Starlette(
        routes=[
            Route("/read", read, methods=["GET"]),
            Route("/docs", docs, methods=["GET"]),
            Route("/own-csp", own_csp, methods=["GET"]),
            WebSocketRoute("/ws", socket),
        ]
    )
    install_security_headers(app)
    return app


class TestSecurityHeaders:
    """The CSP is the backstop that keeps a sanitizer miss from being fatal.

    The Studio page holds a non-expiring bearer token in ``localStorage`` and
    injects server-sanitized HTML; without a CSP every sanitizing bug is
    immediately script execution. These tests pin the *content* of the policy,
    not just its presence — a header that permits inline script protects
    nothing.
    """

    def test_security_headers_are_set_on_an_ordinary_response(self):
        r = TestClient(_headers_app()).get("/read")
        assert "content-security-policy" in r.headers
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "no-referrer"

    def test_the_policy_forbids_what_makes_xss_fatal(self):
        r = TestClient(_headers_app()).get("/read")
        directives = dict(
            d.strip().split(" ", 1) for d in r.headers["content-security-policy"].split(";")
        )
        # Inline script and event handlers are what an injected tag needs.
        assert directives["script-src"] == "'self'"
        # Token exfil goes through fetch/XHR/WebSocket — same-origin only.
        assert directives["connect-src"] == "'self'"
        assert directives["object-src"] == "'none'"
        assert directives["base-uri"] == "'none'"
        assert directives["frame-ancestors"] == "'none'"
        # The Studio logo is a data: URI; off-origin images are the documented
        # tier-1-parity decision.
        assert "data:" in directives["img-src"]
        assert "https:" in directives["img-src"]
        # Inline styles must stay: the Studio shell has an inline <style> block
        # and the tier-2 sanitizer deliberately keeps style attributes (CSS
        # policy is the sanitizer's job, not the CSP's).
        assert "'unsafe-inline'" in directives["style-src"]

    def test_swagger_pages_are_exempt(self):
        """FastAPI's /docs + /redoc pull JS/CSS from a CDN and run inline script.

        A strict CSP breaks them; they are static, trusted pages, so the
        exemption costs nothing. (openapi.json needs no exemption — CSP only
        governs documents.)
        """
        r = TestClient(_headers_app()).get("/docs")
        assert "content-security-policy" not in r.headers

    def test_a_routes_own_csp_is_not_overwritten(self):
        """A route that sets a policy deliberately keeps the last word."""
        r = TestClient(_headers_app()).get("/own-csp")
        assert r.headers["content-security-policy"] == "script-src 'none'"

    def test_websocket_scope_is_untouched(self):
        client = TestClient(_headers_app())
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_text() == "ws ok"


def _raw_scope_probe(app, path: str, root_path: str = "") -> list[dict]:
    """Drive ``SecurityHeadersMiddleware`` around a raw ASGI ``app``.

    Needed where Starlette gets in the way of what is being tested: uvicorn's
    root_path-into-``scope["path"]`` composition, and a non-Starlette app that
    emits a mixed-case header name (Starlette lowercases everything).
    """
    import asyncio

    async def _run() -> list[dict]:
        messages: list[dict] = []
        scope = {
            "type": "http",
            "path": path,
            "root_path": root_path,
            "method": "GET",
            "headers": [],
        }

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            messages.append(message)

        await SecurityHeadersMiddleware(app)(scope, receive, send)
        return messages

    return asyncio.run(_run())


async def _ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


class TestExemptionMatching:
    """The exemption must name pages, never a prefix that can grow."""

    def test_docs_subpaths_are_exempt(self):
        messages = _raw_scope_probe(_ok_app, "/docs/oauth2-redirect")
        assert all(k != b"content-security-policy" for k, _ in messages[0]["headers"])

    def test_a_path_that_merely_begins_with_docs_is_not_exempt(self):
        """A future /docs-archive route must not silently lose the backstop."""
        messages = _raw_scope_probe(_ok_app, "/docs-archive")
        assert any(k == b"content-security-policy" for k, _ in messages[0]["headers"])

    def test_docs_stays_exempt_behind_a_root_path_proxy(self):
        """uvicorn folds ``root_path`` into ``scope["path"]``; strip it first."""
        messages = _raw_scope_probe(_ok_app, "/clm/docs", root_path="/clm")
        assert all(k != b"content-security-policy" for k, _ in messages[0]["headers"])

    def test_root_path_does_not_exempt_unrelated_pages(self):
        messages = _raw_scope_probe(_ok_app, "/clm/read", root_path="/clm")
        assert any(k == b"content-security-policy" for k, _ in messages[0]["headers"])


class TestExistingHeaderCasing:
    def test_a_mixed_case_route_csp_is_not_duplicated(self):
        """ASGI only *recommends* lower-case names; two CSPs = intersection."""

        async def mixed_case_app(scope, receive, send):
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"Content-Security-Policy", b"script-src 'none'")],
                }
            )
            await send({"type": "http.response.body", "body": b"ok"})

        messages = _raw_scope_probe(mixed_case_app, "/read")
        csp_headers = [
            v for k, v in messages[0]["headers"] if k.lower() == b"content-security-policy"
        ]
        assert csp_headers == [b"script-src 'none'"]
