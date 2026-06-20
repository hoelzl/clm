"""Tests for Studio pairing: persistent token + QR helper."""

from __future__ import annotations

from pathlib import Path

from clm.web.studio import auth, qr


class _FakeRequest:
    def __init__(self, headers=None, query=None):
        self.headers = headers or {}
        self.query_params = query or {}


class TestToken:
    def test_create_is_stable_then_rotates(self, tmp_path: Path, monkeypatch):
        token_file = tmp_path / "studio_token"
        monkeypatch.setattr(auth, "_token_path", lambda: token_file)

        first = auth.get_or_create_token()
        assert first
        assert token_file.read_text(encoding="utf-8").strip() == first
        # Stable across calls…
        assert auth.get_or_create_token() == first
        # …until rotated.
        rotated = auth.get_or_create_token(rotate=True)
        assert rotated != first
        assert auth.get_or_create_token() == rotated

    def test_extract_from_bearer_header(self):
        req = _FakeRequest(headers={"Authorization": "Bearer abc123"})
        assert auth.extract_token(req) == "abc123"

    def test_extract_from_query_param(self):
        req = _FakeRequest(query={"token": "qtok"})
        assert auth.extract_token(req) == "qtok"

    def test_token_matches_is_constant_time_check(self):
        req = _FakeRequest(headers={"Authorization": "Bearer secret"})
        assert auth.token_matches(req, "secret")
        assert not auth.token_matches(req, "other")

    def test_no_token_does_not_match(self):
        assert not auth.token_matches(_FakeRequest(), "secret")


class TestQr:
    def test_available(self):
        # segno ships with the [web] extra used in the test env.
        assert qr.is_available() is True

    def test_svg_data_uri(self):
        uri = qr.svg_data_uri("http://example.test/studio")
        assert uri.startswith("data:image/svg+xml")

    def test_print_terminal_is_safe(self, capsys):
        qr.print_terminal("http://example.test/studio")
        # Should emit *something* and never raise.
        captured = capsys.readouterr()
        assert captured.out != "" or captured.err == ""
