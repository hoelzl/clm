"""Tests for the recording session state machine."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clm.recordings.workflow.directories import (
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

        with patch("clm.recordings.workflow.session.shutil.move"):
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
    @patch("clm.recordings.workflow.session.shutil.move")
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
        assert src == str(obs_output)
        assert "python-basics" in dst
        assert "Section 01" in dst
        assert "01 Intro--RAW.mkv" in dst

    @patch("clm.recordings.workflow.session.shutil.move")
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
        assert "03 Intro (part 2)--RAW.mkv" in dst

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

        _wait_for_state(session, SessionState.IDLE, timeout=15.0)

        snap = session.snapshot()
        assert snap.last_output is not None
        assert snap.last_output.name == "t--RAW.mp4"

    @patch("clm.recordings.workflow.session.shutil.move")
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

    def test_rename_supersedes_existing_target(
        self, session: RecordingSession, mock_obs, recording_root: Path, tmp_path
    ):
        """When the target file already exists, it is moved to superseded/."""
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

        # Old recording moved to superseded
        sup = superseded_dir(recording_root) / "c" / "s" / "t--RAW.mkv"
        assert sup.exists()
        assert sup.read_bytes() == b"old recording"


# ---------------------------------------------------------------------------
# Dynamic part naming
# ---------------------------------------------------------------------------


class TestDynamicPartNaming:
    def test_part_0_no_existing(self, recording_root: Path):
        """No files exist, part 0 → unsuffixed target."""
        td = to_process_dir(recording_root) / "c" / "s"
        td.mkdir(parents=True)
        target = _prepare_target_slot(td, "deck", ".mkv", 0, "--RAW", recording_root)
        assert target.name == "deck--RAW.mkv"

    def test_part_2_renames_unsuffixed_to_part_1(self, recording_root: Path):
        """Existing unsuffixed file renamed to (part 1) when part 2 is recorded."""
        td = to_process_dir(recording_root) / "c" / "s"
        td.mkdir(parents=True)
        (td / "deck--RAW.mkv").write_bytes(b"old")

        target = _prepare_target_slot(td, "deck", ".mkv", 2, "--RAW", recording_root)

        assert target.name == "deck (part 2)--RAW.mkv"
        assert not (td / "deck--RAW.mkv").exists()
        assert (td / "deck (part 1)--RAW.mkv").exists()
        assert (td / "deck (part 1)--RAW.mkv").read_bytes() == b"old"

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

        target = _prepare_target_slot(td, "deck", ".mkv", 0, "--RAW", recording_root)

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

        target = _prepare_target_slot(td, "deck", ".mkv", 2, "--RAW", recording_root)

        assert target.name == "deck (part 2)--RAW.mkv"
        # Part 1 untouched
        assert (td / "deck (part 1)--RAW.mkv").read_bytes() == b"p1"
        # Old part 2 superseded
        sup = superseded_dir(recording_root) / "c" / "s" / "deck (part 2)--RAW.mkv"
        assert sup.exists()
        assert sup.read_bytes() == b"old p2"

    def test_part_3_with_existing_parts(self, recording_root: Path):
        """Adding part 3 when parts 1 and 2 exist: no cascade needed."""
        td = to_process_dir(recording_root) / "c" / "s"
        td.mkdir(parents=True)
        (td / "deck (part 1)--RAW.mkv").write_bytes(b"p1")
        (td / "deck (part 2)--RAW.mkv").write_bytes(b"p2")

        target = _prepare_target_slot(td, "deck", ".mkv", 3, "--RAW", recording_root)

        assert target.name == "deck (part 3)--RAW.mkv"
        # Existing parts untouched
        assert (td / "deck (part 1)--RAW.mkv").read_bytes() == b"p1"
        assert (td / "deck (part 2)--RAW.mkv").read_bytes() == b"p2"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wait_for_state(
    session: RecordingSession,
    expected: SessionState,
    timeout: float = 15.0,
) -> None:
    """Block until the session reaches the expected state or timeout.

    Default timeout is generous to tolerate Windows scheduler jitter under
    parallel xdist load; the poll loop exits immediately on match, so a fast
    state transition still completes quickly.
    """
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
