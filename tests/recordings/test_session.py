"""Tests for the recording session state machine."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clm.recordings.workflow.directories import (
    archive_dir,
    ensure_root,
    final_dir,
    superseded_dir,
    to_process_dir,
)
from clm.recordings.workflow.obs import ObsClient, RecordingEvent
from clm.recordings.workflow.session import (
    ArmedDeck,
    RecordingSession,
    SessionSnapshot,
    SessionState,
    _prepare_target_slot,
    _supersede_file,
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
    """A session with short stability checks for fast tests.

    Short-take detection and the retake window are disabled by default
    (``short_take_seconds=0.0``, ``retake_window_seconds=0.0``) so that
    existing tests which fire STARTED/STOPPED back-to-back still exercise
    the normal rename path. Phase 2 tests that want to exercise those
    features construct their own session with explicit values.
    """
    return RecordingSession(
        mock_obs,
        recording_root,
        stability_interval=0.01,
        stability_checks=1,
        short_take_seconds=0.0,
        retake_window_seconds=0.0,
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

    def test_no_armed_deck(self, session: RecordingSession):
        assert session.armed_deck is None

    def test_snapshot_initial(self, session: RecordingSession):
        snap = session.snapshot()
        assert snap.state is SessionState.IDLE
        assert snap.armed_deck is None
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
        session.arm("python-basics", "Section 01", "01 Intro")
        assert session.state is SessionState.ARMED
        assert session.armed_deck == ArmedDeck("python-basics", "Section 01", "01 Intro")

    def test_arm_from_armed_switches_deck(self, session: RecordingSession):
        session.arm("course-a", "s1", "01 Deck A")
        session.arm("course-b", "s2", "02 Deck B")
        assert session.armed_deck == ArmedDeck("course-b", "s2", "02 Deck B")
        assert session.state is SessionState.ARMED

    def test_arm_with_part_number(self, session: RecordingSession):
        session.arm("c", "s", "03 Intro", part_number=2)
        assert session.armed_deck == ArmedDeck("c", "s", "03 Intro", 2)
        assert session.armed_deck.part_number == 2

    def test_arm_part_number_defaults_to_zero(self, session: RecordingSession):
        session.arm("c", "s", "03 Intro")
        assert session.armed_deck.part_number == 0

    def test_arm_clears_previous_error(self, session: RecordingSession):
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
        assert session.armed_deck is None

    def test_disarm_from_idle(self, session: RecordingSession):
        session.disarm()  # No-op, should not raise
        assert session.state is SessionState.IDLE

    def test_disarm_while_recording_raises(self, session: RecordingSession, mock_obs):
        session.arm("c", "s", "t")
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))

        with pytest.raises(RuntimeError, match="Cannot disarm"):
            session.disarm()

    def test_armed_topic_alias(self, session: RecordingSession):
        """The deprecated armed_topic property returns the same as armed_deck."""
        session.arm("c", "s", "t")
        assert session.armed_topic is session.armed_deck

    def test_snapshot_armed_topic_alias(self, session: RecordingSession):
        """SessionSnapshot.armed_topic returns armed_deck for backward compat."""
        session.arm("c", "s", "t")
        snap = session.snapshot()
        assert snap.armed_topic is snap.armed_deck


# ---------------------------------------------------------------------------
# One-click record / stop (Phase 1)
# ---------------------------------------------------------------------------


class TestRecordAndStop:
    def test_record_arms_and_starts_obs(self, session: RecordingSession, mock_obs):
        session.record("c", "s", "01 Deck")
        assert session.state is SessionState.ARMED
        assert session.armed_deck == ArmedDeck("c", "s", "01 Deck")
        mock_obs.start_record.assert_called_once_with()

    def test_record_passes_part_number_and_lang(self, session: RecordingSession, mock_obs):
        session.record("c", "s", "01 Deck", part_number=2, lang="de")
        assert session.armed_deck.part_number == 2
        assert session.armed_deck.lang == "de"
        mock_obs.start_record.assert_called_once_with()

    def test_record_obs_failure_leaves_deck_armed(self, session: RecordingSession, mock_obs):
        """If OBS rejects the start, the deck stays armed so the user can
        start recording manually or retry once OBS is reachable."""
        mock_obs.start_record.side_effect = ConnectionError("OBS not running")

        with pytest.raises(ConnectionError):
            session.record("c", "s", "01 Deck")

        assert session.state is SessionState.ARMED
        assert session.armed_deck == ArmedDeck("c", "s", "01 Deck")

    def test_record_while_recording_raises(self, session: RecordingSession, mock_obs):
        """Trying to start a new recording while one is in flight is a
        RuntimeError from arm(); OBS is never contacted."""
        session.arm("c", "s", "t")
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        mock_obs.start_record.reset_mock()

        with pytest.raises(RuntimeError, match="Cannot arm"):
            session.record("c", "s", "other")

        mock_obs.start_record.assert_not_called()

    def test_stop_calls_obs_stop_record(self, session: RecordingSession, mock_obs):
        session.stop()
        mock_obs.stop_record.assert_called_once_with()

    def test_stop_propagates_obs_error(self, session: RecordingSession, mock_obs):
        mock_obs.stop_record.side_effect = ConnectionError("OBS not connected")
        with pytest.raises(ConnectionError):
            session.stop()


# ---------------------------------------------------------------------------
# Pause / resume
# ---------------------------------------------------------------------------


def _start_recording(session: RecordingSession, mock_obs: MagicMock) -> None:
    """Helper that drives the session from IDLE to RECORDING."""
    session.arm("c", "s", "t")
    _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
    assert session.state is SessionState.RECORDING


class TestPauseResume:
    """OBS pause/resume events must be surfaced, not mistaken for a stop.

    Regression for the bug where a pause during an active recording
    produced a ``RecordStateChanged`` event with ``output_active=False``
    and no ``output_path``; the old handler fell into the stopped
    branch, set an error, and disarmed the deck — leaving the user to
    manually move the recording file into ``to-process/`` once OBS
    finally stopped.
    """

    def test_paused_event_transitions_to_paused_state(
        self, session: RecordingSession, mock_obs: MagicMock
    ):
        _start_recording(session, mock_obs)
        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="OBS_WEBSOCKET_OUTPUT_PAUSED",
                output_path=None,
            ),
        )
        assert session.state is SessionState.PAUSED
        assert session.armed_deck is not None  # deck must be preserved

    def test_paused_event_does_not_set_error(self, session: RecordingSession, mock_obs: MagicMock):
        """The old handler misread pause as a missing-path stop."""
        _start_recording(session, mock_obs)
        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="OBS_WEBSOCKET_OUTPUT_PAUSED",
                output_path=None,
            ),
        )
        assert session.snapshot().error is None

    def test_resumed_event_returns_to_recording(
        self, session: RecordingSession, mock_obs: MagicMock
    ):
        _start_recording(session, mock_obs)
        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="OBS_WEBSOCKET_OUTPUT_PAUSED",
            ),
        )
        assert session.state is SessionState.PAUSED

        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=True,
                output_state="OBS_WEBSOCKET_OUTPUT_RESUMED",
            ),
        )
        assert session.state is SessionState.RECORDING
        assert session.armed_deck is not None

    def test_stop_after_pause_renames_file(
        self,
        mock_obs: MagicMock,
        recording_root: Path,
        tmp_path: Path,
    ):
        """Pause → Stop from OBS must still trigger the normal rename."""
        session = RecordingSession(
            mock_obs,
            recording_root,
            stability_interval=0.01,
            stability_checks=1,
            short_take_seconds=0.0,
            retake_window_seconds=0.0,
        )
        obs_output = tmp_path / "rec.mkv"
        obs_output.write_bytes(b"video data")

        session.arm("c", "s", "t")
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))

        _fire_event(
            mock_obs,
            RecordingEvent(output_active=False, output_state="OBS_WEBSOCKET_OUTPUT_PAUSED"),
        )
        assert session.state is SessionState.PAUSED

        with patch("clm.recordings.workflow.session.safe_move"):
            _fire_event(
                mock_obs,
                RecordingEvent(
                    output_active=False,
                    output_state="OBS_WEBSOCKET_OUTPUT_STOPPED",
                    output_path=str(obs_output),
                ),
            )
            _wait_for_state(session, SessionState.IDLE, timeout=15.0)

        assert session.state is SessionState.IDLE
        assert session.armed_deck is None
        assert session.snapshot().error is None

    def test_paused_event_outside_recording_is_ignored(
        self, session: RecordingSession, mock_obs: MagicMock
    ):
        """A PAUSED event arriving while IDLE must not disturb the session."""
        _fire_event(
            mock_obs,
            RecordingEvent(output_active=False, output_state="OBS_WEBSOCKET_OUTPUT_PAUSED"),
        )
        assert session.state is SessionState.IDLE
        assert session.armed_deck is None

    def test_resumed_event_outside_pause_is_ignored(
        self, session: RecordingSession, mock_obs: MagicMock
    ):
        """A stray RESUMED while IDLE must not start fabricated recording."""
        _fire_event(
            mock_obs,
            RecordingEvent(output_active=True, output_state="OBS_WEBSOCKET_OUTPUT_RESUMED"),
        )
        assert session.state is SessionState.IDLE

    def test_pause_calls_obs_pause_record(self, session: RecordingSession, mock_obs: MagicMock):
        _start_recording(session, mock_obs)
        session.pause()
        mock_obs.pause_record.assert_called_once_with()

    def test_pause_raises_when_not_recording(self, session: RecordingSession, mock_obs: MagicMock):
        with pytest.raises(RuntimeError, match="Cannot pause"):
            session.pause()
        mock_obs.pause_record.assert_not_called()

    def test_resume_calls_obs_resume_record(self, session: RecordingSession, mock_obs: MagicMock):
        _start_recording(session, mock_obs)
        _fire_event(
            mock_obs,
            RecordingEvent(output_active=False, output_state="OBS_WEBSOCKET_OUTPUT_PAUSED"),
        )
        session.resume()
        mock_obs.resume_record.assert_called_once_with()

    def test_resume_raises_when_not_paused(self, session: RecordingSession, mock_obs: MagicMock):
        _start_recording(session, mock_obs)
        with pytest.raises(RuntimeError, match="Cannot resume"):
            session.resume()
        mock_obs.resume_record.assert_not_called()

    def test_disarm_while_paused_raises(self, session: RecordingSession, mock_obs: MagicMock):
        _start_recording(session, mock_obs)
        _fire_event(
            mock_obs,
            RecordingEvent(output_active=False, output_state="OBS_WEBSOCKET_OUTPUT_PAUSED"),
        )
        with pytest.raises(RuntimeError, match="Cannot disarm"):
            session.disarm()

    def test_snapshot_paused_flag(self, session: RecordingSession, mock_obs: MagicMock):
        _start_recording(session, mock_obs)
        assert session.snapshot().paused is False
        _fire_event(
            mock_obs,
            RecordingEvent(output_active=False, output_state="OBS_WEBSOCKET_OUTPUT_PAUSED"),
        )
        snap = session.snapshot()
        assert snap.paused is True
        assert snap.state is SessionState.PAUSED
        # Paused elapsed is frozen — present as a non-None float.
        assert snap.recording_elapsed_seconds is not None

    def test_pause_freezes_elapsed_timer(self, session: RecordingSession, mock_obs: MagicMock):
        """Elapsed reads must not advance while paused."""
        _start_recording(session, mock_obs)
        _fire_event(
            mock_obs,
            RecordingEvent(output_active=False, output_state="OBS_WEBSOCKET_OUTPUT_PAUSED"),
        )
        first = session.snapshot().recording_elapsed_seconds
        # Sleep briefly — in paused state the elapsed must not tick.
        import time as _time

        _time.sleep(0.05)
        second = session.snapshot().recording_elapsed_seconds
        assert first == second


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
    def test_stop_without_armed_deck_goes_idle(self, session: RecordingSession, mock_obs):
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

    def test_stopping_event_ignored_while_recording(self, session: RecordingSession, mock_obs):
        """OBS fires an intermediate STOPPING event (no output_path) before
        the definitive STOPPED event.  The session must stay in RECORDING
        and wait for STOPPED."""
        session.arm("c", "s", "t")
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        assert session.state is SessionState.RECORDING

        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="OBS_WEBSOCKET_OUTPUT_STOPPING",
            ),
        )
        assert session.state is SessionState.RECORDING
        assert session.armed_deck is not None

    def test_stopping_then_stopped_completes_rename(
        self, session: RecordingSession, mock_obs, tmp_path
    ):
        """Full STOPPING -> STOPPED sequence: the rename only happens on
        the STOPPED event that carries the output_path."""
        obs_output = tmp_path / "rec.mkv"
        obs_output.write_bytes(b"video data")

        session.arm("c", "s", "t")
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))

        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="OBS_WEBSOCKET_OUTPUT_STOPPING",
            ),
        )
        assert session.state is SessionState.RECORDING

        with patch("clm.recordings.workflow.session.safe_move"):
            _fire_event(
                mock_obs,
                RecordingEvent(
                    output_active=False,
                    output_state="OBS_WEBSOCKET_OUTPUT_STOPPED",
                    output_path=str(obs_output),
                ),
            )
            _wait_for_state(session, SessionState.IDLE, timeout=15.0)

        assert session.state is SessionState.IDLE
        assert session.armed_deck is None

    def test_stopping_event_does_not_trigger_callback(
        self, mock_obs: MagicMock, recording_root: Path
    ):
        """Intermediate STOPPING should not fire the on_state_change callback."""
        callback = MagicMock()
        session = RecordingSession(
            mock_obs,
            recording_root,
            stability_interval=0.01,
            stability_checks=1,
            on_state_change=callback,
        )
        session.arm("c", "s", "t")
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        callback.reset_mock()

        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="OBS_WEBSOCKET_OUTPUT_STOPPING",
            ),
        )
        callback.assert_not_called()


# ---------------------------------------------------------------------------
# Recording stop event — with rename
# ---------------------------------------------------------------------------


class TestRecordingStopWithRename:
    @patch("clm.recordings.workflow.session.safe_move")
    def test_rename_moves_file(self, mock_move, session: RecordingSession, mock_obs, tmp_path):
        obs_output = tmp_path / "2025-04-01_12-00-00.mkv"
        obs_output.write_bytes(b"video data")

        session.arm("python-basics", "Section 01", "01 Intro")
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

        _wait_for_state(session, SessionState.IDLE, timeout=15.0)

        assert session.state is SessionState.IDLE
        mock_move.assert_called_once()
        src, dst = mock_move.call_args[0]
        assert Path(src) == obs_output
        assert "python-basics" in str(dst)
        assert "Section 01" in str(dst)
        assert "01 Intro--RAW.mkv" in str(dst)

    @patch("clm.recordings.workflow.session.safe_move")
    def test_rename_with_part_number(self, mock_move, session, mock_obs, tmp_path):
        obs_output = tmp_path / "rec.mkv"
        obs_output.write_bytes(b"video data")

        session.arm("c", "s", "03 Intro", part_number=2)
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="stopped",
                output_path=str(obs_output),
            ),
        )

        _wait_for_state(session, SessionState.IDLE, timeout=15.0)

        mock_move.assert_called_once()
        _, dst = mock_move.call_args[0]
        assert "03 Intro (part 2)--RAW.mkv" in str(dst)

    @patch("clm.recordings.workflow.session.safe_move")
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

        _wait_for_state(session, SessionState.IDLE, timeout=15.0)

        snap = session.snapshot()
        assert snap.last_output is not None
        assert snap.last_output.name == "t--RAW.mp4"

    @patch("clm.recordings.workflow.session.safe_move")
    def test_rename_clears_armed_deck(self, mock_move, session, mock_obs, tmp_path):
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

        _wait_for_state(session, SessionState.IDLE, timeout=15.0)
        assert session.armed_deck is None

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

        _wait_for_state(session, SessionState.IDLE, timeout=15.0)

        snap = session.snapshot()
        assert snap.error is not None
        assert "not found" in snap.error.lower() or "nonexistent" in snap.error.lower()

    @patch(
        "clm.recordings.workflow.session.safe_move",
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

        _wait_for_state(session, SessionState.IDLE, timeout=15.0)

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

        session.arm("c", "s", "t")
        assert session.state is SessionState.ARMED


# ---------------------------------------------------------------------------
# ArmedDeck
# ---------------------------------------------------------------------------


class TestArmedDeck:
    def test_frozen(self):
        deck = ArmedDeck("c", "s", "d")
        with pytest.raises(AttributeError):
            deck.course_slug = "other"  # type: ignore[misc]

    def test_equality(self):
        a = ArmedDeck("c", "s", "d")
        b = ArmedDeck("c", "s", "d")
        assert a == b

    def test_inequality(self):
        a = ArmedDeck("c", "s", "d")
        b = ArmedDeck("c", "s", "other")
        assert a != b

    def test_default_part_number(self):
        deck = ArmedDeck("c", "s", "d")
        assert deck.part_number == 0

    def test_custom_part_number(self):
        deck = ArmedDeck("c", "s", "d", part_number=3)
        assert deck.part_number == 3

    def test_equality_includes_part(self):
        a = ArmedDeck("c", "s", "d", part_number=1)
        b = ArmedDeck("c", "s", "d", part_number=2)
        assert a != b


# ---------------------------------------------------------------------------
# Supersede
# ---------------------------------------------------------------------------


class TestSupersede:
    def test_supersede_moves_file(self, recording_root: Path):
        tp = to_process_dir(recording_root) / "course" / "section"
        tp.mkdir(parents=True)
        f = tp / "deck--RAW.mkv"
        f.write_bytes(b"video data")

        _supersede_file(f, recording_root)

        assert not f.exists()
        dest = superseded_dir(recording_root) / "course" / "section" / "deck--RAW.mkv"
        assert dest.exists()
        assert dest.read_bytes() == b"video data"

    def test_supersede_moves_companion_wav(self, recording_root: Path):
        tp = to_process_dir(recording_root) / "course" / "section"
        tp.mkdir(parents=True)
        video = tp / "deck--RAW.mkv"
        video.write_bytes(b"video")
        audio = tp / "deck--RAW.wav"
        audio.write_bytes(b"audio")

        _supersede_file(video, recording_root)

        assert not video.exists()
        assert not audio.exists()
        sup = superseded_dir(recording_root) / "course" / "section"
        assert (sup / "deck--RAW.mkv").exists()
        assert (sup / "deck--RAW.wav").exists()

    def test_supersede_incrementing_suffix(self, recording_root: Path):
        tp = to_process_dir(recording_root) / "course" / "section"
        tp.mkdir(parents=True)
        sup = superseded_dir(recording_root) / "course" / "section"
        sup.mkdir(parents=True)

        # Pre-populate superseded with two prior versions
        (sup / "deck--RAW.mkv").write_bytes(b"v1")
        (sup / "deck--RAW (2).mkv").write_bytes(b"v2")

        f = tp / "deck--RAW.mkv"
        f.write_bytes(b"v3")

        _supersede_file(f, recording_root)

        assert not f.exists()
        assert (sup / "deck--RAW (3).mkv").exists()
        assert (sup / "deck--RAW (3).mkv").read_bytes() == b"v3"

    def test_supersede_no_companion(self, recording_root: Path):
        """When no .wav companion exists, only the main file is moved."""
        tp = to_process_dir(recording_root) / "course" / "section"
        tp.mkdir(parents=True)
        f = tp / "deck--RAW.mkv"
        f.write_bytes(b"video")

        _supersede_file(f, recording_root)

        sup = superseded_dir(recording_root) / "course" / "section"
        assert (sup / "deck--RAW.mkv").exists()
        assert not (sup / "deck--RAW.wav").exists()

    def test_rename_preserves_existing_target_as_take(
        self, session: RecordingSession, mock_obs, recording_root: Path, tmp_path
    ):
        """When the target file already exists, it is preserved under takes/.

        Phase 3 change: a retake now demotes the existing raw into
        ``takes/`` with a ``(take K)`` suffix rather than discarding it
        to ``superseded/``. Previously-processed takes (and their raws)
        are too expensive to throw away.
        """
        tp = to_process_dir(recording_root) / "c" / "s"
        tp.mkdir(parents=True)
        existing = tp / "t--RAW.mkv"
        existing.write_bytes(b"old recording")

        obs_output = tmp_path / "rec.mkv"
        obs_output.write_bytes(b"new recording")

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

        _wait_for_state(session, SessionState.IDLE, timeout=15.0)

        # New recording is at target
        target = tp / "t--RAW.mkv"
        assert target.exists()
        assert target.read_bytes() == b"new recording"

        # Old recording preserved under takes/ with take-1 suffix
        from clm.recordings.workflow.directories import takes_dir

        preserved = takes_dir(recording_root) / "c" / "s" / "t (take 1)--RAW.mkv"
        assert preserved.exists()
        assert preserved.read_bytes() == b"old recording"


# ---------------------------------------------------------------------------
# Dynamic part naming
# ---------------------------------------------------------------------------


class TestDynamicPartNaming:
    def test_part_0_no_existing(self, recording_root: Path):
        """No files exist, part 0 → unsuffixed target."""
        td = to_process_dir(recording_root) / "c" / "s"
        td.mkdir(parents=True)
        target, renames = _prepare_target_slot(td, "deck", ".mkv", 0, "--RAW", recording_root)
        assert target.name == "deck--RAW.mkv"
        assert renames == []

    def test_part_2_renames_unsuffixed_to_part_1(self, recording_root: Path):
        """Existing unsuffixed file renamed to (part 1) when part 2 is recorded."""
        td = to_process_dir(recording_root) / "c" / "s"
        td.mkdir(parents=True)
        (td / "deck--RAW.mkv").write_bytes(b"old")

        target, renames = _prepare_target_slot(td, "deck", ".mkv", 2, "--RAW", recording_root)

        assert target.name == "deck (part 2)--RAW.mkv"
        assert not (td / "deck--RAW.mkv").exists()
        assert (td / "deck (part 1)--RAW.mkv").exists()
        assert (td / "deck (part 1)--RAW.mkv").read_bytes() == b"old"
        assert (td / "deck--RAW.mkv", td / "deck (part 1)--RAW.mkv") in renames

    def test_part_2_renames_companion_audio(self, recording_root: Path):
        """Companion .wav is also renamed to (part 1)."""
        td = to_process_dir(recording_root) / "c" / "s"
        td.mkdir(parents=True)
        (td / "deck--RAW.mkv").write_bytes(b"video")
        (td / "deck--RAW.wav").write_bytes(b"audio")

        _prepare_target_slot(td, "deck", ".mkv", 2, "--RAW", recording_root)

        assert not (td / "deck--RAW.wav").exists()
        assert (td / "deck (part 1)--RAW.wav").exists()

    def test_part_2_renames_final_file(self, recording_root: Path):
        """Unsuffixed file in final/ is also renamed to (part 1)."""
        td = to_process_dir(recording_root) / "c" / "s"
        td.mkdir(parents=True)
        (td / "deck--RAW.mkv").write_bytes(b"raw")

        fd = final_dir(recording_root) / "c" / "s"
        fd.mkdir(parents=True)
        (fd / "deck.mkv").write_bytes(b"final")

        _prepare_target_slot(td, "deck", ".mkv", 2, "--RAW", recording_root)

        assert not (fd / "deck.mkv").exists()
        assert (fd / "deck (part 1).mkv").exists()

    def test_supersede_only_recording(self, recording_root: Path):
        """Re-recording the only file: old goes to superseded, target is unsuffixed."""
        td = to_process_dir(recording_root) / "c" / "s"
        td.mkdir(parents=True)
        (td / "deck--RAW.mkv").write_bytes(b"old")

        target, _renames = _prepare_target_slot(td, "deck", ".mkv", 0, "--RAW", recording_root)

        assert target.name == "deck--RAW.mkv"
        assert not (td / "deck--RAW.mkv").exists()  # superseded
        sup = superseded_dir(recording_root) / "c" / "s" / "deck--RAW.mkv"
        assert sup.exists()

    def test_supersede_one_of_multiple_parts(self, recording_root: Path):
        """Re-recording part 2 of a multi-part: old part 2 superseded."""
        td = to_process_dir(recording_root) / "c" / "s"
        td.mkdir(parents=True)
        (td / "deck (part 1)--RAW.mkv").write_bytes(b"p1")
        (td / "deck (part 2)--RAW.mkv").write_bytes(b"old p2")

        target, _renames = _prepare_target_slot(td, "deck", ".mkv", 2, "--RAW", recording_root)

        assert target.name == "deck (part 2)--RAW.mkv"
        # Part 1 untouched
        assert (td / "deck (part 1)--RAW.mkv").read_bytes() == b"p1"
        # Old part 2 superseded
        sup = superseded_dir(recording_root) / "c" / "s" / "deck (part 2)--RAW.mkv"
        assert sup.exists()
        assert sup.read_bytes() == b"old p2"

    def test_part_2_renames_final_when_raw_already_archived(self, recording_root: Path) -> None:
        """Processed raw lives in archive/; final stays unsuffixed.

        Regression from the Phase-4 smoke test: user records part 0,
        processes it (raw moves to archive/, final lands in final/),
        then records part 2. The cascade scanned only to-process/ and
        found nothing, so both the archived raw and the unsuffixed
        final stayed put — leaving the numbering ``[0, 2]`` on disk.
        The cascade must now scan archive/ and final/ too.
        """
        td = to_process_dir(recording_root) / "c" / "s"
        td.mkdir(parents=True)
        ad = archive_dir(recording_root) / "c" / "s"
        ad.mkdir(parents=True)
        (ad / "deck--RAW.mkv").write_bytes(b"archived raw")
        fd = final_dir(recording_root) / "c" / "s"
        fd.mkdir(parents=True)
        (fd / "deck.mp4").write_bytes(b"final")

        target, renames = _prepare_target_slot(td, "deck", ".mkv", 2, "--RAW", recording_root)

        assert target.name == "deck (part 2)--RAW.mkv"
        assert not (ad / "deck--RAW.mkv").exists()
        assert (ad / "deck (part 1)--RAW.mkv").exists()
        assert not (fd / "deck.mp4").exists()
        assert (fd / "deck (part 1).mp4").exists()

        renamed_srcs = {old.name for old, _new in renames}
        assert "deck--RAW.mkv" in renamed_srcs
        assert "deck.mp4" in renamed_srcs

    def test_part_2_renames_archive_wav_companion(self, recording_root: Path) -> None:
        """``.wav`` companion in archive/ renames alongside the video."""
        td = to_process_dir(recording_root) / "c" / "s"
        td.mkdir(parents=True)
        ad = archive_dir(recording_root) / "c" / "s"
        ad.mkdir(parents=True)
        (ad / "deck--RAW.mkv").write_bytes(b"video")
        (ad / "deck--RAW.wav").write_bytes(b"audio")

        _prepare_target_slot(td, "deck", ".mkv", 2, "--RAW", recording_root)

        assert not (ad / "deck--RAW.wav").exists()
        assert (ad / "deck (part 1)--RAW.wav").exists()

    def test_part_2_final_only_cascade(self, recording_root: Path) -> None:
        """Final exists unsuffixed and no raw anywhere: still rename final."""
        td = to_process_dir(recording_root) / "c" / "s"
        td.mkdir(parents=True)
        fd = final_dir(recording_root) / "c" / "s"
        fd.mkdir(parents=True)
        (fd / "deck.mp4").write_bytes(b"final")

        target, renames = _prepare_target_slot(td, "deck", ".mkv", 2, "--RAW", recording_root)

        assert target.name == "deck (part 2)--RAW.mkv"
        assert not (fd / "deck.mp4").exists()
        assert (fd / "deck (part 1).mp4").exists()
        assert len(renames) == 1

    def test_part_2_renames_final_cut_list_and_transcript_companions(
        self, recording_root: Path
    ) -> None:
        """All files sharing the unsuffixed stem in final/ are renamed.

        Auphonic writes an ``.edl`` cut list alongside the ``.mp4``;
        future backends will add subtitles (``.vtt``/``.srt``) and
        transcripts (``.json``/``.html``). The cascade must rename every
        sibling by stem so the companion files stay paired with their
        video — earlier versions broke after the first video match and
        left the ``.edl`` as ``deck.edl`` next to ``deck (part 1).mp4``.
        """
        td = to_process_dir(recording_root) / "c" / "s"
        td.mkdir(parents=True)
        fd = final_dir(recording_root) / "c" / "s"
        fd.mkdir(parents=True)
        (fd / "deck.mp4").write_bytes(b"final video")
        (fd / "deck.edl").write_text("# cut list")
        (fd / "deck.vtt").write_text("WEBVTT\n")
        (fd / "deck.json").write_text("{}")

        _prepare_target_slot(td, "deck", ".mkv", 2, "--RAW", recording_root)

        for ext in (".mp4", ".edl", ".vtt", ".json"):
            assert not (fd / f"deck{ext}").exists(), ext
            assert (fd / f"deck (part 1){ext}").exists(), ext

    def test_part_2_renames_raw_sidecars_by_stem(self, recording_root: Path) -> None:
        """Raw companions beyond ``.wav`` also cascade.

        Today only the ``.wav`` audio rides alongside the raw video, but
        the stem-based matching is extension-agnostic so the same rule
        covers any future sidecar (e.g. OBS chapter marker files). This
        test locks in the contract with a synthetic ``.cues`` sibling.
        """
        td = to_process_dir(recording_root) / "c" / "s"
        td.mkdir(parents=True)
        (td / "deck--RAW.mkv").write_bytes(b"video")
        (td / "deck--RAW.wav").write_bytes(b"audio")
        (td / "deck--RAW.cues").write_text("01:00:00 Intro\n")

        _prepare_target_slot(td, "deck", ".mkv", 2, "--RAW", recording_root)

        for ext in (".mkv", ".wav", ".cues"):
            assert not (td / f"deck--RAW{ext}").exists(), ext
            assert (td / f"deck (part 1)--RAW{ext}").exists(), ext

    def test_part_3_with_existing_parts(self, recording_root: Path):
        """Adding part 3 when parts 1 and 2 exist: no cascade needed."""
        td = to_process_dir(recording_root) / "c" / "s"
        td.mkdir(parents=True)
        (td / "deck (part 1)--RAW.mkv").write_bytes(b"p1")
        (td / "deck (part 2)--RAW.mkv").write_bytes(b"p2")

        target, renames = _prepare_target_slot(td, "deck", ".mkv", 3, "--RAW", recording_root)

        assert target.name == "deck (part 3)--RAW.mkv"
        # Existing parts untouched
        assert (td / "deck (part 1)--RAW.mkv").read_bytes() == b"p1"
        assert (td / "deck (part 2)--RAW.mkv").read_bytes() == b"p2"
        assert renames == []


# ---------------------------------------------------------------------------
# Short-take detection and retake window (Phase 2)
# ---------------------------------------------------------------------------


def _phase2_session(
    mock_obs: MagicMock,
    root: Path,
    *,
    short_take_seconds: float = 5.0,
    retake_window_seconds: float = 60.0,
) -> RecordingSession:
    """Build a session with Phase 2 features enabled for explicit testing."""
    return RecordingSession(
        mock_obs,
        root,
        stability_interval=0.01,
        stability_checks=1,
        short_take_seconds=short_take_seconds,
        retake_window_seconds=retake_window_seconds,
    )


class TestShortTake:
    def test_short_take_goes_to_superseded(
        self, mock_obs: MagicMock, recording_root: Path, tmp_path: Path
    ):
        """A stop within short_take_seconds moves the file to superseded/
        and leaves the session in ARMED with the same deck intact."""
        sess = _phase2_session(mock_obs, recording_root, short_take_seconds=5.0)
        sess.arm("c", "s", "01 Deck")

        obs_out = tmp_path / "rec.mkv"
        obs_out.write_bytes(b"tiny take")

        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        assert sess.state is SessionState.RECORDING
        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="OBS_WEBSOCKET_OUTPUT_STOPPED",
                output_path=str(obs_out),
            ),
        )

        # Wait for the background short-take thread to finish.
        import time

        deadline = time.monotonic() + 5.0
        while obs_out.exists() and time.monotonic() < deadline:
            time.sleep(0.01)

        # File moved to superseded/<course>/<section>/
        sup = superseded_dir(recording_root) / "c" / "s" / "rec.mkv"
        assert sup.exists(), f"short take should be at {sup}"
        # Deck stays armed on the same ArmedDeck
        assert sess.state is SessionState.ARMED
        assert sess.armed_deck == ArmedDeck("c", "s", "01 Deck")

    def test_short_take_can_be_followed_by_real_take(
        self, mock_obs: MagicMock, recording_root: Path, tmp_path: Path
    ):
        """After a short take, a subsequent normal recording should
        complete the usual rename flow (the deck was never disarmed)."""
        sess = _phase2_session(
            mock_obs,
            recording_root,
            short_take_seconds=0.05,
            retake_window_seconds=0.01,
        )
        sess.arm("c", "s", "01 Deck")

        # First take: short.
        short_out = tmp_path / "short.mkv"
        short_out.write_bytes(b"x")
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="OBS_WEBSOCKET_OUTPUT_STOPPED",
                output_path=str(short_out),
            ),
        )
        _wait_for_state(sess, SessionState.ARMED)

        # Second take: wait past the short threshold before stopping.
        import time

        real_out = tmp_path / "real.mkv"
        real_out.write_bytes(b"real take data")
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        time.sleep(0.1)  # exceed short_take_seconds=0.05
        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="OBS_WEBSOCKET_OUTPUT_STOPPED",
                output_path=str(real_out),
            ),
        )

        # Rename should produce a file in to-process/<course>/<section>/
        _wait_for_state(sess, SessionState.IDLE)
        renamed = to_process_dir(recording_root) / "c" / "s" / "01 Deck--RAW.mkv"
        assert renamed.exists()

    def test_short_take_threshold_zero_never_fires(
        self, mock_obs: MagicMock, recording_root: Path, tmp_path: Path
    ):
        """``short_take_seconds=0.0`` means no elapsed duration can be
        'short', so the rename path is always taken."""
        sess = _phase2_session(
            mock_obs,
            recording_root,
            short_take_seconds=0.0,
            retake_window_seconds=0.0,
        )
        sess.arm("c", "s", "01 Deck")

        obs_out = tmp_path / "rec.mkv"
        obs_out.write_bytes(b"content")
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="OBS_WEBSOCKET_OUTPUT_STOPPED",
                output_path=str(obs_out),
            ),
        )
        _wait_for_state(sess, SessionState.IDLE)
        renamed = to_process_dir(recording_root) / "c" / "s" / "01 Deck--RAW.mkv"
        assert renamed.exists()


class TestRetakeWindow:
    def test_rename_transitions_to_armed_after_take(
        self, mock_obs: MagicMock, recording_root: Path, tmp_path: Path
    ):
        """After a normal take, the session lands in ARMED_AFTER_TAKE
        with the same deck preserved for a potential retake."""
        # ARMED_AFTER_TAKE is a *time-limited* state: the retake timer
        # auto-expires it to IDLE after retake_window_seconds. The window
        # MUST exceed _wait_for_state's poll ceiling (15s), otherwise under
        # xdist CPU starvation the poll thread can be descheduled past the
        # window, the timer fires ARMED_AFTER_TAKE -> IDLE first, and the
        # poll never observes the transient state ("stuck at idle within
        # 15.0s"). Raising the timeout can't fix that — the target state is
        # already gone. Window expiry itself is covered by
        # test_retake_window_expires_to_idle.
        sess = _phase2_session(
            mock_obs, recording_root, short_take_seconds=0.0, retake_window_seconds=60.0
        )
        sess.arm("c", "s", "01 Deck")

        obs_out = tmp_path / "rec.mkv"
        obs_out.write_bytes(b"real")
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="OBS_WEBSOCKET_OUTPUT_STOPPED",
                output_path=str(obs_out),
            ),
        )

        _wait_for_state(sess, SessionState.ARMED_AFTER_TAKE)
        assert sess.armed_deck == ArmedDeck("c", "s", "01 Deck")

    def test_retake_window_expires_to_idle(
        self, mock_obs: MagicMock, recording_root: Path, tmp_path: Path
    ):
        """When no retake arrives before the window elapses, the session
        returns to IDLE and the deck is cleared."""
        sess = _phase2_session(
            mock_obs,
            recording_root,
            short_take_seconds=0.0,
            retake_window_seconds=0.1,
        )
        sess.arm("c", "s", "01 Deck")

        obs_out = tmp_path / "rec.mkv"
        obs_out.write_bytes(b"real")
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="OBS_WEBSOCKET_OUTPUT_STOPPED",
                output_path=str(obs_out),
            ),
        )

        _wait_for_state(sess, SessionState.IDLE)
        assert sess.armed_deck is None

    def test_retake_within_window_rearms_same_deck(
        self, mock_obs: MagicMock, recording_root: Path, tmp_path: Path
    ):
        """A new OBS STARTED during the retake window is treated as a
        retake of the same armed deck (back to RECORDING)."""
        sess = _phase2_session(
            mock_obs,
            recording_root,
            short_take_seconds=0.0,
            retake_window_seconds=60.0,
        )
        sess.arm("c", "s", "01 Deck")

        obs_out = tmp_path / "rec.mkv"
        obs_out.write_bytes(b"real")
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="OBS_WEBSOCKET_OUTPUT_STOPPED",
                output_path=str(obs_out),
            ),
        )
        _wait_for_state(sess, SessionState.ARMED_AFTER_TAKE)

        # New STARTED → retake of same deck.
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        assert sess.state is SessionState.RECORDING
        assert sess.armed_deck == ArmedDeck("c", "s", "01 Deck")

    def test_disarm_during_window_cancels(
        self, mock_obs: MagicMock, recording_root: Path, tmp_path: Path
    ):
        """Disarm during ARMED_AFTER_TAKE cancels the timer and goes IDLE
        immediately."""
        sess = _phase2_session(
            mock_obs,
            recording_root,
            short_take_seconds=0.0,
            retake_window_seconds=60.0,
        )
        sess.arm("c", "s", "01 Deck")

        obs_out = tmp_path / "rec.mkv"
        obs_out.write_bytes(b"real")
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="OBS_WEBSOCKET_OUTPUT_STOPPED",
                output_path=str(obs_out),
            ),
        )
        _wait_for_state(sess, SessionState.ARMED_AFTER_TAKE)

        sess.disarm()
        assert sess.state is SessionState.IDLE
        assert sess.armed_deck is None

    def test_arm_different_deck_during_window_switches(
        self, mock_obs: MagicMock, recording_root: Path, tmp_path: Path
    ):
        """Arming a different deck during ARMED_AFTER_TAKE cancels the
        timer and arms the new deck."""
        sess = _phase2_session(
            mock_obs,
            recording_root,
            short_take_seconds=0.0,
            retake_window_seconds=60.0,
        )
        sess.arm("c", "s", "01 Deck")

        obs_out = tmp_path / "rec.mkv"
        obs_out.write_bytes(b"real")
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="OBS_WEBSOCKET_OUTPUT_STOPPED",
                output_path=str(obs_out),
            ),
        )
        _wait_for_state(sess, SessionState.ARMED_AFTER_TAKE)

        sess.arm("c", "s", "02 Other Deck")
        assert sess.state is SessionState.ARMED
        assert sess.armed_deck == ArmedDeck("c", "s", "02 Other Deck")


class TestRenameTimeout:
    def test_wait_for_stable_honors_timeout(
        self, mock_obs: MagicMock, recording_root: Path, tmp_path: Path
    ):
        """A file whose size keeps growing should cause _wait_for_stable
        to raise TimeoutError once the rename budget elapses."""
        growing = tmp_path / "growing.mkv"
        growing.write_bytes(b"seed")

        sess = RecordingSession(
            mock_obs,
            recording_root,
            stability_interval=0.05,
            stability_checks=10,
            short_take_seconds=0.0,
            retake_window_seconds=0.0,
            rename_timeout_seconds=0.2,
        )

        # Spawn a thread that keeps changing the file size so it never
        # stabilises; _wait_for_stable must give up after the timeout.
        import time as _time

        stop = threading.Event()

        def grow():
            i = 1
            while not stop.is_set():
                try:
                    growing.write_bytes(b"x" * i)
                except OSError:
                    return
                i += 1
                _time.sleep(0.01)

        t = threading.Thread(target=grow, daemon=True)
        t.start()
        try:
            with pytest.raises(TimeoutError, match="did not stabilise"):
                sess._wait_for_stable(growing)
        finally:
            stop.set()
            t.join(timeout=1.0)


# ---------------------------------------------------------------------------
# Phase 3: retake pre-move + state wiring
# ---------------------------------------------------------------------------


class TestRetakePreMove:
    """The session demotes existing active-take files into ``takes/``.

    Each scenario exercises one arm of the ``_preserve_active_take`` logic
    so a future refactor cannot silently regress one path while the others
    still pass.
    """

    def _stop(self, mock_obs, obs_output: Path) -> None:
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="stopped",
                output_path=str(obs_output),
            ),
        )

    def test_retake_moves_final_and_archive_to_takes(
        self, session: RecordingSession, mock_obs, recording_root: Path, tmp_path: Path
    ):
        """Happy path: processed part gets demoted; new raw lands cleanly."""
        from clm.recordings.workflow.directories import archive_dir, takes_dir

        rel = Path("c") / "s"
        arc = archive_dir(recording_root) / rel
        fin = final_dir(recording_root) / rel
        arc.mkdir(parents=True)
        fin.mkdir(parents=True)
        (arc / "t--RAW.mkv").write_bytes(b"old-raw")
        (fin / "t.mp4").write_bytes(b"old-final")

        obs_output = tmp_path / "rec.mkv"
        obs_output.write_bytes(b"new-raw")

        session.arm("c", "s", "t")
        self._stop(mock_obs, obs_output)
        _wait_for_state(session, SessionState.IDLE, timeout=15.0)

        takes = takes_dir(recording_root) / rel
        assert (takes / "t (take 1)--RAW.mkv").read_bytes() == b"old-raw"
        assert (takes / "t (take 1).mp4").read_bytes() == b"old-final"
        # Archive/final slots are clear — ready for the new take to process.
        assert not (arc / "t--RAW.mkv").exists()
        assert not (fin / "t.mp4").exists()

        tp = to_process_dir(recording_root) / rel
        assert (tp / "t--RAW.mkv").read_bytes() == b"new-raw"

    def test_retake_when_only_raw_exists(
        self, session: RecordingSession, mock_obs, recording_root: Path, tmp_path: Path
    ):
        """Processing failed before retake — only a raw in archive/."""
        from clm.recordings.workflow.directories import archive_dir, takes_dir

        rel = Path("c") / "s"
        arc = archive_dir(recording_root) / rel
        arc.mkdir(parents=True)
        (arc / "t--RAW.mkv").write_bytes(b"old-raw")

        obs_output = tmp_path / "rec.mkv"
        obs_output.write_bytes(b"new-raw")

        session.arm("c", "s", "t")
        self._stop(mock_obs, obs_output)
        _wait_for_state(session, SessionState.IDLE, timeout=15.0)

        takes = takes_dir(recording_root) / rel
        assert (takes / "t (take 1)--RAW.mkv").read_bytes() == b"old-raw"

    def test_retake_when_only_final_exists(
        self, session: RecordingSession, mock_obs, recording_root: Path, tmp_path: Path
    ):
        """Raw manually deleted after processing — only a final to preserve."""
        from clm.recordings.workflow.directories import takes_dir

        rel = Path("c") / "s"
        fin = final_dir(recording_root) / rel
        fin.mkdir(parents=True)
        (fin / "t.mp4").write_bytes(b"old-final")

        obs_output = tmp_path / "rec.mkv"
        obs_output.write_bytes(b"new-raw")

        session.arm("c", "s", "t")
        self._stop(mock_obs, obs_output)
        _wait_for_state(session, SessionState.IDLE, timeout=15.0)

        takes = takes_dir(recording_root) / rel
        assert (takes / "t (take 1).mp4").read_bytes() == b"old-final"

    def test_retake_when_nothing_exists_yet(
        self, session: RecordingSession, mock_obs, recording_root: Path, tmp_path: Path
    ):
        """Retake fires before first processing finished — nothing to demote."""
        from clm.recordings.workflow.directories import takes_dir

        obs_output = tmp_path / "rec.mkv"
        obs_output.write_bytes(b"first-take")

        session.arm("c", "s", "t")
        self._stop(mock_obs, obs_output)
        _wait_for_state(session, SessionState.IDLE, timeout=15.0)

        takes = takes_dir(recording_root) / "c" / "s"
        assert not takes.exists() or list(takes.iterdir()) == []

    def test_retake_increments_take_number(
        self, session: RecordingSession, mock_obs, recording_root: Path, tmp_path: Path
    ):
        """Second retake writes ``(take 2)``; existing ``(take 1)`` is untouched."""
        from clm.recordings.workflow.directories import archive_dir, takes_dir

        rel = Path("c") / "s"
        arc = archive_dir(recording_root) / rel
        takes = takes_dir(recording_root) / rel
        arc.mkdir(parents=True)
        takes.mkdir(parents=True)
        # Pretend a prior retake already demoted take 1.
        (takes / "t (take 1)--RAW.mkv").write_bytes(b"take-1")
        (arc / "t--RAW.mkv").write_bytes(b"take-2-active")

        obs_output = tmp_path / "rec.mkv"
        obs_output.write_bytes(b"take-3")

        session.arm("c", "s", "t")
        self._stop(mock_obs, obs_output)
        _wait_for_state(session, SessionState.IDLE, timeout=15.0)

        assert (takes / "t (take 1)--RAW.mkv").read_bytes() == b"take-1"
        assert (takes / "t (take 2)--RAW.mkv").read_bytes() == b"take-2-active"

    def test_retake_companion_wav_also_preserved(
        self, session: RecordingSession, mock_obs, recording_root: Path, tmp_path: Path
    ):
        """``.wav`` companion in archive/ gets the same ``(take K)`` suffix."""
        from clm.recordings.workflow.directories import archive_dir, takes_dir

        rel = Path("c") / "s"
        arc = archive_dir(recording_root) / rel
        arc.mkdir(parents=True)
        (arc / "t--RAW.mkv").write_bytes(b"raw-video")
        (arc / "t--RAW.wav").write_bytes(b"raw-audio")

        obs_output = tmp_path / "rec.mkv"
        obs_output.write_bytes(b"new")

        session.arm("c", "s", "t")
        self._stop(mock_obs, obs_output)
        _wait_for_state(session, SessionState.IDLE, timeout=15.0)

        takes = takes_dir(recording_root) / rel
        assert (takes / "t (take 1)--RAW.mkv").read_bytes() == b"raw-video"
        assert (takes / "t (take 1)--RAW.wav").read_bytes() == b"raw-audio"

    def test_new_part_after_processed_parts_preserves_existing(
        self, session: RecordingSession, mock_obs, recording_root: Path, tmp_path: Path
    ):
        """Regression guard: adding part 2 while part 1 is already processed.

        The existing part-1 files live under ``archive/`` and ``final/``
        (not ``to-process/``), so the scanner should leave them untouched
        when the armed part is 2.
        """
        from clm.recordings.workflow.directories import archive_dir, takes_dir

        rel = Path("c") / "s"
        arc = archive_dir(recording_root) / rel
        fin = final_dir(recording_root) / rel
        arc.mkdir(parents=True)
        fin.mkdir(parents=True)
        (arc / "t (part 1)--RAW.mkv").write_bytes(b"p1-raw")
        (fin / "t (part 1).mp4").write_bytes(b"p1-final")

        obs_output = tmp_path / "rec.mkv"
        obs_output.write_bytes(b"p2-raw")

        session.arm("c", "s", "t", part_number=2)
        self._stop(mock_obs, obs_output)
        _wait_for_state(session, SessionState.IDLE, timeout=15.0)

        assert (arc / "t (part 1)--RAW.mkv").read_bytes() == b"p1-raw"
        assert (fin / "t (part 1).mp4").read_bytes() == b"p1-final"

        takes = takes_dir(recording_root) / rel
        assert not takes.exists() or list(takes.iterdir()) == []


class TestStateWiring:
    """When a ``CourseRecordingState`` is injected, disk renames sync to state."""

    def test_cascade_updates_state_paths(self, mock_obs, recording_root: Path, tmp_path: Path):
        """Multi-part cascade rename propagates to state.json."""
        from clm.recordings.state import CourseRecordingState, LectureState, RecordingPart

        tp = to_process_dir(recording_root) / "c" / "s"
        tp.mkdir(parents=True)
        unsuffixed = tp / "t--RAW.mkv"
        unsuffixed.write_bytes(b"old-p0")

        state = CourseRecordingState(
            course_id="cid",
            lectures=[
                LectureState(
                    lecture_id="l1",
                    display_name="L1",
                    parts=[RecordingPart(part=1, raw_file=str(unsuffixed))],
                )
            ],
        )

        session = RecordingSession(
            mock_obs,
            recording_root,
            stability_interval=0.01,
            stability_checks=1,
            short_take_seconds=0.0,
            retake_window_seconds=0.0,
            state=state,
        )

        obs_output = tmp_path / "rec.mkv"
        obs_output.write_bytes(b"new-p2")

        session.arm("c", "s", "t", part_number=2)
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="stopped",
                output_path=str(obs_output),
            ),
        )
        _wait_for_state(session, SessionState.IDLE, timeout=15.0)

        # state.json's raw_file updated from the unsuffixed path to (part 1).
        expected = tp / "t (part 1)--RAW.mkv"
        assert state.lectures[0].parts[0].raw_file == str(expected)

    def test_retake_updates_state_paths(self, mock_obs, recording_root: Path, tmp_path: Path):
        """Retake pre-move propagates to state.json processed_file pointer."""
        from clm.recordings.state import CourseRecordingState, LectureState, RecordingPart
        from clm.recordings.workflow.directories import archive_dir, takes_dir

        rel = Path("c") / "s"
        arc = archive_dir(recording_root) / rel
        fin = final_dir(recording_root) / rel
        arc.mkdir(parents=True)
        fin.mkdir(parents=True)
        old_raw = arc / "t--RAW.mkv"
        old_final = fin / "t.mp4"
        old_raw.write_bytes(b"old-raw")
        old_final.write_bytes(b"old-final")

        state = CourseRecordingState(
            course_id="cid",
            lectures=[
                LectureState(
                    lecture_id="l1",
                    display_name="L1",
                    parts=[
                        RecordingPart(
                            part=1,
                            raw_file=str(old_raw),
                            processed_file=str(old_final),
                            status="processed",
                        )
                    ],
                )
            ],
        )

        session = RecordingSession(
            mock_obs,
            recording_root,
            stability_interval=0.01,
            stability_checks=1,
            short_take_seconds=0.0,
            retake_window_seconds=0.0,
            state=state,
        )

        obs_output = tmp_path / "rec.mkv"
        obs_output.write_bytes(b"new-raw")

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
        _wait_for_state(session, SessionState.IDLE, timeout=15.0)

        takes = takes_dir(recording_root) / rel
        expected_raw = takes / "t (take 1)--RAW.mkv"
        expected_final = takes / "t (take 1).mp4"
        part = state.lectures[0].parts[0]
        assert part.raw_file == str(expected_raw)
        assert part.processed_file == str(expected_final)

    def test_interleaving_scenario_tracks_both_parts(
        self, mock_obs, recording_root: Path, tmp_path: Path
    ):
        """End-to-end reproduction of the bug the hardening plan fixes.

        Sequence: record part 0 → record part 2 on the same deck.
        Expected: filesystem cascades unsuffixed to ``(part 1)``, state
        records both parts (1 and 2), and no orphaned part-0 entry is
        left behind.
        """
        from clm.recordings.state import CourseRecordingState

        state = CourseRecordingState(course_id="c")
        state.ensure_lecture("l1", "t")

        session = RecordingSession(
            mock_obs,
            recording_root,
            stability_interval=0.01,
            stability_checks=1,
            short_take_seconds=0.0,
            retake_window_seconds=0.0,
            state=state,
        )

        tp = to_process_dir(recording_root) / "c" / "s"

        # --- Recording 1: part 0 (single-part mode, unsuffixed). ---
        obs_output_1 = tmp_path / "rec1.mkv"
        obs_output_1.write_bytes(b"p0")

        session.arm("c", "s", "t", part_number=0, lecture_id="l1")
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="stopped",
                output_path=str(obs_output_1),
            ),
        )
        _wait_for_state(session, SessionState.IDLE, timeout=15.0)

        lecture = state.get_lecture("l1")
        assert lecture is not None
        assert [p.part for p in lecture.parts] == [1]
        assert lecture.parts[0].raw_file == str(tp / "t--RAW.mkv")

        # --- Recording 2: part 2 (user bumps Part number). ---
        obs_output_2 = tmp_path / "rec2.mkv"
        obs_output_2.write_bytes(b"p2")

        session.arm("c", "s", "t", part_number=2, lecture_id="l1")
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="stopped",
                output_path=str(obs_output_2),
            ),
        )
        _wait_for_state(session, SessionState.IDLE, timeout=15.0)

        lecture = state.get_lecture("l1")
        assert lecture is not None
        assert sorted(p.part for p in lecture.parts) == [1, 2]

        part_1 = next(p for p in lecture.parts if p.part == 1)
        part_2 = next(p for p in lecture.parts if p.part == 2)
        assert part_1.raw_file == str(tp / "t (part 1)--RAW.mkv")
        assert part_2.raw_file == str(tp / "t (part 2)--RAW.mkv")

        # Filesystem matches.
        assert (tp / "t (part 1)--RAW.mkv").exists()
        assert (tp / "t (part 2)--RAW.mkv").exists()
        assert not (tp / "t--RAW.mkv").exists()

    def test_persist_callback_fires_on_rename(self, mock_obs, recording_root: Path, tmp_path: Path):
        """on_state_mutation is invoked after the session updates state."""
        from clm.recordings.state import CourseRecordingState

        state = CourseRecordingState(course_id="c")
        state.ensure_lecture("l1", "t")

        persisted: list[CourseRecordingState] = []

        session = RecordingSession(
            mock_obs,
            recording_root,
            stability_interval=0.01,
            stability_checks=1,
            short_take_seconds=0.0,
            retake_window_seconds=0.0,
            state=state,
            on_state_mutation=persisted.append,
        )

        obs_output = tmp_path / "rec.mkv"
        obs_output.write_bytes(b"p0")

        session.arm("c", "s", "t", part_number=0, lecture_id="l1")
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="stopped",
                output_path=str(obs_output),
            ),
        )
        _wait_for_state(session, SessionState.IDLE, timeout=15.0)

        assert persisted, "on_state_mutation was not invoked"
        assert persisted[-1] is state

    def test_provenance_stamped_on_first_take(self, mock_obs, recording_root: Path, tmp_path: Path):
        """A deck armed with provenance stamps the recorded part (issue #208)."""
        from clm.recordings.record_provenance import RecordProvenance
        from clm.recordings.state import CourseRecordingState

        state = CourseRecordingState(course_id="c")
        state.ensure_lecture("l1", "t")

        session = RecordingSession(
            mock_obs,
            recording_root,
            stability_interval=0.01,
            stability_checks=1,
            short_take_seconds=0.0,
            retake_window_seconds=0.0,
            state=state,
        )

        obs_output = tmp_path / "rec.mkv"
        obs_output.write_bytes(b"p0")

        prov = RecordProvenance(
            section_id="sec-1",
            topic_id="topic-x",
            slide_digest="sha256:abc",
            git_commit="deadbeef",
            git_dirty=True,
        )
        session.arm("c", "s", "t", part_number=0, lecture_id="l1", provenance=prov)
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="stopped",
                output_path=str(obs_output),
            ),
        )
        _wait_for_state(session, SessionState.IDLE, timeout=15.0)

        part = state.get_lecture("l1").parts[0]
        assert part.section_id == "sec-1"
        assert part.topic_id == "topic-x"
        assert part.slide_digest == "sha256:abc"
        assert part.git_commit == "deadbeef"
        assert part.git_dirty is True

    def test_no_provenance_leaves_fields_none(self, mock_obs, recording_root: Path, tmp_path: Path):
        """Arming without provenance keeps the pre-#208 behaviour (all None)."""
        from clm.recordings.state import CourseRecordingState

        state = CourseRecordingState(course_id="c")
        state.ensure_lecture("l1", "t")

        session = RecordingSession(
            mock_obs,
            recording_root,
            stability_interval=0.01,
            stability_checks=1,
            short_take_seconds=0.0,
            retake_window_seconds=0.0,
            state=state,
        )

        obs_output = tmp_path / "rec.mkv"
        obs_output.write_bytes(b"p0")

        session.arm("c", "s", "t", part_number=0, lecture_id="l1")
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="stopped",
                output_path=str(obs_output),
            ),
        )
        _wait_for_state(session, SessionState.IDLE, timeout=15.0)

        part = state.get_lecture("l1").parts[0]
        assert part.topic_id is None
        assert part.slide_digest is None
        assert part.git_commit is None
        assert part.git_dirty is False

    def test_provenance_stamped_on_retake(self, mock_obs, recording_root: Path, tmp_path: Path):
        """A retake stamps the new active take with the freshly armed provenance."""
        from clm.recordings.record_provenance import RecordProvenance
        from clm.recordings.state import CourseRecordingState, LectureState, RecordingPart
        from clm.recordings.workflow.directories import archive_dir

        rel = Path("c") / "s"
        arc = archive_dir(recording_root) / rel
        arc.mkdir(parents=True)
        old_raw = arc / "t--RAW.mkv"
        old_raw.write_bytes(b"old-raw")

        state = CourseRecordingState(
            course_id="cid",
            lectures=[
                LectureState(
                    lecture_id="l1",
                    display_name="L1",
                    parts=[
                        RecordingPart(
                            part=1,
                            raw_file=str(old_raw),
                            status="processed",
                            topic_id="topic-old",
                            slide_digest="sha256:old",
                        )
                    ],
                )
            ],
        )

        session = RecordingSession(
            mock_obs,
            recording_root,
            stability_interval=0.01,
            stability_checks=1,
            short_take_seconds=0.0,
            retake_window_seconds=0.0,
            state=state,
        )

        obs_output = tmp_path / "rec.mkv"
        obs_output.write_bytes(b"new-raw")

        prov = RecordProvenance(
            section_id="sec-1",
            topic_id="topic-new",
            slide_digest="sha256:new",
            git_commit="cafef00d",
            git_dirty=False,
        )
        session.arm("c", "s", "t", lecture_id="l1", provenance=prov)
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="stopped",
                output_path=str(obs_output),
            ),
        )
        _wait_for_state(session, SessionState.IDLE, timeout=15.0)

        part = state.get_lecture("l1").parts[0]
        # Active take carries the new provenance...
        assert part.topic_id == "topic-new"
        assert part.slide_digest == "sha256:new"
        assert part.git_commit == "cafef00d"
        # ...and the demoted take preserved the original.
        assert part.takes, "a take should have been demoted on retake"
        assert part.takes[0].topic_id == "topic-old"
        assert part.takes[0].slide_digest == "sha256:old"

    def test_cascade_rename_fires_on_path_rename(
        self, mock_obs, recording_root: Path, tmp_path: Path
    ):
        """The cascade rename on a multi-part take notifies on_path_rename.

        Regression for the in-flight raw_path rewrite: when the existing
        unsuffixed raw is renamed to ``(part 1)`` to make room for a new
        part 2, the session must publish the rename so the web layer can
        fix up any job that captured the old path before it submitted
        to Auphonic.
        """
        tp = to_process_dir(recording_root) / "c" / "s"
        tp.mkdir(parents=True)
        unsuffixed = tp / "t--RAW.mkv"
        unsuffixed.write_bytes(b"old-p0")

        renames: list[tuple[Path, Path]] = []

        session = RecordingSession(
            mock_obs,
            recording_root,
            stability_interval=0.01,
            stability_checks=1,
            short_take_seconds=0.0,
            retake_window_seconds=0.0,
            on_path_rename=lambda old, new: renames.append((old, new)),
        )

        obs_output = tmp_path / "rec.mkv"
        obs_output.write_bytes(b"new-p2")

        session.arm("c", "s", "t", part_number=2)
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="stopped",
                output_path=str(obs_output),
            ),
        )
        _wait_for_state(session, SessionState.IDLE, timeout=15.0)

        expected_old = unsuffixed
        expected_new = tp / "t (part 1)--RAW.mkv"
        assert (expected_old, expected_new) in renames

    def test_path_rename_not_fired_without_cascade(
        self, mock_obs, recording_root: Path, tmp_path: Path
    ):
        """A first-part recording has no cascade — on_path_rename stays quiet."""
        renames: list[tuple[Path, Path]] = []

        session = RecordingSession(
            mock_obs,
            recording_root,
            stability_interval=0.01,
            stability_checks=1,
            short_take_seconds=0.0,
            retake_window_seconds=0.0,
            on_path_rename=lambda old, new: renames.append((old, new)),
        )

        obs_output = tmp_path / "rec.mkv"
        obs_output.write_bytes(b"p0")

        session.arm("c", "s", "t", part_number=0)
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="stopped",
                output_path=str(obs_output),
            ),
        )
        _wait_for_state(session, SessionState.IDLE, timeout=15.0)

        assert renames == []

    def test_state_provider_resolves_multi_course(
        self, mock_obs, recording_root: Path, tmp_path: Path
    ):
        """The per-deck state_provider routes recordings to the matching course."""
        from clm.recordings.state import CourseRecordingState

        states = {
            "alpha": CourseRecordingState(course_id="alpha"),
            "beta": CourseRecordingState(course_id="beta"),
        }
        states["alpha"].ensure_lecture("a1", "t")

        session = RecordingSession(
            mock_obs,
            recording_root,
            stability_interval=0.01,
            stability_checks=1,
            short_take_seconds=0.0,
            retake_window_seconds=0.0,
            state_provider=lambda deck: states.get(deck.course_slug),
        )

        obs_output = tmp_path / "rec.mkv"
        obs_output.write_bytes(b"x")

        session.arm("alpha", "s", "t", part_number=0, lecture_id="a1")
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        _fire_event(
            mock_obs,
            RecordingEvent(
                output_active=False,
                output_state="stopped",
                output_path=str(obs_output),
            ),
        )
        _wait_for_state(session, SessionState.IDLE, timeout=15.0)

        assert states["alpha"].get_lecture("a1") is not None
        assert states["alpha"].get_lecture("a1").parts
        # beta should be untouched.
        assert states["beta"].lectures == []


