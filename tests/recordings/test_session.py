"""Tests for the recording session state machine."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clm.recordings.workflow.directories import ensure_root
from clm.recordings.workflow.obs import ObsClient, RecordingEvent
from clm.recordings.workflow.session import (
    ArmedTopic,
    RecordingSession,
    SessionSnapshot,
    SessionState,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_obs() -> MagicMock:
    """A mock ObsClient that tracks registered callbacks."""
    obs = MagicMock(spec=ObsClient)
    obs.connected = True
    obs._record_callbacks: list = []

    def register_cb(cb):
        obs._record_callbacks.append(cb)

    obs.on_record_state_changed.side_effect = register_cb
    return obs


@pytest.fixture()
def recording_root(tmp_path: Path) -> Path:
    """A tmp recordings root with the three-tier structure."""
    root = tmp_path / "recordings"
    ensure_root(root)
    return root


@pytest.fixture()
def session(mock_obs: MagicMock, recording_root: Path) -> RecordingSession:
    """A session with short stability checks for fast tests."""
    return RecordingSession(
        mock_obs,
        recording_root,
        stability_interval=0.01,
        stability_checks=1,
    )


def _fire_event(mock_obs: MagicMock, event: RecordingEvent) -> None:
    """Simulate an OBS event by calling registered callbacks."""
    for cb in mock_obs._record_callbacks:
        cb(event)


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------


class TestInitialState:
    def test_starts_idle(self, session: RecordingSession):
        assert session.state is SessionState.IDLE

    def test_no_armed_topic(self, session: RecordingSession):
        assert session.armed_topic is None

    def test_snapshot_initial(self, session: RecordingSession):
        snap = session.snapshot()
        assert snap.state is SessionState.IDLE
        assert snap.armed_topic is None
        assert snap.obs_connected is True
        assert snap.last_output is None
        assert snap.error is None

    def test_registers_obs_callback(self, mock_obs: MagicMock):
        RecordingSession(mock_obs, Path("/tmp/root"))
        mock_obs.on_record_state_changed.assert_called_once()


# ---------------------------------------------------------------------------
# Arming / disarming
# ---------------------------------------------------------------------------


class TestArmDisarm:
    def test_arm_from_idle(self, session: RecordingSession):
        session.arm("python-basics", "Section 01", "intro")
        assert session.state is SessionState.ARMED
        assert session.armed_topic == ArmedTopic("python-basics", "Section 01", "intro")

    def test_arm_from_armed_switches_topic(self, session: RecordingSession):
        session.arm("course-a", "s1", "topic1")
        session.arm("course-b", "s2", "topic2")
        assert session.armed_topic == ArmedTopic("course-b", "s2", "topic2")
        assert session.state is SessionState.ARMED

    def test_arm_clears_previous_error(self, session: RecordingSession):
        # Manually set an error
        session._error = "old error"
        session.arm("c", "s", "t")
        assert session.snapshot().error is None

    def test_arm_while_recording_raises(self, session: RecordingSession, mock_obs):
        session.arm("c", "s", "t")
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        assert session.state is SessionState.RECORDING

        with pytest.raises(RuntimeError, match="Cannot arm"):
            session.arm("c", "s", "other")

    def test_disarm_from_armed(self, session: RecordingSession):
        session.arm("c", "s", "t")
        session.disarm()
        assert session.state is SessionState.IDLE
        assert session.armed_topic is None

    def test_disarm_from_idle(self, session: RecordingSession):
        session.disarm()  # No-op, should not raise
        assert session.state is SessionState.IDLE

    def test_disarm_while_recording_raises(self, session: RecordingSession, mock_obs):
        session.arm("c", "s", "t")
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))

        with pytest.raises(RuntimeError, match="Cannot disarm"):
            session.disarm()


# ---------------------------------------------------------------------------
# Recording start event
# ---------------------------------------------------------------------------


class TestRecordingStart:
    def test_armed_transitions_to_recording(self, session: RecordingSession, mock_obs):
        session.arm("c", "s", "t")
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        assert session.state is SessionState.RECORDING

    def test_idle_stays_idle_on_start(self, session: RecordingSession, mock_obs):
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        assert session.state is SessionState.IDLE


# ---------------------------------------------------------------------------
# Recording stop event — no rename
# ---------------------------------------------------------------------------


class TestRecordingStopNoRename:
    def test_stop_without_armed_topic_goes_idle(self, session: RecordingSession, mock_obs):
        # Force into RECORDING state without armed topic
        session._state = SessionState.RECORDING
        session._armed = None
        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="stopped",
                output_path="/tmp/out.mkv",
            ),
        )
        assert session.state is SessionState.IDLE

    def test_stop_while_idle_is_ignored(self, session: RecordingSession, mock_obs):
        _fire_event(
            mock_obs,
            RecordingEvent(output_active=False, output_state="stopped"),
        )
        assert session.state is SessionState.IDLE

    def test_stop_without_output_path_sets_error(self, session: RecordingSession, mock_obs):
        session.arm("c", "s", "t")
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        _fire_event(
            mock_obs,
            RecordingEvent(output_active=False, output_state="stopped", output_path=None),
        )
        assert session.state is SessionState.IDLE
        snap = session.snapshot()
        assert snap.error is not None
        assert "output file path" in snap.error


# ---------------------------------------------------------------------------
# Recording stop event — with rename
# ---------------------------------------------------------------------------


class TestRecordingStopWithRename:
    @patch("clm.recordings.workflow.session.shutil.move")
    def test_rename_moves_file(self, mock_move, session: RecordingSession, mock_obs, tmp_path):
        # Create a fake OBS output file
        obs_output = tmp_path / "2025-04-01_12-00-00.mkv"
        obs_output.write_bytes(b"video data")

        session.arm("python-basics", "Section 01", "intro")
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))

        assert session.state is SessionState.RECORDING

        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="stopped",
                output_path=str(obs_output),
            ),
        )

        # Wait for the rename thread to complete
        _wait_for_state(session, SessionState.IDLE, timeout=5.0)

        assert session.state is SessionState.IDLE
        mock_move.assert_called_once()
        src, dst = mock_move.call_args[0]
        assert src == str(obs_output)
        assert "python-basics" in dst
        assert "Section 01" in dst
        assert "intro--RAW.mkv" in dst

    @patch("clm.recordings.workflow.session.shutil.move")
    def test_rename_sets_last_output(self, mock_move, session, mock_obs, tmp_path):
        obs_output = tmp_path / "rec.mp4"
        obs_output.write_bytes(b"data")

        session.arm("c", "s", "t")
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="stopped",
                output_path=str(obs_output),
            ),
        )

        _wait_for_state(session, SessionState.IDLE, timeout=5.0)

        snap = session.snapshot()
        assert snap.last_output is not None
        assert snap.last_output.name == "t--RAW.mp4"

    @patch("clm.recordings.workflow.session.shutil.move")
    def test_rename_clears_armed_topic(self, mock_move, session, mock_obs, tmp_path):
        obs_output = tmp_path / "rec.mp4"
        obs_output.write_bytes(b"data")

        session.arm("c", "s", "t")
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="stopped",
                output_path=str(obs_output),
            ),
        )

        _wait_for_state(session, SessionState.IDLE, timeout=5.0)
        assert session.armed_topic is None

    def test_rename_file_not_found_sets_error(self, session, mock_obs):
        session.arm("c", "s", "t")
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="stopped",
                output_path="/nonexistent/file.mkv",
            ),
        )

        _wait_for_state(session, SessionState.IDLE, timeout=5.0)

        snap = session.snapshot()
        assert snap.error is not None
        assert "not found" in snap.error.lower() or "nonexistent" in snap.error.lower()

    @patch(
        "clm.recordings.workflow.session.shutil.move",
        side_effect=PermissionError("access denied"),
    )
    def test_rename_failure_sets_error(self, mock_move, session, mock_obs, tmp_path):
        obs_output = tmp_path / "rec.mp4"
        obs_output.write_bytes(b"data")

        session.arm("c", "s", "t")
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="stopped",
                output_path=str(obs_output),
            ),
        )

        _wait_for_state(session, SessionState.IDLE, timeout=5.0)

        snap = session.snapshot()
        assert snap.error is not None
        assert "access denied" in snap.error.lower()


# ---------------------------------------------------------------------------
# State change callbacks
# ---------------------------------------------------------------------------


class TestStateChangeCallback:
    def test_arm_triggers_callback(self, mock_obs, recording_root):
        callback = MagicMock()
        session = RecordingSession(mock_obs, recording_root, on_state_change=callback)
        session.arm("c", "s", "t")

        callback.assert_called_once()
        snap = callback.call_args[0][0]
        assert isinstance(snap, SessionSnapshot)
        assert snap.state is SessionState.ARMED

    def test_disarm_triggers_callback(self, mock_obs, recording_root):
        callback = MagicMock()
        session = RecordingSession(mock_obs, recording_root, on_state_change=callback)
        session.arm("c", "s", "t")
        callback.reset_mock()
        session.disarm()

        callback.assert_called_once()
        snap = callback.call_args[0][0]
        assert snap.state is SessionState.IDLE

    def test_recording_start_triggers_callback(self, mock_obs, recording_root):
        callback = MagicMock()
        session = RecordingSession(mock_obs, recording_root, on_state_change=callback)
        session.arm("c", "s", "t")
        callback.reset_mock()

        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))

        callback.assert_called_once()
        snap = callback.call_args[0][0]
        assert snap.state is SessionState.RECORDING

    def test_callback_exception_does_not_break_session(self, mock_obs, recording_root):
        callback = MagicMock(side_effect=RuntimeError("callback error"))
        session = RecordingSession(mock_obs, recording_root, on_state_change=callback)

        # Should not raise despite callback error
        session.arm("c", "s", "t")
        assert session.state is SessionState.ARMED


# ---------------------------------------------------------------------------
# ArmedTopic
# ---------------------------------------------------------------------------


class TestArmedTopic:
    def test_frozen(self):
        topic = ArmedTopic("c", "s", "t")
        with pytest.raises(AttributeError):
            topic.course_slug = "other"  # type: ignore[misc]

    def test_equality(self):
        a = ArmedTopic("c", "s", "t")
        b = ArmedTopic("c", "s", "t")
        assert a == b

    def test_inequality(self):
        a = ArmedTopic("c", "s", "t")
        b = ArmedTopic("c", "s", "other")
        assert a != b


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wait_for_state(
    session: RecordingSession,
    expected: SessionState,
    timeout: float = 5.0,
) -> None:
    """Block until the session reaches the expected state or timeout."""
    deadline = threading.Event()
    deadline.wait(0)
    import time

    start = time.monotonic()
    while session.state != expected:
        if time.monotonic() - start > timeout:
            raise TimeoutError(
                f"Session did not reach {expected.value} within {timeout}s "
                f"(stuck at {session.state.value})"
            )
        time.sleep(0.01)
