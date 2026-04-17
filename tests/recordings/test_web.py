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

    def test_get_lectures_with_course(self, app, recording_root: Path):
        """When a Course is cached, the lectures page shows section names
        and slide deck display names."""
        from clm.core.utils.text_utils import Text

        mock_course = MagicMock()
        mock_section = MagicMock()
        mock_section.name = Text(de="Woche 1", en="Week 1")
        mock_nb = MagicMock()
        mock_nb.title = Text(de="Einführung", en="Introduction")
        mock_nb.number_in_section = 1
        mock_nb.file_name.return_value = "01 Einführung"
        mock_section.notebooks = [mock_nb]
        mock_course.sections = [mock_section]
        mock_course.output_dir_name = Text(de="test-course-de", en="test-course-en")
        app.state.course = mock_course

        with TestClient(app) as c:
            # Default lang is "de"
            resp = c.get("/lectures")
        assert resp.status_code == 200
        assert "Woche 1" in resp.text
        assert "01 Einführung" in resp.text

    def test_get_lectures_english(self, app, recording_root: Path):
        """When lang=en cookie is set, English names are shown."""
        from clm.core.utils.text_utils import Text

        mock_course = MagicMock()
        mock_section = MagicMock()
        mock_section.name = Text(de="Woche 1", en="Week 1")
        mock_nb = MagicMock()
        mock_nb.title = Text(de="Einführung", en="Introduction")
        mock_nb.number_in_section = 1
        mock_nb.file_name.return_value = "01 Introduction"
        mock_section.notebooks = [mock_nb]
        mock_course.sections = [mock_section]
        mock_course.output_dir_name = Text(de="test-course-de", en="test-course-en")
        app.state.course = mock_course

        with TestClient(app, cookies={"clm_lang": "en"}) as c:
            resp = c.get("/lectures")
        assert resp.status_code == 200
        assert "Week 1" in resp.text
        assert "01 Introduction" in resp.text

    def test_lectures_shows_course_slug(self, app, recording_root: Path):
        """The arm form should include the correct course_slug."""
        from clm.core.utils.text_utils import Text

        mock_course = MagicMock()
        mock_section = MagicMock()
        mock_section.name = Text(de="Woche 1", en="Week 1")
        mock_nb = MagicMock()
        mock_nb.title = Text(de="Intro", en="Intro")
        mock_nb.number_in_section = 1
        mock_nb.file_name.return_value = "01 Intro"
        mock_section.notebooks = [mock_nb]
        mock_course.sections = [mock_section]
        mock_course.output_dir_name = Text(de="kurs-de", en="course-en")
        app.state.course = mock_course

        with TestClient(app) as c:
            resp = c.get("/lectures")
        assert 'value="kurs-de"' in resp.text


# ---------------------------------------------------------------------------
# Language selection
# ---------------------------------------------------------------------------


class TestLanguageSelection:
    def test_set_lang_de(self, client: TestClient):
        resp = client.post("/set-lang", data={"lang": "de"}, follow_redirects=False)
        assert resp.status_code == 200
        assert resp.headers.get("hx-redirect") == "/lectures"
        assert "clm_lang" in resp.cookies
        assert resp.cookies["clm_lang"] == "de"

    def test_set_lang_en(self, client: TestClient):
        resp = client.post("/set-lang", data={"lang": "en"}, follow_redirects=False)
        assert resp.status_code == 200
        assert resp.cookies["clm_lang"] == "en"

    def test_set_lang_invalid_defaults_to_de(self, client: TestClient):
        resp = client.post("/set-lang", data={"lang": "fr"}, follow_redirects=False)
        assert resp.status_code == 200
        assert resp.cookies["clm_lang"] == "de"


# ---------------------------------------------------------------------------
# Lectures refresh
# ---------------------------------------------------------------------------


class TestLecturesRefresh:
    def test_refresh_rebuilds_course(self, app, client: TestClient, tmp_path: Path):
        app.state.spec_file = tmp_path / "course.xml"
        with patch("clm.recordings.web.app._build_course") as mock_build:
            mock_build.return_value = MagicMock()
            resp = client.post("/lectures/refresh", follow_redirects=False)
        assert resp.status_code == 200
        assert resp.headers.get("hx-redirect") == "/lectures"
        mock_build.assert_called_once_with(app.state.spec_file)

    def test_refresh_without_spec_file(self, app, client: TestClient):
        app.state.spec_file = None
        resp = client.post("/lectures/refresh", follow_redirects=False)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Arm / Disarm
