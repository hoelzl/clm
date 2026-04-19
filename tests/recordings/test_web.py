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
        # Default to connected so routes guarded by the OBS-connected check
        # (``/arm``, ``/record``) are exercised in the happy path. Tests that
        # need the disconnected state override this locally.
        mock_obs.connected = True
        mock_obs.connection_state = "connected"
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
        # Match the attributes themselves, not CSS-selector substrings used by
        # ``hx-trigger="sse:status from:closest [sse-connect]"``.
        assert "sse-swap=" not in panel_tag
        assert "sse-connect=" not in panel_tag


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

    def test_successful_part_2_does_not_mask_failed_part_1(self, app, recording_root: Path):
        """An unresolved failure on part 1 stays visible even when part 2 succeeds.

        Slots are tracked per ``(deck, part)`` so a successful part 2
        can't pretend the earlier part-1 failure is gone. The newest
        entry in the failed slot drives the badge.
        """
        from datetime import datetime, timedelta, timezone
        from pathlib import Path as _Path

        from clm.core.utils.text_utils import Text
        from clm.recordings.workflow.jobs import JobState, ProcessingJob

        mock_course = MagicMock()
        mock_section = MagicMock()
        mock_section.name = Text(de="Woche 1", en="Week 1")
        mock_nb = MagicMock()
        mock_nb.title = Text(de="Einführung", en="Intro")
        mock_nb.number_in_section = 1
        mock_nb.file_name.return_value = "01 Einführung"
        mock_section.notebooks = [mock_nb]
        mock_course.sections = [mock_section]
        mock_course.output_dir_name = Text(de="kurs-de", en="course-en")
        app.state.course = mock_course

        tp = recording_root / "to-process" / "kurs-de" / "Woche 1"
        tp.mkdir(parents=True)
        part1_raw = tp / "01 Einführung (part 1)--RAW.mp4"
        part1_raw.write_bytes(b"p1")
        part2_raw = tp / "01 Einführung (part 2)--RAW.mp4"
        part2_raw.write_bytes(b"p2")

        earlier = datetime(2026, 4, 18, 10, 0, 0, tzinfo=timezone.utc)
        later = earlier + timedelta(minutes=10)
        part1_failed = ProcessingJob(
            backend_name="auphonic",
            raw_path=part1_raw,
            final_path=_Path("/tmp/p1.mp4"),
            relative_dir=_Path("kurs-de/Woche 1"),
            state=JobState.FAILED,
            error="validation failed",
            created_at=earlier,
        )
        part2_done = ProcessingJob(
            backend_name="auphonic",
            raw_path=part2_raw,
            final_path=_Path("/tmp/p2.mp4"),
            relative_dir=_Path("kurs-de/Woche 1"),
            state=JobState.COMPLETED,
            created_at=later,
        )
        app.state.job_manager._store_job(part1_failed)
        app.state.job_manager._store_job(part2_done)

        with TestClient(app) as c:
            resp = c.get("/lectures")
        assert resp.status_code == 200
        assert "processing failed" in resp.text

    def test_successful_retry_clears_failed_badge(self, app, recording_root: Path):
        """A later COMPLETED job for the same deck clears the failed indicator.

        Regression from Phase-4 smoke test: ``_get_failed_jobs_map``
        used to pick the first FAILED job found per deck, so a
        successful retry left the red ``processing failed`` pill
        hanging on the row forever. The fix keys on "most recent job
        per deck" and only flags the deck when that newest entry is
        actually FAILED.
        """
        from datetime import datetime, timedelta, timezone
        from pathlib import Path as _Path

        from clm.core.utils.text_utils import Text
        from clm.recordings.workflow.jobs import JobState, ProcessingJob

        mock_course = MagicMock()
        mock_section = MagicMock()
        mock_section.name = Text(de="Woche 1", en="Week 1")
        mock_nb = MagicMock()
        mock_nb.title = Text(de="Einführung", en="Intro")
        mock_nb.number_in_section = 1
        mock_nb.file_name.return_value = "01 Einführung"
        mock_section.notebooks = [mock_nb]
        mock_course.sections = [mock_section]
        mock_course.output_dir_name = Text(de="kurs-de", en="course-en")
        app.state.course = mock_course

        tp = recording_root / "to-process" / "kurs-de" / "Woche 1"
        tp.mkdir(parents=True)
        raw = tp / "01 Einführung--RAW.mp4"
        raw.write_bytes(b"raw")

        # Older FAILED job, newer COMPLETED job — same raw-path stem.
        earlier = datetime(2026, 4, 18, 10, 0, 0, tzinfo=timezone.utc)
        later = earlier + timedelta(minutes=5)
        failed = ProcessingJob(
            backend_name="auphonic",
            raw_path=raw,
            final_path=_Path("/tmp/final.mp4"),
            relative_dir=_Path("kurs-de/Woche 1"),
            state=JobState.FAILED,
            error="validation failed",
            created_at=earlier,
        )
        succeeded = ProcessingJob(
            backend_name="auphonic",
            raw_path=raw,
            final_path=_Path("/tmp/final.mp4"),
            relative_dir=_Path("kurs-de/Woche 1"),
            state=JobState.COMPLETED,
            created_at=later,
        )
        app.state.job_manager._store_job(failed)
        app.state.job_manager._store_job(succeeded)

        with TestClient(app) as c:
            resp = c.get("/lectures")
        assert resp.status_code == 200
        assert "processing failed" not in resp.text

    def test_lectures_renders_failed_badge_even_when_state_is_recorded(
        self, app, recording_root: Path
    ):
        """Row shows a ``processing failed`` badge when a job failed and the
        raw file still exists.

        Regression from Phase-4 smoke test: the deck-status scanner
        prefers ``recorded`` over ``failed`` when a raw file is on disk,
        so the user had no visual cue that Auphonic had rejected the
        last submission. The template now renders the failed badge
        independently of the main state whenever ``failed_job_id`` is
        set. Only the presence of the badge is asserted — the main
        state badge remains untouched.
        """
        from pathlib import Path as _Path

        from clm.core.utils.text_utils import Text
        from clm.recordings.workflow.jobs import JobState, ProcessingJob

        mock_course = MagicMock()
        mock_section = MagicMock()
        mock_section.name = Text(de="Woche 1", en="Week 1")
        mock_nb = MagicMock()
        mock_nb.title = Text(de="Einführung", en="Intro")
        mock_nb.number_in_section = 1
        mock_nb.file_name.return_value = "01 Einführung"
        mock_section.notebooks = [mock_nb]
        mock_course.sections = [mock_section]
        mock_course.output_dir_name = Text(de="kurs-de", en="course-en")
        app.state.course = mock_course

        tp = recording_root / "to-process" / "kurs-de" / "Woche 1"
        tp.mkdir(parents=True)
        raw = tp / "01 Einführung--RAW.mp4"
        raw.write_bytes(b"raw")

        failed = ProcessingJob(
            backend_name="auphonic",
            raw_path=raw,
            final_path=_Path("/tmp/final.mp4"),
            relative_dir=_Path("kurs-de/Woche 1"),
            state=JobState.FAILED,
            error="validation failed",
        )
        app.state.job_manager._store_job(failed)

        with TestClient(app) as c:
            resp = c.get("/lectures")
        assert resp.status_code == 200
        assert "processing failed" in resp.text
        assert "badge-recorded" in resp.text  # main state still recorded


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

    def test_arm_resolves_lecture_id(self, app, client: TestClient):
        """/arm derives a stable lecture_id and threads it into the session."""
        client.post(
            "/arm",
            data={
                "course_slug": "c",
                "section_name": "intro",
                "deck_name": "01 Hello",
                "part_number": "0",
            },
        )
        session = app.state.session
        assert session.armed_deck is not None
        assert session.armed_deck.lecture_id == "intro::01 Hello"

    def test_arm_seeds_course_state_cache(self, app, client: TestClient):
        """First /arm for a course populates ``app.state.recording_states``."""
        assert app.state.recording_states == {}

        client.post(
            "/arm",
            data={
                "course_slug": "python-basics",
                "section_name": "intro",
                "deck_name": "01 Hello",
                "part_number": "0",
            },
        )

        states = app.state.recording_states
        assert "python-basics" in states
        state = states["python-basics"]
        assert state.course_id == "python-basics"
        lecture = state.get_lecture("intro::01 Hello")
        assert lecture is not None
        assert lecture.display_name == "01 Hello"

    def test_arm_reuses_cached_state(self, app, client: TestClient):
        """Subsequent /arm calls for the same course reuse the cached state."""
        for _ in range(2):
            client.post(
                "/arm",
                data={
                    "course_slug": "c",
                    "section_name": "s",
                    "deck_name": "01 Deck",
                    "part_number": "0",
                },
            )
        states = app.state.recording_states
        assert list(states.keys()) == ["c"]
        # Only one lecture created despite two arm calls — ensure_lecture is idempotent.
        assert len(states["c"].lectures) == 1

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

    def test_sse_subscribers_list_is_configured(self, app):
        """App should expose the list of SSE subscriber queues."""
        assert hasattr(app.state, "sse_subscribers")
        assert app.state.sse_subscribers == []

    def test_arm_pushes_to_sse_subscribers(self, app, client: TestClient):
        """Arming a deck broadcasts a state_changed event to every subscriber."""
        import asyncio

        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=16)
        app.state.sse_subscribers.append(queue)

        client.post(
            "/arm",
            data={
                "course_slug": "c",
                "section_name": "s",
                "deck_name": "01 Deck",
                "part_number": "0",
            },
        )
        assert not queue.empty()

    def test_sse_events_fan_out_to_every_subscriber(self, app, client: TestClient):
        """Every connected tab receives every event, not round-robin.

        Regression: a single shared queue round-robined events between
        tabs, so opening Dashboard + Lectures meant each tab missed
        most updates.
        """
        import asyncio

        q_dashboard: asyncio.Queue[str] = asyncio.Queue(maxsize=16)
        q_lectures: asyncio.Queue[str] = asyncio.Queue(maxsize=16)
        app.state.sse_subscribers.extend([q_dashboard, q_lectures])

        client.post(
            "/arm",
            data={
                "course_slug": "c",
                "section_name": "s",
                "deck_name": "01 Deck",
                "part_number": "0",
            },
        )

        assert not q_dashboard.empty(), "dashboard tab missed the event"
        assert not q_lectures.empty(), "lectures tab missed the event"

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

    def test_notice_payloads_map_to_event_notice(self):
        """``notice:<level>|<msg>`` payloads route to ``event: notice``."""
        from clm.recordings.web.routes import _sse_event_name_for, _sse_payload_for

        assert _sse_event_name_for("notice:error|boom") == "notice"
        # The ``notice:`` prefix is stripped before the client receives it
        # so the browser can split on ``|`` without re-parsing.
        assert _sse_payload_for("notice:error|boom") == "error|boom"
        # Non-notice payloads pass through unchanged.
        assert _sse_payload_for("state_changed") == "state_changed"
        assert _sse_payload_for("job:abc") == "job:abc"

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