# ---------------------------------------------------------------------------
# Helpers
class TestAdvanceTake:
    """`session.advance_take` demotes the active take into takes/ without recording.

    Companion to :class:`TestRetakePreMove`: those tests fire a real
    STARTED/STOPPED pair, this one runs the preserve cascade as a
    standalone operation.
    """

    def test_advance_moves_raw_and_final_to_takes(
        self,
        session: RecordingSession,
        recording_root: Path,
    ):
        from clm.recordings.workflow.directories import archive_dir, takes_dir

        rel = Path("c") / "s"
        arc = archive_dir(recording_root) / rel
        fin = final_dir(recording_root) / rel
        arc.mkdir(parents=True)
        fin.mkdir(parents=True)
        (arc / "t--RAW.mkv").write_bytes(b"raw")
        (fin / "t.mp4").write_bytes(b"final")

        preserved = session.advance_take("c", "s", "t")

        assert len(preserved) == 2
        takes = takes_dir(recording_root) / rel
        assert (takes / "t (take 1)--RAW.mkv").read_bytes() == b"raw"
        assert (takes / "t (take 1).mp4").read_bytes() == b"final"
        assert not (arc / "t--RAW.mkv").exists()
        assert not (fin / "t.mp4").exists()

    def test_advance_with_nothing_to_preserve_is_noop(
        self,
        session: RecordingSession,
        recording_root: Path,
    ):
        preserved = session.advance_take("c", "s", "t")
        assert preserved == []

    def test_advance_refuses_while_recording(self, session: RecordingSession, mock_obs):
        """Cannot demote an active take while a recording is in progress."""
        session.arm("c", "s", "t")
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        assert session.state is SessionState.RECORDING
        with pytest.raises(RuntimeError, match="Cannot advance take"):
            session.advance_take("c", "s", "t")

    def test_advance_transitions_armed_after_take_back_to_armed(
        self, mock_obs, recording_root: Path, tmp_path: Path
    ):
        """Advancing the current active take while ARMED_AFTER_TAKE returns to ARMED.

        Rationale: once the active-take slot is empty the retake-window
        semantics no longer apply; the user is implicitly opting to
        continue rather than retake.
        """
        session = RecordingSession(
            mock_obs,
            recording_root,
            stability_interval=0.01,
            stability_checks=1,
            short_take_seconds=0.0,
            retake_window_seconds=30.0,
        )
        obs_output = tmp_path / "rec.mkv"
        obs_output.write_bytes(b"first")

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
        _wait_for_state(session, SessionState.ARMED_AFTER_TAKE, timeout=15.0)

        preserved = session.advance_take("c", "s", "t")
        assert len(preserved) >= 1
        assert session.state is SessionState.ARMED

    def test_advance_updates_state_paths(self, mock_obs, recording_root: Path):
        """Preserved renames propagate to CourseRecordingState."""
        from clm.recordings.state import CourseRecordingState, LectureState, RecordingPart
        from clm.recordings.workflow.directories import archive_dir

        rel = Path("c") / "s"
        arc = archive_dir(recording_root) / rel
        arc.mkdir(parents=True)
        raw = arc / "t--RAW.mkv"
        raw.write_bytes(b"raw")

        state = CourseRecordingState(
            course_id="cid",
            lectures=[
                LectureState(
                    lecture_id="l1",
                    display_name="L1",
                    parts=[RecordingPart(part=1, raw_file=str(raw))],
                )
            ],
        )
        session = RecordingSession(
            mock_obs,
            recording_root,
            stability_interval=0.01,
            stability_checks=1,
            short_take_seconds=0.0,
            retake_window_seconds=0.0,
            state=state,
        )

        session.advance_take("c", "s", "t", lecture_id="l1")

        # State's raw_file pointer updated to the takes/ location.
        new_path = state.lectures[0].parts[0].raw_file
        assert "takes" in new_path
        assert "(take 1)--RAW.mkv" in new_path