# ---------------------------------------------------------------------------


class TestArmDisarm:
    def test_arm_deck(self, client: TestClient):
        resp = client.post(
            "/arm",
            data={
                "course_slug": "python-basics",
                "section_name": "intro",
                "deck_name": "01 Hello",
                "part_number": "0",
            },
        )
        assert resp.status_code == 200
        assert "python-basics" in resp.text
        assert "01 Hello" in resp.text

    def test_arm_changes_state(self, app, client: TestClient):
        client.post(
            "/arm",
            data={
                "course_slug": "c",
                "section_name": "s",
                "deck_name": "01 Deck",
                "part_number": "0",
            },
        )
        session = app.state.session
        assert session.state is SessionState.ARMED
        assert session.armed_deck is not None
        assert session.armed_deck.deck_name == "01 Deck"

    def test_arm_with_part_number(self, app, client: TestClient):
        client.post(
            "/arm",
            data={
                "course_slug": "c",
                "section_name": "s",
                "deck_name": "03 Intro",
                "part_number": "2",
            },
        )
        session = app.state.session
        assert session.armed_deck.part_number == 2

    def test_disarm(self, client: TestClient):
        client.post(
            "/arm",
            data={
                "course_slug": "c",
                "section_name": "s",
                "deck_name": "01 Deck",
                "part_number": "0",
            },
        )
        resp = client.post("/disarm")
        assert resp.status_code == 200
        assert "idle" in resp.text

    def test_disarm_from_idle(self, client: TestClient):
        resp = client.post("/disarm")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Record / Stop (one-click, Phase 1)
# ---------------------------------------------------------------------------


class TestRecordStop:
    def test_record_arms_and_starts_obs(self, app, client: TestClient):
        resp = client.post(
            "/record",
            data={
                "course_slug": "c",
                "section_name": "s",
                "deck_name": "01 Deck",
                "part_number": "0",
            },
        )
        assert resp.status_code == 200

        session = app.state.session
        assert session.state is SessionState.ARMED
        assert session.armed_deck is not None
        assert session.armed_deck.deck_name == "01 Deck"
        app.state.obs.start_record.assert_called_once_with()

    def test_record_passes_part_number(self, app, client: TestClient):
        client.post(
            "/record",
            data={
                "course_slug": "c",
                "section_name": "s",
                "deck_name": "03 Intro",
                "part_number": "2",
            },
        )
        session = app.state.session
        assert session.armed_deck.part_number == 2

    def test_record_obs_failure_returns_502_but_leaves_armed(self, app, client: TestClient):
        """If OBS rejects start_record, /record returns 502 but the deck
        stays armed so the user can start recording manually or retry."""
        app.state.obs.start_record.side_effect = ConnectionError("OBS not connected")

        resp = client.post(
            "/record",
            data={
                "course_slug": "c",
                "section_name": "s",
                "deck_name": "01 Deck",
                "part_number": "0",
            },
        )
        assert resp.status_code == 502
        assert "OBS" in resp.text

        session = app.state.session
        assert session.state is SessionState.ARMED
        assert session.armed_deck.deck_name == "01 Deck"

    def test_record_while_recording_returns_409(self, app, client: TestClient):
        """Re-recording while one is in flight is a state-machine conflict."""
        # Arm + simulate OBS STARTED to reach RECORDING state.
        client.post(
            "/record",
            data={
                "course_slug": "c",
                "section_name": "s",
                "deck_name": "01 Deck",
                "part_number": "0",
            },
        )
        from clm.recordings.workflow.obs import RecordingEvent

        for cb in app.state.obs._record_callbacks:
            cb(RecordingEvent(output_active=True, output_state="started"))

        resp = client.post(
            "/record",
            data={
                "course_slug": "c",
                "section_name": "s",
                "deck_name": "02 Other",
                "part_number": "0",
            },
        )
        assert resp.status_code == 409

    def test_stop_calls_obs(self, app, client: TestClient):
        resp = client.post("/stop")
        assert resp.status_code == 200
        app.state.obs.stop_record.assert_called_once_with()

    def test_stop_obs_failure_returns_502(self, app, client: TestClient):
        app.state.obs.stop_record.side_effect = ConnectionError("not recording")
        resp = client.post("/stop")
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