class TestReconcileRoute:
    """``POST /jobs/{id}/reconcile`` drives the backend's reconcile hook."""

    def test_reconcile_requires_known_job_id(self, client: TestClient):
        resp = client.post("/jobs/no-such-job/reconcile")
        assert resp.status_code == 404

    def test_reconcile_returns_updated_partial(self, app, client: TestClient):
        """Submitting a reconcile route re-renders the jobs panel."""
        from pathlib import Path as _Path

        from clm.recordings.workflow.jobs import JobState, ProcessingJob

        # Inject a fake job directly into the manager so we don't need
        # to run a full backend submit flow.
        manager = app.state.job_manager
        job = ProcessingJob(
            backend_name="onnx",
            raw_path=_Path("/tmp/raw--RAW.mp4"),
            final_path=_Path("/tmp/final.mp4"),
            relative_dir=_Path(),
            state=JobState.FAILED,
            error="to-be-reconciled",
        )
        manager._store_job(job)

        resp = client.post(f"/jobs/{job.id}/reconcile")
        assert resp.status_code == 200
        # The response is the jobs panel partial — contains the Jobs
        # header when any jobs are present.
        assert "Processing Jobs" in resp.text


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

    def test_status_shows_connect_button_when_disconnected(self, app, client: TestClient):
        app.state.obs.connected = False
        app.state.obs.connection_state = "disconnected"
        resp = client.get("/status-partial")
        assert resp.status_code == 200
        assert "disconnected" in resp.text
        assert "/obs/connect" in resp.text

    def test_status_shows_disconnect_button_when_connected(self, app, client: TestClient):
        app.state.obs.connected = True
        resp = client.get("/status-partial")
        assert resp.status_code == 200
        assert "/obs/disconnect" in resp.text