class TestSwapActiveWithTake:
    """Tests for the pure module-level :func:`_swap_active_with_take` helper."""

    def _layout(
        self,
        recording_root: Path,
        rel: Path,
        *,
        active_in_archive: bool = False,
        active_has_final: bool = False,
        active_wav: bool = False,
        target_take: int = 1,
        target_has_final: bool = True,
    ) -> dict[str, Path]:
        """Materialise a deck with one active take and one historical take on disk.

        Returns a dict of named paths the test can assert against.
        """
        from clm.recordings.workflow.directories import takes_dir

        arc = archive_dir(recording_root) / rel
        tp = to_process_dir(recording_root) / rel
        fin = final_dir(recording_root) / rel
        takes = takes_dir(recording_root) / rel
        for d in (arc, tp, fin, takes):
            d.mkdir(parents=True, exist_ok=True)

        active_raw_dir = arc if active_in_archive else tp
        active_raw = active_raw_dir / "deck--RAW.mkv"
        active_raw.write_bytes(b"active-raw")
        result = {"active_raw": active_raw}

        if active_wav:
            active_wav_path = active_raw_dir / "deck--RAW.wav"
            active_wav_path.write_bytes(b"active-wav")
            result["active_wav"] = active_wav_path

        if active_has_final:
            active_final = fin / "deck.mp4"
            active_final.write_bytes(b"active-final")
            result["active_final"] = active_final

        target_raw = takes / f"deck (take {target_take})--RAW.mkv"
        target_raw.write_bytes(b"target-raw")
        result["target_raw"] = target_raw

        if target_has_final:
            target_final = takes / f"deck (take {target_take}).mp4"
            target_final.write_bytes(b"target-final")
            result["target_final"] = target_final

        result["takes"] = takes
        result["arc"] = arc
        result["tp"] = tp
        result["fin"] = fin
        return result

    def test_happy_path_processed_target(self, recording_root: Path):
        """Active processed deck swaps with a processed historical take."""
        from clm.recordings.workflow.session import _swap_active_with_take

        rel = Path("c") / "s"
        paths = self._layout(
            recording_root,
            rel,
            active_in_archive=True,
            active_has_final=True,
            target_take=1,
            target_has_final=True,
        )

        renames = _swap_active_with_take(
            recordings_root=recording_root,
            rel_dir=str(rel),
            deck_name="deck",
            part=0,
            active_take=3,
            target_take=1,
        )

        # Active take 3 went to takes/ with the (take 3) suffix.
        assert (paths["takes"] / "deck (take 3)--RAW.mkv").read_bytes() == b"active-raw"
        assert (paths["takes"] / "deck (take 3).mp4").read_bytes() == b"active-final"
        # Target take 1 became active — same paths as before, new content.
        assert (paths["arc"] / "deck--RAW.mkv").read_bytes() == b"target-raw"
        assert (paths["fin"] / "deck.mp4").read_bytes() == b"target-final"
        # The take-1 slots in takes/ are now empty.
        assert not paths["target_raw"].exists()
        assert not paths["target_final"].exists()
        # Returned renames are non-empty and ordered: phase A first, then phase B.
        assert len(renames) == 4

    def test_pending_active_pending_target_to_process_only(self, recording_root: Path):
        """Pending → pending swap leaves both raws in to-process/, never archive/."""
        from clm.recordings.workflow.session import _swap_active_with_take

        rel = Path("c") / "s"
        paths = self._layout(
            recording_root,
            rel,
            active_in_archive=False,
            active_has_final=False,
            target_take=1,
            target_has_final=False,
        )

        _swap_active_with_take(
            recordings_root=recording_root,
            rel_dir=str(rel),
            deck_name="deck",
            part=0,
            active_take=2,
            target_take=1,
        )

        assert (paths["tp"] / "deck--RAW.mkv").read_bytes() == b"target-raw"
        assert (paths["takes"] / "deck (take 2)--RAW.mkv").read_bytes() == b"active-raw"
        # Archive must remain empty — neither take was processed.
        assert not list(paths["arc"].iterdir())

    def test_final_edl_sidecar_moves_with_swap(self, recording_root: Path):
        """An ``.edl`` (or any sidecar sharing the final video stem) is preserved.

        Before ``_scan_active_take_files`` was widened to a stem sweep,
        non-video sidecars (Auphonic's ``.edl`` cut list, future
        ``.vtt``/``.srt``/``.json``/``.html`` outputs) got left behind
        on every retake and never came back on restore. The fix anchors
        on the video file and sweeps every sibling sharing its stem.
        """
        from clm.recordings.workflow.session import _swap_active_with_take

        rel = Path("c") / "s"
        paths = self._layout(
            recording_root,
            rel,
            active_in_archive=True,
            active_has_final=True,
            target_take=1,
            target_has_final=True,
        )
        active_edl = paths["fin"] / "deck.edl"
        active_edl.write_bytes(b"active-edl")
        target_edl = paths["takes"] / "deck (take 1).edl"
        target_edl.write_bytes(b"target-edl")

        _swap_active_with_take(
            recordings_root=recording_root,
            rel_dir=str(rel),
            deck_name="deck",
            part=0,
            active_take=3,
            target_take=1,
        )

        assert (paths["takes"] / "deck (take 3).edl").read_bytes() == b"active-edl"
        assert (paths["fin"] / "deck.edl").read_bytes() == b"target-edl"

    def test_companion_wav_moves_with_raw(self, recording_root: Path):
        """A ``.wav`` companion in the active slot rides along to takes/."""
        from clm.recordings.workflow.session import _swap_active_with_take

        rel = Path("c") / "s"
        paths = self._layout(
            recording_root,
            rel,
            active_wav=True,
            target_take=1,
            target_has_final=False,
        )

        _swap_active_with_take(
            recordings_root=recording_root,
            rel_dir=str(rel),
            deck_name="deck",
            part=0,
            active_take=2,
            target_take=1,
        )

        assert (paths["takes"] / "deck (take 2)--RAW.wav").read_bytes() == b"active-wav"
        # Active wav slot now holds the restored target raw (no companion wav existed).
        assert not (paths["tp"] / "deck--RAW.wav").exists()

    def test_missing_target_raises(self, recording_root: Path):
        """Empty takes/ subtree → :class:`FileNotFoundError`."""
        from clm.recordings.workflow.session import _swap_active_with_take

        rel = Path("c") / "s"
        tp = to_process_dir(recording_root) / rel
        tp.mkdir(parents=True)
        (tp / "deck--RAW.mkv").write_bytes(b"active")

        with pytest.raises(FileNotFoundError, match="No files for take"):
            _swap_active_with_take(
                recordings_root=recording_root,
                rel_dir=str(rel),
                deck_name="deck",
                part=0,
                active_take=2,
                target_take=99,
            )

    def test_rollback_on_phase_b_failure(
        self, recording_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """A failure during phase B reverses every completed move."""
        from clm.recordings.workflow import session as session_mod

        rel = Path("c") / "s"
        paths = self._layout(
            recording_root,
            rel,
            active_in_archive=True,
            active_has_final=True,
            target_take=1,
            target_has_final=True,
        )

        original_move = session_mod.safe_move
        call_count = {"n": 0}

        def flaky_move(src, dst, *args, **kwargs):
            call_count["n"] += 1
            # Fail on the third move (first phase B move) — phase A has
            # 2 moves (raw + final → takes/), phase B starts at call 3.
            if call_count["n"] == 3:
                raise OSError("simulated failure")
            return original_move(src, dst, *args, **kwargs)

        monkeypatch.setattr(session_mod, "safe_move", flaky_move)

        with pytest.raises(OSError, match="simulated"):
            session_mod._swap_active_with_take(
                recordings_root=recording_root,
                rel_dir=str(rel),
                deck_name="deck",
                part=0,
                active_take=3,
                target_take=1,
            )

        # After rollback the original layout must be restored.
        assert paths["active_raw"].read_bytes() == b"active-raw"
        assert paths["active_final"].read_bytes() == b"active-final"
        assert paths["target_raw"].read_bytes() == b"target-raw"
        assert paths["target_final"].read_bytes() == b"target-final"
        assert not (paths["takes"] / "deck (take 3)--RAW.mkv").exists()
        assert not (paths["takes"] / "deck (take 3).mp4").exists()


class TestSessionRestoreTake:
    """Integration tests for :meth:`RecordingSession.restore_take`."""

    def _build_state_with_history(self, recording_root: Path):  # type: ignore[no-untyped-def]
        """Build state matching a deck with active take 3 and history takes 1, 2."""
        from clm.recordings.state import (
            CourseRecordingState,
            LectureState,
            RecordingPart,
            TakeRecord,
        )
        from clm.recordings.workflow.directories import takes_dir

        rel = Path("c") / "s"
        arc = archive_dir(recording_root) / rel
        fin = final_dir(recording_root) / rel
        takes = takes_dir(recording_root) / rel
        for d in (arc, fin, takes):
            d.mkdir(parents=True, exist_ok=True)

        active_raw = arc / "deck--RAW.mkv"
        active_final = fin / "deck.mp4"
        active_raw.write_bytes(b"a3-raw")
        active_final.write_bytes(b"a3-final")

        t1_raw = takes / "deck (take 1)--RAW.mkv"
        t1_final = takes / "deck (take 1).mp4"
        t1_raw.write_bytes(b"t1-raw")
        t1_final.write_bytes(b"t1-final")
        t2_raw = takes / "deck (take 2)--RAW.mkv"
        t2_final = takes / "deck (take 2).mp4"
        t2_raw.write_bytes(b"t2-raw")
        t2_final.write_bytes(b"t2-final")

        state = CourseRecordingState(
            course_id="c",
            lectures=[
                LectureState(
                    lecture_id="l1",
                    display_name="L1",
                    parts=[
                        RecordingPart(
                            part=1,
                            active_take=3,
                            raw_file=str(active_raw),
                            processed_file=str(active_final),
                            status="processed",
                            takes=[
                                TakeRecord(
                                    take=1,
                                    raw_file=str(t1_raw),
                                    processed_file=str(t1_final),
                                    status="processed",
                                ),
                                TakeRecord(
                                    take=2,
                                    raw_file=str(t2_raw),
                                    processed_file=str(t2_final),
                                    status="processed",
                                ),
                            ],
                        )
                    ],
                )
            ],
        )
        return state, {
            "active_raw": active_raw,
            "active_final": active_final,
            "t1_raw": t1_raw,
            "t1_final": t1_final,
            "t2_raw": t2_raw,
            "t2_final": t2_final,
            "arc": arc,
            "fin": fin,
            "takes": takes,
            "rel": rel,
        }

    def test_restore_swaps_state_and_filesystem(self, mock_obs: MagicMock, recording_root: Path):
        """End-to-end: state + FS both reflect the swap."""
        state, paths = self._build_state_with_history(recording_root)
        session = RecordingSession(
            mock_obs,
            recording_root,
            stability_interval=0.01,
            stability_checks=1,
            short_take_seconds=0.0,
            retake_window_seconds=0.0,
            state=state,
        )

        session.restore_take("c", "s", "deck", 1, lecture_id="l1")

        # Active is now take 1 with the original take-1 paths in the active slot.
        part = state.lectures[0].parts[0]
        assert part.active_take == 1
        assert part.raw_file == str(paths["arc"] / "deck--RAW.mkv")
        assert part.processed_file == str(paths["fin"] / "deck.mp4")
        # History now contains takes 2 (untouched) and 3 (the demoted ex-active).
        assert sorted(t.take for t in part.takes) == [2, 3]
        take3 = next(t for t in part.takes if t.take == 3)
        assert "(take 3)--RAW.mkv" in take3.raw_file
        # Filesystem reflects the swap.
        assert (paths["arc"] / "deck--RAW.mkv").read_bytes() == b"t1-raw"
        assert (paths["fin"] / "deck.mp4").read_bytes() == b"t1-final"
        assert (paths["takes"] / "deck (take 3)--RAW.mkv").read_bytes() == b"a3-raw"
        assert (paths["takes"] / "deck (take 3).mp4").read_bytes() == b"a3-final"
        # Take 2 untouched.
        assert (paths["takes"] / "deck (take 2)--RAW.mkv").read_bytes() == b"t2-raw"

    def test_advance_after_restore_uses_state_take_number(
        self, mock_obs: MagicMock, recording_root: Path
    ):
        """advance_take must demote with the state's ``active_take`` number.

        After a restore, ``state.active_take`` and the filesystem max
        in ``takes/`` diverge: state still calls the restored take "1"
        while ``takes/`` already holds "(take 2)" and "(take 3)". The
        pre-fix demote path used FS max+1 = 4 (or, mid-state, a number
        that collided with an existing entry), producing two rows
        labeled with the same take number in the panel. This test
        pins the fix: the demoted file is named with the take's
        stable identity, "(take 1)".
        """
        state, paths = self._build_state_with_history(recording_root)
        session = RecordingSession(
            mock_obs,
            recording_root,
            stability_interval=0.01,
            stability_checks=1,
            short_take_seconds=0.0,
            retake_window_seconds=0.0,
            state=state,
        )
        session.restore_take("c", "s", "deck", 1, lecture_id="l1")

        # advance_take demotes the active slot without recording a
        # new file — exercises the same _preserve_active_take path
        # the OBS rename thread uses on a real retake.
        session.advance_take("c", "s", "deck", lecture_id="l1")

        # The demoted file is named with the restored take's number (1),
        # not the FS max+1 (which would be 4 — and any FS-derived
        # number ≤3 would collide with an existing entry).
        assert (paths["takes"] / "deck (take 1)--RAW.mkv").exists()
        # Pre-existing takes 2 and 3 (from the swap) are untouched.
        assert (paths["takes"] / "deck (take 2)--RAW.mkv").exists()
        assert (paths["takes"] / "deck (take 3)--RAW.mkv").exists()

    def test_retake_after_restore_does_not_clobber_history(
        self, mock_obs: MagicMock, recording_root: Path
    ):
        """The Phase D correctness invariant: restore + retake → take 4, not take 2 overwrite."""
        state, paths = self._build_state_with_history(recording_root)
        session = RecordingSession(
            mock_obs,
            recording_root,
            stability_interval=0.01,
            stability_checks=1,
            short_take_seconds=0.0,
            retake_window_seconds=0.0,
            state=state,
        )

        session.restore_take("c", "s", "deck", 1, lecture_id="l1")

        # Now record a fresh retake on top of the restored take 1.
        new_raw = "/obs/new-take.mkv"
        state.record_retake("l1", 1, new_raw)

        part = state.lectures[0].parts[0]
        assert part.active_take == 4
        assert part.raw_file == new_raw
        # Takes 1, 2, 3 all present and intact.
        assert sorted(t.take for t in part.takes) == [1, 2, 3]
        take2 = next(t for t in part.takes if t.take == 2)
        assert take2.raw_file == str(paths["t2_raw"])

    def test_restore_refuses_while_recording(self, mock_obs: MagicMock, recording_root: Path):
        state, _ = self._build_state_with_history(recording_root)
        session = RecordingSession(
            mock_obs,
            recording_root,
            stability_interval=0.01,
            stability_checks=1,
            short_take_seconds=0.0,
            retake_window_seconds=0.0,
            state=state,
        )
        session.arm("c", "s", "deck", lecture_id="l1")
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))
        assert session.state is SessionState.RECORDING

        with pytest.raises(RuntimeError, match="Cannot restore take"):
            session.restore_take("c", "s", "deck", 1, lecture_id="l1")

    def test_restore_unknown_take_raises(self, mock_obs: MagicMock, recording_root: Path):
        state, _ = self._build_state_with_history(recording_root)
        session = RecordingSession(
            mock_obs,
            recording_root,
            stability_interval=0.01,
            stability_checks=1,
            short_take_seconds=0.0,
            retake_window_seconds=0.0,
            state=state,
        )

        with pytest.raises(ValueError, match="Take 99 not found"):
            session.restore_take("c", "s", "deck", 99, lecture_id="l1")


