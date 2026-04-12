"""Tests for the recordings web dashboard routes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from clm.recordings.workflow.directories import ensure_root
from clm.recordings.workflow.session import SessionState

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def recording_root(tmp_path: Path) -> Path:
    root = tmp_path / "recordings"
    ensure_root(root)
    return root


@pytest.fixture()
def app(recording_root: Path):
    """Create a test app with mocked OBS (no real connection)."""
    with patch("clm.recordings.workflow.obs.ObsClient") as MockObs:
        mock_obs = MagicMock()
        mock_obs.connected = False
        # Register callbacks so the session manager wiring works
        mock_obs._record_callbacks = []
        mock_obs.on_record_state_changed.side_effect = lambda cb: mock_obs._record_callbacks.append(
            cb
        )
        MockObs.return_value = mock_obs

        from clm.recordings.web.app import create_app

        application = create_app(
            recordings_root=recording_root,
            obs_host="localhost",
            obs_port=4455,
        )
        # Prevent lifespan from trying to connect
        mock_obs.connect.side_effect = ConnectionError("OBS not running")
        yield application


@pytest.fixture()
def client(app) -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


class TestDashboard:
    def test_get_dashboard(self, client: TestClient):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "CLM Recordings" in resp.text
        assert "Session Status" in resp.text

    def test_dashboard_shows_idle_state(self, client: TestClient):
        resp = client.get("/")
        assert "idle" in resp.text

    def test_dashboard_sse_on_wrapper_not_panel(self, client: TestClient):
        """SSE connection must live on a wrapper element, not on
        ``#status-panel`` directly.  If ``sse-swap`` is placed on the
        panel itself, raw SSE data text (e.g. 'state_changed') replaces
        the panel HTML, destroying buttons before users can click them.
        """
        html = client.get("/").text
        # sse-connect should NOT be on #status-panel
        assert 'id="status-panel"' in html
        # The panel must not carry sse-swap — that causes raw-text overwrites
        panel_idx = html.index('id="status-panel"')
        # Find the opening tag that contains id="status-panel"
        tag_start = html.rfind("<", 0, panel_idx)
        tag_end = html.index(">", panel_idx)
        panel_tag = html[tag_start : tag_end + 1]
        assert "sse-swap" not in panel_tag
        assert "sse-connect" not in panel_tag


# ---------------------------------------------------------------------------
# Lectures
# ---------------------------------------------------------------------------


class TestLectures:
    def test_get_lectures_no_spec(self, client: TestClient):
        resp = client.get("/lectures")
        assert resp.status_code == 200
        assert "No course spec file" in resp.text

    def test_get_lectures_with_spec(self, app, recording_root: Path, tmp_path: Path):
        spec_xml = tmp_path / "course.xml"
        spec_xml.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
            <course>
                <name>
                    <de>Test Kurs</de>
                    <en>Test Course</en>
                </name>
                <prog-lang>python</prog-lang>
                <description>
                    <de>Beschreibung</de>
                    <en>Description</en>
                </description>
                <certificate>
                    <de>Zertifikat</de>
                    <en>Certificate</en>
                </certificate>
                <sections>
                    <section>
                        <name>
                            <de>Einleitung</de>
                            <en>Introduction</en>
                        </name>
                        <topics>
                            <topic>intro/overview</topic>
                            <topic>intro/setup</topic>
                        </topics>
                    </section>
                </sections>
            </course>
            """,
            encoding="utf-8",
        )
        app.state.spec_file = spec_xml

        with TestClient(app) as c:
            resp = c.get("/lectures")
        assert resp.status_code == 200
        assert "Introduction" in resp.text
        assert "overview" in resp.text
        assert "setup" in resp.text


# ---------------------------------------------------------------------------
# Arm / Disarm
# ---------------------------------------------------------------------------