# ---------------------------------------------------------------------------
# Process route
# ---------------------------------------------------------------------------


class TestProcessRoute:
    """POST /process must return immediately; backend work runs off-thread."""

    def test_process_calls_submit_async_not_submit(
        self, app, client: TestClient, recording_root: Path, monkeypatch
    ):
        """Regression guard: /process must dispatch via submit_async so the
        handler returns before the backend finishes its upload."""
        tp = recording_root / "to-process" / "course" / "section"
        tp.mkdir(parents=True)
        raw = tp / "topic--RAW.mp4"
        raw.write_bytes(b"video")

        manager = app.state.job_manager
        calls: list[Path] = []
        monkeypatch.setattr(
            manager,
            "submit_async",
            lambda path, *, options=None: calls.append(path),
        )
        # If the route regresses back to blocking submit, this will be
        # called and the assertion below will fail.
        called_submit: list[Path] = []
        monkeypatch.setattr(
            manager,
            "submit",
            lambda path, *, options=None: called_submit.append(path),
        )

        resp = client.post("/process", data={"raw_path": str(raw)})

        assert resp.status_code == 200
        assert len(calls) == 1
        assert calls[0] == raw
        assert called_submit == []  # blocking submit must NOT be used

    def test_process_redirects_to_lectures(
        self, app, client: TestClient, recording_root: Path, monkeypatch
    ):
        """After submitting, /process redirects the HTMX client to /lectures."""
        tp = recording_root / "to-process" / "course" / "section"
        tp.mkdir(parents=True)
        raw = tp / "topic--RAW.mp4"
        raw.write_bytes(b"video")

        monkeypatch.setattr(
            app.state.job_manager,
            "submit_async",
            lambda path, *, options=None: None,
        )

        resp = client.post("/process", data={"raw_path": str(raw)})

        assert resp.status_code == 200
        assert resp.headers.get("HX-Redirect") == "/lectures"

    def test_process_skips_missing_files(self, app, client: TestClient, monkeypatch):
        """Non-existent raw_path is logged and skipped, not submitted."""
        calls: list[Path] = []
        monkeypatch.setattr(
            app.state.job_manager,
            "submit_async",
            lambda path, *, options=None: calls.append(path),
        )

        resp = client.post("/process", data={"raw_path": "/does/not/exist.mp4"})

        assert resp.status_code == 200
        assert calls == []