# ---------------------------------------------------------------------------


class _SessionStateRecorder:
    """Event-driven waiter for session state, fed by ``on_state_change``.

    The session calls its ``on_state_change`` callback (``RecordingSession.
    _notify``) *outside the lock* immediately after every transition, on the
    thread that made the transition. Attaching this recorder there lets
    :meth:`wait_for` block on a ``Condition`` and wake the instant a transition
    fires, instead of busy-polling.

    Why this matters: the old helper spun ``while session.state != expected:
    time.sleep(0.01)``. Under heavy xdist load that 100 Hz spin competes for
    CPU with the very background thread (OBS event handler, retake ``Timer``,
    watcher) it is waiting on — so the waited-for transition arrives *later*,
    and the wait can hit its ceiling and flake. That self-inflicted contention
    grows with the worker count, which is exactly why the pre-commit cap had
    been pinned low. A ``Condition`` wait sleeps the waiter (zero CPU spin), so
    the background thread runs unimpeded and we still wake immediately on the
    transition. The predicate stays ``session.state == expected`` (current
    state, not history) so repeated waits for a recurring state are correct.
    """

    def __init__(self, initial: SessionState) -> None:
        self.cond = threading.Condition()
        # Transition trail, for diagnostics in the timeout message only — never
        # used as the wait predicate (that would break repeated waits for a
        # state the session legitimately re-enters).
        self.history: list[SessionState] = [initial]

    def __call__(self, snapshot: SessionSnapshot) -> None:
        with self.cond:
            self.history.append(snapshot.state)
            self.cond.notify_all()

    def wait_for(self, session: RecordingSession, expected: SessionState, timeout: float) -> None:
        import time

        deadline = time.monotonic() + timeout
        with self.cond:
            while session.state != expected:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"Session did not reach {expected.value} within {timeout}s "
                        f"(stuck at {session.state.value}; "
                        f"transitions={[s.value for s in self.history]})"
                    )
                self.cond.wait(timeout=remaining)