class TestStatus:
    def test_status_json(self, client: TestClient):
        resp = client.get("/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "idle"
        assert data["armed_deck"] is None
        assert "obs_connected" in data

    def test_status_json_after_arm(self, client: TestClient):
        client.post(
            "/arm",
            data={
                "course_slug": "c",
                "section_name": "s",
                "deck_name": "01 Deck",
                "part_number": "0",
            },
        )
        resp = client.get("/status")
        data = resp.json()
        assert data["state"] == "armed"
        assert data["armed_deck"]["deck_name"] == "01 Deck"
        assert data["armed_deck"]["part_number"] == 0

    def test_status_json_backward_compat(self, client: TestClient):
        """The JSON response still includes armed_topic for backward compat."""
        client.post(
            "/arm",
            data={
                "course_slug": "c",
                "section_name": "s",
                "deck_name": "01 Deck",
                "part_number": "0",
            },
        )
        data = client.get("/status").json()
        assert data["armed_topic"] is not None
        assert data["armed_topic"] == data["armed_deck"]

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
        """Arming a deck should push a state_changed event to the SSE queue."""
        client.post(
            "/arm",
            data={
                "course_slug": "c",
                "section_name": "s",
                "deck_name": "01 Deck",
                "part_number": "0",
            },
        )
        queue = app.state.sse_queue
        assert not queue.empty()

    def test_job_payloads_map_to_event_job(self):
        """Job-prefixed SSE payloads are classified as ``event: job``.

        Splitting event names lets the dashboard bind refreshes on a per-
        panel basis instead of flooding every panel on every tick. The
        helper is unit-tested directly so we don't have to stream the
        open-ended ``/events`` endpoint from a test client.
        """
        from clm.recordings.web.routes import _sse_event_name_for

        assert _sse_event_name_for("job") == "job"
        assert _sse_event_name_for("job:abc-123") == "job"
        assert _sse_event_name_for("submitted:abc-123") == "job"

    def test_status_payloads_map_to_event_status(self):
        """Non-job payloads stay on ``event: status`` — status panel binding."""
        from clm.recordings.web.routes import _sse_event_name_for

        assert _sse_event_name_for("state_changed") == "status"
        assert _sse_event_name_for("watcher_error") == "status"

    def test_jobs_panel_refreshes_on_sse_job(self, client: TestClient):
        """The jobs-panel must bind its refresh to ``sse:job`` (new) in
        addition to the legacy ``sse:status`` so per-job ticks don't
        require every panel to refresh.
        """
        html = client.get("/").text
        assert 'id="jobs-panel"' in html
        panel_idx = html.index('id="jobs-panel"')
        tag_start = html.rfind("<", 0, panel_idx)
        tag_end = html.index(">", panel_idx)
        panel_tag = html[tag_start : tag_end + 1]
        assert "sse:job" in panel_tag


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


# ---------------------------------------------------------------------------
# OBS controls
# ---------------------------------------------------------------------------


class TestObsControls:
    def test_obs_connect_success(self, app, client: TestClient):
        obs = app.state.obs
        obs.connect.side_effect = None  # Clear the ConnectionError side_effect
        obs.connected = True
        resp = client.post("/obs/connect")
        assert resp.status_code == 200
        obs.connect.assert_called()
        assert "connected" in resp.text

    def test_obs_connect_failure(self, app, client: TestClient):
        obs = app.state.obs
        obs.connect.side_effect = ConnectionError("OBS not running")
        obs.connected = False
        resp = client.post("/obs/connect")
        assert resp.status_code == 200
        assert "disconnected" in resp.text

    def test_obs_disconnect(self, app, client: TestClient):
        obs = app.state.obs
        obs.connected = False
        resp = client.post("/obs/disconnect")
        assert resp.status_code == 200
        obs.disconnect.assert_called()

    def test_status_shows_connect_button_when_disconnected(self, client: TestClient):
        resp = client.get("/status-partial")
        assert resp.status_code == 200
        assert "disconnected" in resp.text
        assert "/obs/connect" in resp.text

    def test_status_shows_disconnect_button_when_connected(self, app, client: TestClient):
        app.state.obs.connected = True
        resp = client.get("/status-partial")
        assert resp.status_code == 200
        assert "/obs/disconnect" in resp.text