# ---------------------------------------------------------------------------
# OBS connection-aware guards (Phase 3)
# ---------------------------------------------------------------------------


class TestObsConnectionGuard:
    """Record/Arm actions are blocked when OBS is not connected."""

    def test_arm_returns_409_when_obs_disconnected(self, app, client: TestClient):
        app.state.obs.connected = False
        resp = client.post(
            "/arm",
            data={
                "course_slug": "c",
                "section_name": "s",
                "deck_name": "01 Deck",
                "part_number": "0",
            },
        )
        assert resp.status_code == 409
        assert "OBS not connected" in resp.text

    def test_record_returns_409_when_obs_disconnected(self, app, client: TestClient):
        app.state.obs.connected = False
        resp = client.post(
            "/record",
            data={
                "course_slug": "c",
                "section_name": "s",
                "deck_name": "01 Deck",
                "part_number": "0",
            },
        )
        assert resp.status_code == 409
        # OBS start_record must not be attempted when the guard fires.
        app.state.obs.start_record.assert_not_called()

    def test_arm_still_works_when_obs_connected(self, app, client: TestClient):
        app.state.obs.connected = True
        resp = client.post(
            "/arm",
            data={
                "course_slug": "c",
                "section_name": "s",
                "deck_name": "01 Deck",
                "part_number": "0",
            },
        )
        assert resp.status_code == 200
        assert app.state.session.state is SessionState.ARMED


