"""Recording session state machine.

Coordinates the recording workflow by tracking which slide deck is "armed"
for recording and responding to OBS events to rename the output file
into the structured directory layout.

State transitions::

    idle ──arm()──► armed ──OBS starts──► recording ──OBS stops──► renaming ──done──► idle
      ▲               │                                                                │
      └──disarm()─────┘                                                                │
      └────────────────────────────────────────────────────────────────────────────────┘

The session manager is **thread-safe**.  OBS events arrive on a background
thread (from ``obsws-python``), while :meth:`arm` / :meth:`disarm` are
called from the main thread or a web request handler.
"""

from __future__ import annotations

import enum
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from .directories import to_process_dir
from .naming import DEFAULT_RAW_SUFFIX, raw_filename, recording_relative_dir
from .obs import ObsClient, RecordingEvent


class SessionState(enum.Enum):
    """Recording session states."""

    IDLE = "idle"
    ARMED = "armed"
    RECORDING = "recording"
    RENAMING = "renaming"


@dataclass(frozen=True)
class ArmedDeck:
    """Identifies the slide deck armed for the next recording."""

    course_slug: str
    section_name: str
    deck_name: str
    part_number: int = 0


# Keep old name as alias for backward compatibility during transition
ArmedTopic = ArmedDeck


@dataclass
class SessionSnapshot:
    """Immutable snapshot of session state for UI consumption."""

    state: SessionState
    armed_deck: ArmedDeck | None = None
    obs_connected: bool = False
    last_output: Path | None = None
    error: str | None = None

    @property
    def armed_topic(self) -> ArmedDeck | None:
        """Deprecated alias for :attr:`armed_deck`."""
        return self.armed_deck