def _attach_state_recorder(session: RecordingSession) -> _SessionStateRecorder:
    """Get-or-create the recorder wired into *session*'s ``on_state_change``.

    Idempotent and cached on the session, so repeated ``_wait_for_state`` calls
    share one waiter. Composes with any pre-existing callback (tests don't set
    one today, but stay defensive) so the original still fires.
    """
    recorder = getattr(session, "_test_state_recorder", None)
    if recorder is not None:
        return recorder

    recorder = _SessionStateRecorder(session.state)
    previous = session._on_state_change
    if previous is None:
        session._on_state_change = recorder
    else:

        def _chained(snapshot: SessionSnapshot) -> None:
            recorder(snapshot)
            previous(snapshot)

        session._on_state_change = _chained
    session._test_state_recorder = recorder
    return recorder


def _wait_for_state(
    session: RecordingSession,
    expected: SessionState,
    timeout: float = 15.0,
) -> None:
    """Block until the session reaches *expected*, or raise after *timeout*.

    Event-driven via :class:`_SessionStateRecorder` rather than a CPU-burning
    busy-poll, so it stays reliable under heavy parallel load (see that class's
    docstring). The generous default ``timeout`` is a backstop for a genuinely
    stuck session; success returns the instant the state is reached.
    """
    _attach_state_recorder(session).wait_for(session, expected, timeout)