class TestNoticeEvents:
    """Failed routes push a ``notice:`` payload so the toast region lights up.

    Errors that previously went only to the log are now surfaced to the
    user — a failed OBS connect or a missing file passed to /process
    should produce a toast, not silence.
    """

    def test_base_template_exposes_toast_region(self, client: TestClient):
        """Every page has a single ``#toast-region`` the JS bridge targets."""
        html = client.get("/").text
        assert 'id="toast-region"' in html
        # The region itself is empty on initial render — JS fills it.
        assert html.count('id="toast-region"') == 1

    def test_obs_connect_failure_pushes_notice(self, app, client: TestClient):
        """A ConnectionError during OBS connect pushes a notice:error."""
        import asyncio

        obs = app.state.obs
        obs.connect.side_effect = ConnectionError("OBS not running")
        obs.connected = False

        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=16)
        app.state.sse_subscribers.append(queue)

        client.post("/obs/connect")

        # Drain the queue and find the notice payload.
        seen: list[str] = []
        while not queue.empty():
            seen.append(queue.get_nowait())
        assert any(s.startswith("notice:error|") for s in seen), seen
        notice = next(s for s in seen if s.startswith("notice:error|"))
        assert "OBS not running" in notice

    def test_process_missing_file_pushes_warning_notice(self, app, client: TestClient):
        """``/process`` with a non-existent raw_path pushes a warning."""
        import asyncio

        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=16)
        app.state.sse_subscribers.append(queue)

        client.post("/process", data={"raw_path": "/does/not/exist.mp4"})

        seen: list[str] = []
        while not queue.empty():
            seen.append(queue.get_nowait())
        assert any(s.startswith("notice:warning|") for s in seen), seen

    def test_process_successful_submit_pushes_success_notice(
        self, app, client: TestClient, recording_root: Path, monkeypatch
    ):
        """A successful submission via /process emits a notice:success."""
        import asyncio

        tp = recording_root / "to-process" / "c" / "s"
        tp.mkdir(parents=True)
        raw = tp / "topic--RAW.mp4"
        raw.write_bytes(b"video")

        monkeypatch.setattr(
            app.state.job_manager,
            "submit_async",
            lambda path, *, options=None: None,
        )

        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=16)
        app.state.sse_subscribers.append(queue)

        client.post("/process", data={"raw_path": str(raw)})

        seen: list[str] = []
        while not queue.empty():
            seen.append(queue.get_nowait())
        assert any(s.startswith("notice:success|") for s in seen), seen

    def test_failed_job_event_pushes_error_notice(self, app, recording_root: Path):
        """Job transitions into FAILED push a ``notice:error`` toast.

        Before this, a stuck Auphonic validation error only surfaced in
        the jobs panel on next poll — the lectures page stayed quiet
        and the user had to go hunting in the dashboard.
        """
        import asyncio

        from clm.recordings.workflow.job_manager import JOB_EVENT_TOPIC
        from clm.recordings.workflow.jobs import JobState, ProcessingJob

        job = ProcessingJob(
            backend_name="auphonic",
            raw_path=recording_root / "to-process" / "c" / "s" / "deck--RAW.mp4",
            final_path=recording_root / "final" / "c" / "s" / "deck.mp4",
            relative_dir=Path("c/s"),
            state=JobState.FAILED,
            error="validation failed",
        )

        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=16)
        app.state.sse_subscribers.append(queue)

        app.state.event_bus.publish(JOB_EVENT_TOPIC, job)

        seen: list[str] = []
        while not queue.empty():
            seen.append(queue.get_nowait())
        error_notice = next((s for s in seen if s.startswith("notice:error|")), None)
        assert error_notice is not None, seen
        assert "deck--RAW" in error_notice
        assert "validation failed" in error_notice

    def test_terminal_job_event_toasts_only_once(self, app, recording_root: Path):
        """Re-publishing the same terminal job does not produce duplicate toasts.

        The Auphonic poller re-emits FAILED jobs on every tick until the
        user reconciles or cancels; without a dedup guard the toast
        region would fill with identical error cards.
        """
        import asyncio

        from clm.recordings.workflow.job_manager import JOB_EVENT_TOPIC
        from clm.recordings.workflow.jobs import JobState, ProcessingJob

        job = ProcessingJob(
            backend_name="auphonic",
            raw_path=recording_root / "to-process" / "c" / "s" / "deck--RAW.mp4",
            final_path=recording_root / "final" / "c" / "s" / "deck.mp4",
            relative_dir=Path("c/s"),
            state=JobState.FAILED,
            error="validation failed",
        )

        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=16)
        app.state.sse_subscribers.append(queue)

        app.state.event_bus.publish(JOB_EVENT_TOPIC, job)
        app.state.event_bus.publish(JOB_EVENT_TOPIC, job)

        seen: list[str] = []
        while not queue.empty():
            seen.append(queue.get_nowait())
        error_notices = [s for s in seen if s.startswith("notice:error|")]
        assert len(error_notices) == 1, seen

    def test_completed_job_event_pushes_success_notice(self, app, recording_root: Path):
        """Job transitions into COMPLETED push a ``notice:success`` toast."""
        import asyncio

        from clm.recordings.workflow.job_manager import JOB_EVENT_TOPIC
        from clm.recordings.workflow.jobs import JobState, ProcessingJob

        job = ProcessingJob(
            backend_name="auphonic",
            raw_path=recording_root / "to-process" / "c" / "s" / "deck--RAW.mp4",
            final_path=recording_root / "final" / "c" / "s" / "deck.mp4",
            relative_dir=Path("c/s"),
            state=JobState.COMPLETED,
        )

        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=16)
        app.state.sse_subscribers.append(queue)

        app.state.event_bus.publish(JOB_EVENT_TOPIC, job)

        seen: list[str] = []
        while not queue.empty():
            seen.append(queue.get_nowait())
        success_notice = next((s for s in seen if s.startswith("notice:success|")), None)
        assert success_notice is not None, seen
        assert "deck--RAW" in success_notice


