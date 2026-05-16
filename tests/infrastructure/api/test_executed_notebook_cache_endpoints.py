"""Tests for the executed_notebooks cache REST endpoints.

These endpoints close the Docker/API-mode gap that previously left
``NotebookWorker`` without a cache: in API mode the worker now reads and
writes ``executed_notebooks`` over the Worker API instead of opening
``clm_cache.db`` directly. Without these endpoints (and the adapter that
calls them), Stage 4 Completed/Trainer/Partial HTML jobs would
unconditionally re-execute their notebooks under Docker — the same bug
that PR #71 fixed for direct mode.
"""

from __future__ import annotations

import gzip
import pickle
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from nbformat.v4 import new_code_cell, new_notebook

from clm.infrastructure.api.server import WorkerApiServer
from clm.infrastructure.database.executed_notebook_cache import ExecutedNotebookCache
from clm.infrastructure.database.schema import init_database


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test_jobs.db"
    init_database(path)
    return path


@pytest.fixture
def cache_db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_clm_cache.db"


@pytest.fixture
def client(db_path: Path, cache_db_path: Path):
    server = WorkerApiServer(db_path, cache_db_path=cache_db_path)
    app = server._create_app()
    with TestClient(app) as c:
        yield c


def _sample_nb():
    nb = new_notebook()
    nb.cells.append(new_code_cell(source="print('hi')"))
    return nb


def _params() -> dict[str, str]:
    return {
        "input_file": "/tmp/foo.py",
        "content_hash": "abc123",
        "language": "en",
        "prog_lang": "python",
    }


class TestExecutedNotebookCacheEndpoints:
    def test_get_miss_returns_404(self, client):
        response = client.get("/api/worker/cache/executed_notebook", params=_params())
        assert response.status_code == 404

    def test_post_then_get_round_trips_notebook(self, client, cache_db_path):
        nb = _sample_nb()
        pickle_bytes = pickle.dumps(nb)
        body = gzip.compress(pickle_bytes)

        post_response = client.post(
            "/api/worker/cache/executed_notebook",
            params=_params(),
            content=body,
            headers={"Content-Type": "application/octet-stream"},
        )
        assert post_response.status_code == 200
        assert post_response.json()["acknowledged"] is True
        assert post_response.json()["bytes_stored"] == len(pickle_bytes)

        # Direct verification: the host's SQLite cache has the entry now,
        # which is exactly the state Stage 4 direct-mode consumers need.
        with ExecutedNotebookCache(cache_db_path) as cache:
            cached_nb = cache.get(**_params())
        assert cached_nb is not None
        assert cached_nb.cells[0].source == "print('hi')"

        # GET round-trip: httpx auto-decompresses gzip on the way back, so
        # response.content is already the raw pickle bytes.
        get_response = client.get("/api/worker/cache/executed_notebook", params=_params())
        assert get_response.status_code == 200
        round_tripped = pickle.loads(get_response.content)
        assert round_tripped.cells[0].source == "print('hi')"

    def test_post_with_non_gzip_body_returns_400(self, client):
        response = client.post(
            "/api/worker/cache/executed_notebook",
            params=_params(),
            content=b"not gzip",
            headers={"Content-Type": "application/octet-stream"},
        )
        assert response.status_code == 400
        assert "gzip" in response.json()["detail"].lower()

    def test_get_requires_all_key_parts(self, client):
        # FastAPI returns 422 when a required query param is missing.
        partial = dict(_params())
        partial.pop("language")
        response = client.get("/api/worker/cache/executed_notebook", params=partial)
        assert response.status_code == 422

    def test_overwrite_replaces_existing_entry(self, client, cache_db_path):
        nb1 = _sample_nb()
        nb1.cells.append(new_code_cell(source="x = 1"))
        client.post(
            "/api/worker/cache/executed_notebook",
            params=_params(),
            content=gzip.compress(pickle.dumps(nb1)),
            headers={"Content-Type": "application/octet-stream"},
        )

        nb2 = _sample_nb()
        nb2.cells.append(new_code_cell(source="y = 2"))
        client.post(
            "/api/worker/cache/executed_notebook",
            params=_params(),
            content=gzip.compress(pickle.dumps(nb2)),
            headers={"Content-Type": "application/octet-stream"},
        )

        # ExecutedNotebookCache.get uses ORDER BY created_at DESC LIMIT 1,
        # so we should see nb2. The UNIQUE(input_file, content_hash, language,
        # prog_lang) constraint plus INSERT OR REPLACE keeps row count at 1.
        with ExecutedNotebookCache(cache_db_path) as cache:
            cached_nb = cache.get(**_params())
            stats = cache.get_stats()
        assert cached_nb is not None
        assert cached_nb.cells[-1].source == "y = 2"
        assert stats["total_entries"] == 1

    def test_falls_back_to_job_db_when_no_cache_db_path(self, db_path: Path, tmp_path: Path):
        """Backwards-compat: callers that constructed WorkerApiServer without
        a cache_db_path should still see endpoint behavior — the cache table
        gets created on-demand in the job DB."""
        server = WorkerApiServer(db_path)  # no cache_db_path
        app = server._create_app()
        with TestClient(app) as fallback_client:
            response = fallback_client.get("/api/worker/cache/executed_notebook", params=_params())
            assert response.status_code == 404

            nb = _sample_nb()
            post = fallback_client.post(
                "/api/worker/cache/executed_notebook",
                params=_params(),
                content=gzip.compress(pickle.dumps(nb)),
                headers={"Content-Type": "application/octet-stream"},
            )
            assert post.status_code == 200

            # Entry landed in the job DB (which has the executed_notebooks
            # table auto-created by ExecutedNotebookCache).
            with ExecutedNotebookCache(db_path) as cache:
                cached = cache.get(**_params())
            assert cached is not None