class TestArmDisarm:
    def test_arm_topic(self, client: TestClient):
        resp = client.post(
            "/arm",
            data={
                "course_slug": "python-basics",
                "section_name": "intro",
                "topic_name": "hello",
            },
        )
        assert resp.status_code == 200
        assert "python-basics" in resp.text
        assert "hello" in resp.text

    def test_arm_changes_state(self, app, client: TestClient):
        client.post(
            "/arm",
            data={
                "course_slug": "c",
                "section_name": "s",
                "topic_name": "t",
            },
        )
        session = app.state.session
        assert session.state is SessionState.ARMED
        assert session.armed_topic is not None
        assert session.armed_topic.topic_name == "t"

    def test_disarm(self, client: TestClient):
        # Arm first
        client.post(
            "/arm",
            data={
                "course_slug": "c",
                "section_name": "s",
                "topic_name": "t",
            },
        )
        # Then disarm
        resp = client.post("/disarm")
        assert resp.status_code == 200
        assert "idle" in resp.text

    def test_disarm_from_idle(self, client: TestClient):
        resp = client.post("/disarm")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


class TestStatus:
    def test_status_json(self, client: TestClient):
        resp = client.get("/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "idle"
        assert data["armed_topic"] is None
        assert "obs_connected" in data

    def test_status_json_after_arm(self, client: TestClient):
        client.post(
            "/arm",
            data={
                "course_slug": "c",
                "section_name": "s",
                "topic_name": "t",
            },
        )
        resp = client.get("/status")
        data = resp.json()
        assert data["state"] == "armed"
        assert data["armed_topic"]["topic_name"] == "t"

    def test_status_partial(self, client: TestClient):
        resp = client.get("/status-partial")
        assert resp.status_code == 200
        assert "Session Status" in resp.text

    def test_pairs_partial(self, client: TestClient):
        resp = client.get("/pairs-partial")
        assert resp.status_code == 200
        assert "Pending Pairs" in resp.text


# ---------------------------------------------------------------------------
# SSE Events
# ---------------------------------------------------------------------------


class TestSSEEvents:
    def test_events_route_is_registered(self, app):
        """SSE endpoint should be registered in the app."""
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/events" in routes

    def test_sse_queue_is_configured(self, app):
        """App should have an SSE queue in state."""
        assert hasattr(app.state, "sse_queue")
        assert app.state.sse_queue is not None

    def test_arm_pushes_to_sse_queue(self, app, client: TestClient):
        """Arming a topic should push a state_changed event to the SSE queue."""
        client.post(
            "/arm",
            data={"course_slug": "c", "section_name": "s", "topic_name": "t"},
        )
        # The session's on_state_change callback pushes to the queue
        queue = app.state.sse_queue
        assert not queue.empty()


# ---------------------------------------------------------------------------
# Pending pairs display
# ---------------------------------------------------------------------------


class TestPendingPairsDisplay:
    def test_dashboard_shows_no_pairs(self, client: TestClient):
        resp = client.get("/")
        assert "No video + audio pairs" in resp.text

    def test_dashboard_shows_pairs(self, app, recording_root: Path):
        tp = recording_root / "to-process" / "course" / "section"
        tp.mkdir(parents=True)
        (tp / "topic--RAW.mp4").write_bytes(b"video")
        (tp / "topic--RAW.wav").write_bytes(b"audio")

        with TestClient(app) as c:
            resp = c.get("/")
        assert "topic" in resp.text
        assert "1" in resp.text  # pair count


# ---------------------------------------------------------------------------
# Watcher controls
# ---------------------------------------------------------------------------


class TestWatcherControls:
    def test_dashboard_shows_watcher_section(self, client: TestClient):
        resp = client.get("/")
        assert "File Watcher" in resp.text
        assert "stopped" in resp.text

    def test_dashboard_shows_watcher_mode(self, client: TestClient):
        resp = client.get("/")
        # Default backend is now "onnx" (Phase C); the dashboard renders
        # the backend's machine name in the watcher status panel.
        assert "onnx" in resp.text

    def test_start_watcher(self, app, client: TestClient):
        resp = client.post("/watcher/start")
        assert resp.status_code == 200
        assert app.state.watcher.running
        assert "running" in resp.text
        app.state.watcher.stop()

    def test_stop_watcher(self, app, client: TestClient):
        app.state.watcher.start()
        resp = client.post("/watcher/stop")
        assert resp.status_code == 200
        assert not app.state.watcher.running
        assert "stopped" in resp.text

    def test_status_partial_includes_watcher(self, client: TestClient):
        resp = client.get("/status-partial")
        assert resp.status_code == 200
        assert "File Watcher" in resp.text