class TestObsStateRendering:
    """Lectures page shows a disabled Record button and a warning banner
    when OBS is not connected; SSE event forwards connection transitions."""

    def _set_course(self, app) -> None:
        from clm.core.utils.text_utils import Text

        mock_course = MagicMock()
        mock_section = MagicMock()
        mock_section.name = Text(de="S", en="S")
        mock_nb = MagicMock()
        mock_nb.title = Text(de="T", en="T")
        mock_nb.number_in_section = 1
        mock_nb.file_name.return_value = "01 T"
        mock_section.notebooks = [mock_nb]
        mock_course.sections = [mock_section]
        mock_course.output_dir_name = Text(de="c", en="c")
        app.state.course = mock_course

    def test_lectures_record_button_disabled_when_obs_down(self, app):
        self._set_course(app)
        app.state.obs.connected = False
        app.state.obs.connection_state = "disconnected"
        with TestClient(app) as c:
            resp = c.get("/lectures")
        assert "OBS not connected" in resp.text
        assert "disabled" in resp.text

    def test_lectures_record_button_enabled_when_obs_up(self, app):
        self._set_course(app)
        app.state.obs.connected = True
        app.state.obs.connection_state = "connected"
        with TestClient(app) as c:
            resp = c.get("/lectures")
        # No OBS-not-connected banner when connected.
        assert "OBS not connected" not in resp.text

    def test_status_partial_shows_reconnecting(self, app, client: TestClient):
        app.state.obs.connected = False
        app.state.obs.connection_state = "reconnecting"
        resp = client.get("/status-partial")
        assert resp.status_code == 200
        assert "reconnecting" in resp.text

    def test_obs_state_callback_pushes_sse_event(self, app):
        """create_app wires on_state_change → broadcast to every subscriber."""
        import asyncio

        # Simulate the watchdog reporting a state transition by invoking the
        # registered callback directly.
        obs = app.state.obs
        call_args_list = obs.on_state_change.call_args_list
        assert call_args_list, "create_app must register an on_state_change cb"
        callback = call_args_list[0][0][0]

        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=16)
        app.state.sse_subscribers.append(queue)

        callback("reconnecting")
        assert queue.get_nowait() == "obs:reconnecting"

    def test_status_json_includes_obs_state(self, app, client: TestClient):
        app.state.obs.connected = False
        app.state.obs.connection_state = "reconnecting"
        resp = client.get("/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["obs_state"] == "reconnecting"
        assert data["obs_connected"] is False
