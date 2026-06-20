"""Tests for P4 — tier-2 (no-exec) render + the PWA / offline assets."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clm.web.app import create_app
from clm.web.studio.render import render_j2_cell
from clm.web.studio.service import StudioService

from .conftest import Course

TOKEN = "test-studio-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

# A real CLM j2 header cell: a line-statement import + a macro call expression.
J2_HEADER = "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"Hallo Welt\") }}"


class TestTier2Render:
    def test_j2_header_expands(self, tmp_path: Path):
        ok, error, text = render_j2_cell(tmp_path / "slides_x.de.py", J2_HEADER, "de")
        assert ok and error is None
        assert "Hallo Welt" in text
        assert "{{" not in text  # the macro call was expanded, not echoed

    def test_broken_jinja_falls_back(self, tmp_path: Path):
        bad = "# {{ this is not valid jinja "
        ok, error, text = render_j2_cell(tmp_path / "slides_x.de.py", bad, "de")
        assert ok is False
        assert error  # explains why
        assert text == bad  # body returned unchanged for tier-1 fallback

    def test_service_skips_non_j2(self, service: StudioService, course: Course):
        ok, error, text = service.render_cell(course.deck_id, "# plain", is_j2=False)
        assert ok is False and text == "# plain"


class TestRenderEndpoint:
    @pytest.fixture()
    def client(self, course: Course) -> TestClient:
        app = create_app(
            db_path=course.slides_dir.parent / "jobs.db",
            spec_path=course.spec_path,
            studio_token=TOKEN,
        )
        return TestClient(app)

    def test_render_cell_expands_j2(self, client: TestClient, course: Course):
        r = client.post(
            "/api/studio/deck/render-cell",
            headers=AUTH,
            json={"deck_id": course.deck_id, "body": J2_HEADER, "is_j2": True, "lang": "de"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["rendered"] is True
        assert "Hallo Welt" in data["body"]

    def test_render_cell_non_j2_passthrough(self, client: TestClient, course: Course):
        r = client.post(
            "/api/studio/deck/render-cell",
            headers=AUTH,
            json={"deck_id": course.deck_id, "body": "# plain", "is_j2": False},
        )
        assert r.status_code == 200 and r.json()["rendered"] is False

    def test_render_cell_requires_token(self, client: TestClient, course: Course):
        r = client.post(
            "/api/studio/deck/render-cell",
            json={"deck_id": course.deck_id, "body": J2_HEADER, "is_j2": True},
        )
        assert r.status_code == 401


class TestPwaAssets:
    @pytest.fixture()
    def client(self, course: Course) -> TestClient:
        app = create_app(
            db_path=course.slides_dir.parent / "jobs.db",
            spec_path=course.spec_path,
            studio_token=TOKEN,
        )
        return TestClient(app)

    def test_manifest_served(self, client: TestClient):
        r = client.get("/studio/manifest.json")
        assert r.status_code == 200
        assert r.json()["display"] == "standalone"

    def test_icon_served(self, client: TestClient):
        assert client.get("/studio/icon.svg").status_code == 200

    def test_service_worker_has_root_scope_header(self, client: TestClient):
        # The sw.js route must override the static mount and grant root scope.
        r = client.get("/studio/sw.js")
        assert r.status_code == 200
        assert r.headers.get("Service-Worker-Allowed") == "/"
        assert "caches" in r.text  # it is the actual service worker

    def test_app_shell_served(self, client: TestClient):
        assert client.get("/studio/app.js").status_code == 200
        assert client.get("/studio/").status_code == 200
