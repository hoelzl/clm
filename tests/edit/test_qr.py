"""Tests for :mod:`clm.edit.qr` and the ``/qr`` route."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

pytest.importorskip("segno", reason="segno not installed (needs [edit] extra)")
pytest.importorskip("jinja2", reason="jinja2 not installed (needs [edit] extra)")
pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi.testclient import TestClient  # noqa: E402

from clm.edit.app import create_app  # noqa: E402
from clm.edit.qr import best_url, print_terminal, svg_data_uri  # noqa: E402


class TestSvgDataUri:
    def test_returns_data_uri_prefixed_svg(self):
        uri = svg_data_uri("http://192.168.1.42:8080")
        assert uri.startswith("data:image/svg+xml")
        assert "svg" in uri

    def test_encodes_the_url(self):
        # segno embeds the data; a fresh URI per distinct URL.
        a = svg_data_uri("http://a.example:8080")
        b = svg_data_uri("http://b.example:8080")
        assert a != b


class TestPrintTerminal:
    def test_writes_block_chars_to_file(self):
        buf = io.StringIO()
        print_terminal("http://192.168.1.42:8080", file=buf)
        out = buf.getvalue()
        assert out  # non-empty
        # Compact terminal output uses Unicode block elements.
        assert any(c in out for c in "▀▄█")

    def test_default_writes_to_stdout(self, capsys):
        print_terminal("http://192.168.1.42:8080")
        captured = capsys.readouterr()
        assert captured.out  # non-empty

    def test_invalid_input_does_not_raise(self):
        # Defensive: a QR glitch must never propagate.
        buf = io.StringIO()
        print_terminal("", file=buf)  # empty string is still encodable, but no crash


class TestBestUrl:
    def test_lan_ip_when_exposed(self):
        assert best_url("0.0.0.0", 8080, lan_ip="192.168.1.42") == "http://192.168.1.42:8080"

    def test_ipv6_any_when_exposed(self):
        assert best_url("::", 9000, lan_ip="fe80::1") == "http://fe80::1:9000"

    def test_falls_back_to_host_when_not_exposed(self):
        assert best_url("127.0.0.1", 8080) == "http://127.0.0.1:8080"

    def test_falls_back_to_host_when_no_lan_ip(self):
        assert best_url("0.0.0.0", 8080) == "http://0.0.0.0:8080"


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    (tmp_path / "slides" / "module_010_demo").mkdir(parents=True)
    (tmp_path / "slides" / "module_010_demo" / "topic_100_demo.py").write_text(
        "# %%\nprint('hi')\n", encoding="utf-8"
    )
    return tmp_path


class TestQrRoute:
    def test_returns_svg(self, data_dir: Path):
        app = create_app(data_dir)
        with TestClient(app) as c:
            resp = c.get("/qr")
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "image/svg+xml"
            assert "<svg" in resp.text
            assert "</svg>" in resp.text

    def test_browse_page_embeds_qr_when_available(self, data_dir: Path):
        # segno is installed in the dev/test environment ([edit] extra).
        app = create_app(data_dir)
        with TestClient(app) as c:
            resp = c.get("/")
            assert resp.status_code == 200
            assert 'src="/qr"' in resp.text
            assert "qr-img" in resp.text


class TestQrUnavailable:
    """When segno is not installed, the editor still loads and degrades gracefully."""

    def test_qr_route_returns_503(self, data_dir: Path, monkeypatch):
        from clm.edit import routes as routes_mod

        # Pretend segno is absent: the route short-circuits with HTTP 503
        # instead of attempting to import segno.
        monkeypatch.setattr(routes_mod, "qr_is_available", lambda: False)
        app = create_app(data_dir)
        with TestClient(app) as c:
            resp = c.get("/qr")
            assert resp.status_code == 503

    def test_browse_page_hides_qr_when_unavailable(self, data_dir: Path, monkeypatch):
        from clm.edit import routes as routes_mod

        monkeypatch.setattr(routes_mod, "qr_is_available", lambda: False)
        app = create_app(data_dir)
        with TestClient(app) as c:
            resp = c.get("/")
            assert resp.status_code == 200
            assert "qr-img" not in resp.text