class RecordingSession:
    """State machine managing the recording → rename workflow.

    Args:
        obs: Connected (or soon-to-be-connected) OBS client.
        recordings_root: Root directory containing ``to-process/``,
            ``final/``, ``archive/`` subdirectories.
        raw_suffix: Suffix for raw recording filenames (default ``--RAW``).
        stability_interval: Seconds between file-size polls when waiting
            for the recording file to stabilise after OBS stops.
        stability_checks: Number of consecutive identical size readings
            required before considering the file stable.
        on_state_change: Optional callback invoked (outside the lock)
            after every state transition.  Receives a :class:`SessionSnapshot`.
    """

    def __init__(
        self,
        obs: ObsClient,
        recordings_root: Path,
        *,
        raw_suffix: str = DEFAULT_RAW_SUFFIX,
        stability_interval: float = 1.0,
        stability_checks: int = 3,
        on_state_change: Callable[[SessionSnapshot], None] | None = None,
    ) -> None:
        self._obs = obs
        self._root = recordings_root
        self._raw_suffix = raw_suffix
        self._stability_interval = stability_interval
        self._stability_checks = stability_checks
        self._on_state_change = on_state_change

        self._state = SessionState.IDLE
        self._armed: ArmedDeck | None = None
        self._last_output: Path | None = None
        self._error: str | None = None
        self._lock = threading.Lock()

        # Wire up OBS events
        self._obs.on_record_state_changed(self._handle_record_event)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def armed_topic(self) -> ArmedDeck | None:
        """Deprecated alias for :attr:`armed_deck`."""
        return self._armed

    @property
    def armed_deck(self) -> ArmedDeck | None:
        return self._armed

    def snapshot(self) -> SessionSnapshot:
        """Thread-safe snapshot of the current session state."""
        with self._lock:
            return SessionSnapshot(
                state=self._state,
                armed_deck=self._armed,
                obs_connected=self._obs.connected,
                last_output=self._last_output,
                error=self._error,
            )

    def arm(
        self,
        course_slug: str,
        section_name: str,
        deck_name: str,
        *,
        part_number: int = 0,
    ) -> None:
        """Arm a slide deck for the next recording.

        Can be called from ``IDLE`` or ``ARMED`` (to switch decks).

        Raises:
            RuntimeError: If a recording or rename is in progress.
        """
        with self._lock:
            if self._state not in (SessionState.IDLE, SessionState.ARMED):
                raise RuntimeError(
                    f"Cannot arm while in state '{self._state.value}'. "
                    "Wait for the current recording to finish."
                )
            self._armed = ArmedDeck(course_slug, section_name, deck_name, part_number)
            self._error = None
            self._state = SessionState.ARMED

        self._notify()

    def disarm(self) -> None:
        """Disarm the currently armed topic, returning to ``IDLE``.

        Raises:
            RuntimeError: If a recording is in progress.
        """
        with self._lock:
            if self._state == SessionState.RECORDING:
                raise RuntimeError("Cannot disarm while recording is in progress.")
            if self._state == SessionState.RENAMING:
                raise RuntimeError("Cannot disarm while rename is in progress.")
            self._armed = None
            self._state = SessionState.IDLE

        self._notify()

    # ------------------------------------------------------------------
    # OBS event handling
    # ------------------------------------------------------------------

    def _handle_record_event(self, event: RecordingEvent) -> None:
        """Respond to an OBS ``RecordStateChanged`` event.

        Called on the obsws-python daemon thread.

        OBS emits *two* events when recording stops:

        1. ``OBS_WEBSOCKET_OUTPUT_STOPPING`` — ``output_active=False``,
           **no** ``output_path`` yet (OBS is still flushing the file).
        2. ``OBS_WEBSOCKET_OUTPUT_STOPPED`` — ``output_active=False``,
           ``output_path`` is set to the final file location.

        We must ignore the intermediate STOPPING event and only act on
        the definitive STOPPED event, otherwise the session transitions
        to ``IDLE`` before the output path is available.
        """
        rename_args: tuple[Path, ArmedDeck] | None = None

        with self._lock:
            if event.output_active:
                # Recording started
                if self._state == SessionState.ARMED:
                    self._state = SessionState.RECORDING
                    logger.info("Recording started for {}", self._armed)
                else:
                    logger.info("Recording started (state={}, no auto-rename)", self._state.value)
            else:
                # Intermediate transition — OBS hasn't finished writing the
                # file yet.  The output_path is only present in the final
                # OBS_WEBSOCKET_OUTPUT_STOPPED event.
                if event.output_state == "OBS_WEBSOCKET_OUTPUT_STOPPING":
                    logger.debug("Intermediate STOPPING event, waiting for STOPPED")
                    return

                # Recording stopped (definitive STOPPED event)
                if self._state == SessionState.RECORDING and self._armed is not None:
                    if event.output_path:
                        self._state = SessionState.RENAMING
                        rename_args = (Path(event.output_path), self._armed)
                    else:
                        logger.warning("Recording stopped but no output path reported")
                        self._error = "OBS did not report the output file path"
                        self._armed = None
                        self._state = SessionState.IDLE
                elif self._state == SessionState.RECORDING:
                    # Was recording but nothing armed — just go back to idle
                    self._state = SessionState.IDLE
                else:
                    logger.debug("Recording stopped event ignored (state={})", self._state.value)

        self._notify()

        if rename_args:
            threading.Thread(
                target=self._rename_recording,
                args=rename_args,
                daemon=True,
                name="recording-rename",
            ).start()

    # ------------------------------------------------------------------
    # File rename (runs on a background thread)
    # ------------------------------------------------------------------

    def _rename_recording(self, obs_output: Path, deck: ArmedDeck) -> None:
        """Move the OBS output file into the structured ``to-process/`` tree."""
        try:
            self._wait_for_stable(obs_output)

            rel_dir = recording_relative_dir(deck.course_slug, deck.section_name)
            target_name = raw_filename(
                deck.deck_name,
                ext=obs_output.suffix,
                raw_suffix=self._raw_suffix,
                part=deck.part_number,
            )
            target_dir = to_process_dir(self._root) / str(rel_dir)
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / target_name

            shutil.move(str(obs_output), str(target))
            logger.info("Renamed {} → {}", obs_output.name, target)

            with self._lock:
                self._last_output = target
                self._armed = None
                self._state = SessionState.IDLE

        except Exception as exc:
            logger.error("Failed to rename recording: {}", exc)
            with self._lock:
                self._error = str(exc)
                self._armed = None
                self._state = SessionState.IDLE

        self._notify()

    def _wait_for_stable(self, path: Path) -> None:
        """Poll file size until it stops changing.

        Raises:
            FileNotFoundError: If the file does not exist.
        """
        prev_size = -1
        stable_count = 0

        while stable_count < self._stability_checks:
            if not path.exists():
                raise FileNotFoundError(f"Recording file not found: {path}")

            size = path.stat().st_size
            if size == prev_size and size > 0:
                stable_count += 1
            else:
                stable_count = 0
            prev_size = size

            if stable_count < self._stability_checks:
                time.sleep(self._stability_interval)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _notify(self) -> None:
        """Emit state-change callback outside the lock."""
        if self._on_state_change:
            try:
                self._on_state_change(self.snapshot())
            except Exception:
                logger.exception("Error in state change callback")