# ---------------------------------------------------------------------------
# Lock contention regression: the multi-part-during-upload race
# ---------------------------------------------------------------------------


class TestLockContention:
    """Regression coverage for the May 2026 multi-part-during-upload incident.

    Setup mirrors the forensic data left in ``D:\\CLM\\Recordings``:
    one Auphonic upload is mid-flight (file lock held), and the user
    starts recording the next part. Pre-fix, this produced duplicates
    via ``shutil.move``'s copy+unlink fallback. Post-fix, the cascade
    rename is deferred to the queue and the new recording lands cleanly
    at the correct slot.
    """

    def test_cascade_defers_locked_files(self, recording_root: Path) -> None:
        """When the source is locked, ``_cascade_unsuffixed_to_part1`` enqueues."""
        from clm.recordings.workflow import rename_queue as rq_mod
        from clm.recordings.workflow import session as session_mod
        from clm.recordings.workflow.directories import (
            archive_dir,
            to_process_dir,
        )
        from clm.recordings.workflow.rename_queue import PendingRenameQueue
        from clm.recordings.workflow.safe_move import FileLockedError

        td = to_process_dir(recording_root) / "c" / "s"
        td.mkdir(parents=True)
        ad = archive_dir(recording_root) / "c" / "s"
        ad.mkdir(parents=True)
        # Unsuffixed raw in archive/ — simulates Job 1 having moved it
        # there post-processing, but the next attempt to rename it (the
        # cascade) hits a persistent lock (e.g. a stuck AV handle).
        (ad / "deck--RAW.mp4").write_bytes(b"raw")

        queue = PendingRenameQueue()

        def always_locked(src: Path, dst: Path, **kwargs):
            raise FileLockedError(src, dst, 4, PermissionError("WinError 32"))

        # Patch both bindings so the queue's optimistic re-try also
        # observes the lock — the test is verifying the persistent-lock
        # path, not the self-heal path.
        with (
            patch.object(session_mod, "safe_move", side_effect=always_locked),
            patch.object(rq_mod, "safe_move", side_effect=always_locked),
        ):
            session_mod._cascade_unsuffixed_to_part1(
                recordings_root=recording_root,
                target_dir=td,
                deck_name="deck",
                raw_suffix="--RAW",
                lang="en",
                pending=queue,
            )

        # File is still in archive/ unsuffixed (lock prevented rename).
        assert (ad / "deck--RAW.mp4").exists()
        assert not (ad / "deck (part 1)--RAW.mp4").exists()
        # And the rename is parked on the queue, ready for a future drain.
        assert len(queue) == 1
        [entry] = queue.snapshot()
        assert entry.src == ad / "deck--RAW.mp4"
        assert entry.dst == ad / "deck (part 1)--RAW.mp4"

    def test_takes_promotion_runs_during_cascade(self, recording_root: Path) -> None:
        """Pre-existing ``(take K)`` files in takes/ are promoted to ``(part 1, take K)``.

        This is the cosmetic-but-confusing half of the May 2026 incident:
        takes 1-4 of the deck were demoted while it was still single-part,
        so they kept their unsuffixed ``(take K)`` names even after the
        deck was promoted to multi-part by the cascade. The new
        ``_promote_takes_to_part1`` step fixes that.
        """
        from clm.recordings.workflow import session as session_mod
        from clm.recordings.workflow.directories import takes_dir, to_process_dir

        td = to_process_dir(recording_root) / "c" / "s"
        td.mkdir(parents=True)
        ts = takes_dir(recording_root) / "c" / "s"
        ts.mkdir(parents=True)
        (ts / "deck (take 1)--RAW.mp4").write_bytes(b"t1")
        (ts / "deck (take 2)--RAW.mp4").write_bytes(b"t2")
        (ts / "deck (take 3)--RAW.mp4").write_bytes(b"t3")

        session_mod._cascade_unsuffixed_to_part1(
            recordings_root=recording_root,
            target_dir=td,
            deck_name="deck",
            raw_suffix="--RAW",
            lang="en",
        )

        for k in (1, 2, 3):
            assert not (ts / f"deck (take {k})--RAW.mp4").exists(), k
            assert (ts / f"deck (part 1, take {k})--RAW.mp4").exists(), k

    def test_takes_promotion_uses_lang_specific_label(self, recording_root: Path) -> None:
        """German decks should promote to ``(Teil 1, take K)``, not ``(part 1, take K)``."""
        from clm.recordings.workflow import session as session_mod
        from clm.recordings.workflow.directories import takes_dir, to_process_dir

        td = to_process_dir(recording_root) / "c" / "s"
        td.mkdir(parents=True)
        ts = takes_dir(recording_root) / "c" / "s"
        ts.mkdir(parents=True)
        (ts / "deck (take 1)--RAW.mp4").write_bytes(b"t1")

        session_mod._cascade_unsuffixed_to_part1(
            recordings_root=recording_root,
            target_dir=td,
            deck_name="deck",
            raw_suffix="--RAW",
            lang="de",
        )

        assert (ts / "deck (Teil 1, take 1)--RAW.mp4").exists()

    def test_takes_promotion_skips_already_multipart_files(self, recording_root: Path) -> None:
        """``(part N, take K)`` files are left alone — they're already correct."""
        from clm.recordings.workflow import session as session_mod
        from clm.recordings.workflow.directories import takes_dir, to_process_dir

        td = to_process_dir(recording_root) / "c" / "s"
        td.mkdir(parents=True)
        ts = takes_dir(recording_root) / "c" / "s"
        ts.mkdir(parents=True)
        (ts / "deck (part 2, take 5)--RAW.mp4").write_bytes(b"t5")

        session_mod._cascade_unsuffixed_to_part1(
            recordings_root=recording_root,
            target_dir=td,
            deck_name="deck",
            raw_suffix="--RAW",
            lang="en",
        )

        # File should be untouched.
        assert (ts / "deck (part 2, take 5)--RAW.mp4").exists()


