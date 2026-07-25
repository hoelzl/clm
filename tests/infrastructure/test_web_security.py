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
    TrustedHostMiddleware,
    check_request_origin,
    default_allowed_hosts,
    extract_host,
    host_is_allowed,
    install_web_security,
    normalize_origin,
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

    def test_wildcard_suffix_matches_subdomains(self):
        assert host_is_allowed("box.ts.net", ["*.ts.net"])
        assert not host_is_allowed("boxts.net", ["*.ts.net"])


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

    @pytest.mark.parametrize("origin", ["null", "", "   ", "not-a-url", "http://"])
    def test_unusable_origins_are_none(self, origin: str):
        assert normalize_origin(origin) is None


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

    def test_fetch_metadata_wins_over_a_matching_origin(self):
        """A forged ``Origin`` cannot talk its way past ``Sec-Fetch-Site``."""
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
