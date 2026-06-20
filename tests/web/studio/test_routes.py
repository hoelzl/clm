"""Tests for the Studio API routes (auth + concurrency surfaced over HTTP)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from clm.web.app import create_app

from .conftest import Course

TOKEN = "test-studio-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture()
def client(course: Course) -> TestClient:
    app = create_app(
        db_path=course.slides_dir.parent / "jobs.db",
        spec_path=course.spec_path,
        studio_token=TOKEN,
    )
    # No `with` → lifespan (and the filesystem watcher) is not started; routes
    # work because StudioService is wired in create_app, not the lifespan.
    return TestClient(app)


class TestAuth:
    def test_missing_token_is_401(self, client: TestClient):
        assert client.get("/api/studio/decks").status_code == 401

    def test_wrong_token_is_401(self, client: TestClient):
        r = client.get("/api/studio/decks", headers={"Authorization": "Bearer nope"})
        assert r.status_code == 401

    def test_token_via_query_param(self, client: TestClient):
        r = client.get(f"/api/studio/decks?token={TOKEN}")
        assert r.status_code == 200


class TestReadEndpoints:
    def test_list_decks(self, client: TestClient, course: Course):
        r = client.get("/api/studio/decks", headers=AUTH)
        assert r.status_code == 200
        ids = [d["deck_id"] for d in r.json()["decks"]]
        assert course.deck_id in ids

    def test_open_deck(self, client: TestClient, course: Course):
        r = client.get(f"/api/studio/deck?id={course.deck_id}", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["deck_version"]
        assert any(c["role"] == "slide" and c["editable"] for c in body["cells"])

    def test_open_unknown_deck_404(self, client: TestClient):
        r = client.get(
            "/api/studio/deck?id=module_100_basics/topic_010_intro/ghost.de.py",
            headers=AUTH,
        )
        assert r.status_code == 404

    def test_search(self, client: TestClient):
        r = client.get("/api/studio/search?q=intro", headers=AUTH)
        assert r.status_code == 200
        assert "hits" in r.json()


class TestWriteEndpoints:
    def _open_slide(self, client: TestClient, course: Course):
        body = client.get(f"/api/studio/deck?id={course.deck_id}", headers=AUTH).json()
        slide = next(c for c in body["cells"] if c["role"] == "slide")
        return body["deck_version"], slide

    def test_edit_body_ok(self, client: TestClient, course: Course):
        deck_version, slide = self._open_slide(client, course)
        r = client.post(
            "/api/studio/deck/edit-body",
            headers=AUTH,
            json={
                "deck_id": course.deck_id,
                "slide_id": slide["slide_id"],
                "role": slide["role"],
                "new_body": "# Neu\n#\n# Inhalt",
                "expected_deck_version": deck_version,
                "expected_cell_hash": slide["content_hash"],
            },
        )
        assert r.status_code == 200
        assert r.json()["deck_version"] != deck_version

    def test_stale_write_is_409_with_fresh_guard(self, client: TestClient, course: Course):
        _, slide = self._open_slide(client, course)
        r = client.post(
            "/api/studio/deck/edit-body",
            headers=AUTH,
            json={
                "deck_id": course.deck_id,
                "slide_id": slide["slide_id"],
                "role": slide["role"],
                "new_body": "x",
                "expected_deck_version": "staleversion00000",
                "expected_cell_hash": slide["content_hash"],
            },
        )
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert detail["kind"] == "deck_version"
        assert detail["current"]  # fresh guard handed back for retry