class TestObsOutputLandingFallback:
    """The new recording must land *somewhere* even if the target slot is locked.

    The fix's contract: when the previous take's slot is held by an
    Auphonic upload and can't be superseded, the new OBS output is
    placed at a take-suffixed sibling so the recording isn't lost.
    """

    def test_obs_landing_falls_back_when_target_locked(
        self, recording_root: Path, mock_obs: MagicMock, tmp_path: Path
    ) -> None:
        from clm.recordings.workflow import session as session_mod
        from clm.recordings.workflow.directories import takes_dir, to_process_dir
        from clm.recordings.workflow.safe_move import FileLockedError, safe_move

        # Use a non-zero retake window so the rename thread has time to
        # finish before the post-take auto-disarm timer fires; without
        # this the session bounces straight back to IDLE and the
        # ``last_output`` snapshot can race the test's assertions.
        session = RecordingSession(
            mock_obs,
            recording_root,
            stability_interval=0.01,
            stability_checks=1,
            short_take_seconds=0.0,
            retake_window_seconds=30.0,
        )

        obs_output = tmp_path / "obs.mp4"
        obs_output.write_bytes(b"new take")
        target_dir = to_process_dir(recording_root) / "c" / "s"
        target_dir.mkdir(parents=True)
        target_slot = target_dir / "deck--RAW.mp4"

        original_safe_move = safe_move

        def lock_target_slot(src, dst, **kwargs):
            # Only the move into the target to-process/ slot is blocked.
            # The fallback into takes/ goes through.
            if Path(dst) == target_slot:
                raise FileLockedError(Path(src), Path(dst), 4, PermissionError("WinError 32"))
            return original_safe_move(Path(src), Path(dst), **kwargs)

        session.arm("c", "s", "deck")
        _fire_event(mock_obs, RecordingEvent(output_active=True, output_state="started"))

        # Patch both bindings so the queue's optimistic re-try also
        # observes the lock — without that, the test couldn't tell the
        # park-and-defer behavior apart from a self-healing transient
        # lock that the queue successfully drained immediately.
        from clm.recordings.workflow import rename_queue as rq_mod

        with (
            patch.object(session_mod, "safe_move", side_effect=lock_target_slot),
            patch.object(rq_mod, "safe_move", side_effect=lock_target_slot),
        ):
            _fire_event(
                mock_obs,
                RecordingEvent(
                    output_active=False,
                    output_state="OBS_WEBSOCKET_OUTPUT_STOPPED",
                    output_path=str(obs_output),
                ),
            )
            _wait_for_state(session, SessionState.ARMED_AFTER_TAKE, timeout=15.0)

        # The OBS file is no longer at its source — it landed somewhere.
        assert not obs_output.exists()
        # And it landed in takes/ as a fallback take-suffixed file.
        ts = takes_dir(recording_root) / "c" / "s"
        landed = list(ts.glob("deck (take *)--RAW.mp4"))
        assert len(landed) == 1
        assert landed[0].read_bytes() == b"new take"
        # Pending queue holds the deferred move-to-target.
        assert len(session.pending_renames) == 1
