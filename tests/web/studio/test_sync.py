"""Tests for P3b — streamed sync-to-other-language.

The subprocess is **injected** (``stream=``) or the runner is monkeypatched, so
no real ``clm slides sync`` (LLM/network) ever runs: these assert the
orchestration — command construction, WS event sequence, self-write marking,
in-flight dedupe, and the route's validation/auth.
"""

from __future__ import annotations

import asyncio
import sys

import pytest
from fastapi.testclient import TestClient

from clm.web.app import create_app
from clm.web.studio import sync_runner
from clm.web.studio.service import InvalidStructuralOpError, StudioService

from .conftest import Bilingual, Course

TOKEN = "test-studio-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class TestResolveSyncCommand:
    def test_pair_builds_sync_command(self, bilingual_service: StudioService, bilingual: Bilingual):
        cmd, de_id, en_id = bilingual_service.resolve_sync_command(bilingual.de_id)
        assert cmd[0] == sys.executable
        assert cmd[1:6] == ["-m", "clm", "slides", "sync", "apply"]
        assert cmd[-1] == str(bilingual.de_path)
        assert de_id == bilingual.de_id and en_id == bilingual.en_id

    def test_either_half_resolves_same_pair(
        self, bilingual_service: StudioService, bilingual: Bilingual
    ):
        _, de_a, en_a = bilingual_service.resolve_sync_command(bilingual.de_id)
        _, de_b, en_b = bilingual_service.resolve_sync_command(bilingual.en_id)
        assert (de_a, en_a) == (de_b, en_b)

    def test_no_twin_rejected(self, service: StudioService, course: Course):
        with pytest.raises(InvalidStructuralOpError):
            service.resolve_sync_command(course.deck_id)


class TestRunSync:
    def test_streams_events_and_marks_self_writes(
        self, bilingual_service: StudioService, bilingual: Bilingual, monkeypatch
    ):
        from clm.web.api.websocket import ws_manager

        msgs: list[tuple[str | None, dict]] = []

        async def fake_broadcast(message, channel=None):  # noqa: ANN001
            msgs.append((channel, message))

        monkeypatch.setattr(ws_manager, "broadcast", fake_broadcast)

        async def fake_stream(cmd, on_line):  # noqa: ANN001
            await on_line("classifying pair…")
            await on_line("applied 1 edit")
            return 0

        asyncio.run(sync_runner.run_sync(bilingual_service, bilingual.de_id, stream=fake_stream))

        types = [m["type"] for _, m in msgs]
        assert types[0] == "sync-started"
        assert "sync-progress" in types
        assert types[-1] == "sync-done"
        assert msgs[-1][1]["ok"] is True
        assert all(ch == "studio" for ch, _ in msgs)
        # Both halves are suppressed from the watcher during the write.
        assert bilingual_service.is_self_write(bilingual.de_id)
        assert bilingual_service.is_self_write(bilingual.en_id)

    def test_nonzero_exit_reports_failure(
        self, bilingual_service: StudioService, bilingual: Bilingual, monkeypatch
    ):
        from clm.web.api.websocket import ws_manager

        msgs: list[dict] = []
        monkeypatch.setattr(
            ws_manager, "broadcast", lambda message, channel=None: _collect(msgs, message)
        )

        async def fake_stream(cmd, on_line):  # noqa: ANN001
            await on_line("conflict left unresolved")
            return 2

        asyncio.run(sync_runner.run_sync(bilingual_service, bilingual.de_id, stream=fake_stream))
        assert msgs[-1]["type"] == "sync-done" and msgs[-1]["ok"] is False

    def test_stream_exception_reports_failure(
        self, bilingual_service: StudioService, bilingual: Bilingual, monkeypatch
    ):
        from clm.web.api.websocket import ws_manager

        msgs: list[dict] = []
        monkeypatch.setattr(
            ws_manager, "broadcast", lambda message, channel=None: _collect(msgs, message)
        )

        async def boom(cmd, on_line):  # noqa: ANN001
            raise RuntimeError("spawn failed")

        asyncio.run(sync_runner.run_sync(bilingual_service, bilingual.de_id, stream=boom))
        assert msgs[-1]["type"] == "sync-done" and msgs[-1]["ok"] is False
        assert "error" in msgs[-1]


async def _collect(bucket: list[dict], message: dict) -> None:
    bucket.append(message)


class TestSyncEndpoint:
    @pytest.fixture()
    def bilingual_client(self, bilingual: Bilingual) -> TestClient:
        app = create_app(
            db_path=bilingual.slides_dir.parent / "jobs.db",
            spec_path=bilingual.spec_path,
            studio_token=TOKEN,
        )
        return TestClient(app)

    def test_sync_starts(self, bilingual_client: TestClient, bilingual: Bilingual, monkeypatch):
        called: list[str] = []

        async def fake_run(service, deck_id, **kwargs):  # noqa: ANN001
            called.append(deck_id)

        monkeypatch.setattr(sync_runner, "run_sync", fake_run)
        r = bilingual_client.post(
            "/api/studio/deck/sync", headers=AUTH, json={"deck_id": bilingual.de_id}
        )
        assert r.status_code == 200
        assert r.json()["started"] is True

    def test_sync_dedupe_is_409(self, bilingual_client: TestClient, bilingual: Bilingual):
        # Pre-occupy the in-flight slot for the DE half → a POST must 409.
        service = bilingual_client.app.state.studio_service
        _, de_id, _ = service.resolve_sync_command(bilingual.de_id)
        assert service.try_begin_sync(de_id)
        r = bilingual_client.post(
            "/api/studio/deck/sync", headers=AUTH, json={"deck_id": bilingual.de_id}
        )
        assert r.status_code == 409
        service.end_sync(de_id)

    def test_sync_no_twin_is_400(self, course: Course):
        app = create_app(
            db_path=course.slides_dir.parent / "jobs.db",
            spec_path=course.spec_path,
            studio_token=TOKEN,
        )
        client = TestClient(app)
        r = client.post("/api/studio/deck/sync", headers=AUTH, json={"deck_id": course.deck_id})
        assert r.status_code == 400

    def test_sync_requires_token(self, bilingual_client: TestClient, bilingual: Bilingual):
        r = bilingual_client.post("/api/studio/deck/sync", json={"deck_id": bilingual.de_id})
        assert r.status_code == 401
