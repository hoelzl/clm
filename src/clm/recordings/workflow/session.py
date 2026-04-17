"""Recording session state machine.

Coordinates the recording workflow by tracking which slide deck is "armed"
for recording and responding to OBS events to rename the output file
into the structured directory layout.

State transitions::

    idle ──arm()──► armed ──OBS starts──► recording ──OBS stops──► renaming ──done──┐
      ▲               │                      ▲                                       │
      │               │                      │                                       ▼
      │               │                      └── OBS starts ── armed_after_take ◄────┤
      │               │                                             │                │
      │               │                                             ▼                │
      └──disarm()─────┴────────────────── timer expires ────────────┴────────────────┘

A "short take" (OBS stops within ``short_take_seconds`` of starting) is
treated as an accidental start-then-stop: the output goes to
``superseded/`` and the session returns to ``ARMED`` with the same deck
intact, so the user can start again without re-arming.

A "retake" starts when OBS begins recording again inside the
``retake_window_seconds`` after a normal take completes. The new
recording is associated with the same armed deck; the old raw is
superseded via the existing ``_prepare_target_slot`` cascade.

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

from .directories import final_dir, superseded_dir, to_process_dir
from .naming import (
    DEFAULT_RAW_SUFFIX,
    find_existing_recordings,
    raw_filename,
    recording_relative_dir,
)
from .obs import ObsClient, RecordingEvent


class SessionState(enum.Enum):
    """Recording session states."""

    IDLE = "idle"
    ARMED = "armed"
    RECORDING = "recording"
    RENAMING = "renaming"
    ARMED_AFTER_TAKE = "armed_after_take"
    """A take has completed and the deck remains armed for a short window.

    A new OBS recording that begins before the window expires is
    treated as a retake of the same deck. The window expiring
    transitions the session to :attr:`IDLE` and clears the armed deck.
    """


@dataclass(frozen=True)
class ArmedDeck:
    """Identifies the slide deck armed for the next recording."""

    course_slug: str
    section_name: str
    deck_name: str
    part_number: int = 0
    lang: str = "en"


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


def _prepare_target_slot(
    target_dir: Path,
    deck_name: str,
    ext: str,
    part: int,
    raw_suffix: str,
    recordings_root: Path,
    lang: str = "en",
) -> Path:
    """Ensure the target slot is clear and handle dynamic part renaming.

    Implements these rules:

    - If the user records part N>0 and an unsuffixed (part 0) file exists,
      rename the existing file to ``(part 1)`` (including companion ``.wav``
      and any matching file in ``final/``).
    - If the computed target already exists, supersede it.
    - Single recording = no suffix; multiple parts = all get ``(part N)``.

    Returns the final target :class:`Path` for the new recording.
    """
    existing = find_existing_recordings(target_dir, deck_name, raw_suffix)

    # If user is adding a new part (part>0) and an unsuffixed file exists,
    # rename the unsuffixed file to (part 1)
    if part > 0 and 0 in existing:
        unsuffixed = existing[0]
        part1_name = raw_filename(
            deck_name, ext=unsuffixed.suffix, raw_suffix=raw_suffix, part=1, lang=lang
        )
        part1_target = target_dir / part1_name
        if not part1_target.exists():
            shutil.move(str(unsuffixed), str(part1_target))
            logger.info("Renamed {} → {} (multi-part cascade)", unsuffixed.name, part1_target.name)

            # Also rename companion .wav
            wav = unsuffixed.with_suffix(".wav")
            if wav.exists():
                wav_target = target_dir / raw_filename(
                    deck_name, ext=".wav", raw_suffix=raw_suffix, part=1, lang=lang
                )
                shutil.move(str(wav), str(wav_target))
                logger.info("Renamed {} → {} (companion)", wav.name, wav_target.name)

            # Also rename in final/
            _rename_final_to_part1(deck_name, unsuffixed.suffix, recordings_root, target_dir, lang)

    target_name = raw_filename(deck_name, ext=ext, raw_suffix=raw_suffix, part=part, lang=lang)
    target = target_dir / target_name

    if target.exists():
        _supersede_file(target, recordings_root)

    return target


def _rename_final_to_part1(
    deck_name: str, ext: str, recordings_root: Path, target_dir: Path, lang: str = "en"
) -> None:
    """Rename unsuffixed final output to ``(part 1)`` when a multi-part cascade happens."""
    tp = to_process_dir(recordings_root)
    try:
        rel = target_dir.relative_to(tp)
    except ValueError:
        return

    fd = final_dir(recordings_root) / str(rel)
    if not fd.is_dir():
        return

    from .naming import final_filename, parse_part

    # Look for unsuffixed final file (any video extension)
    for child in fd.iterdir():
        if not child.is_file():
            continue
        base, part_num = parse_part(child.stem)
        from clm.core.utils.text_utils import sanitize_file_name

        if base == sanitize_file_name(deck_name) and part_num == 0:
            new_name = final_filename(deck_name, ext=child.suffix, part=1, lang=lang)
            new_path = fd / new_name
            if not new_path.exists():
                shutil.move(str(child), str(new_path))
                logger.info("Renamed final {} → {}", child.name, new_path)
            break


def _move_to_superseded_dir(src: Path, dest_dir: Path) -> Path:
    """Move *src* into *dest_dir*, appending ``(2)``, ``(3)``, … on collision.

    Creates *dest_dir* if needed. Returns the final resolved destination
    path. Shared by :func:`_supersede_file` (replacing a processed take
    that's being re-recorded) and :meth:`RecordingSession._handle_short_take`
    (moving an accidental zero-length take out of OBS's default dir).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if dest.exists():
        stem = src.stem
        ext = src.suffix
        counter = 2
        while dest.exists():
            dest = dest_dir / f"{stem} ({counter}){ext}"
            counter += 1
    shutil.move(str(src), str(dest))
    logger.info("Superseded {} → {}", src.name, dest)
    return dest


def _supersede_file(existing: Path, recordings_root: Path) -> None:
    """Move *existing* (and any companion ``.wav``) into ``superseded/``.

    Preserves the directory structure relative to ``to-process/``.
    If a file with the same name already exists in the superseded
    directory, appends ``(2)``, ``(3)``, etc. before the extension.
    """
    tp = to_process_dir(recordings_root)
    try:
        rel = existing.parent.relative_to(tp)
    except ValueError:
        rel = Path(".")

    dest_dir = superseded_dir(recordings_root) / str(rel)
    _move_to_superseded_dir(existing, dest_dir)

    # Also move companion .wav if present
    companion = existing.with_suffix(".wav")
    if companion.exists():
        _move_to_superseded_dir(companion, dest_dir)


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
        short_take_seconds: A recording that stops within this many seconds
            of starting is treated as an accidental short take: its output
            is moved to ``superseded/`` and the session remains armed.
            Default 5 seconds.
        retake_window_seconds: After a successful take, the deck stays
            armed for this many seconds. A new OBS recording that begins
            within the window is associated with the same armed deck as
            a retake. When the window expires, the session transitions
            to :attr:`SessionState.IDLE` and clears the armed deck.
            Default 60 seconds.
        rename_timeout_seconds: Total wall-clock budget for
            :meth:`_wait_for_stable` to wait for OBS to finish writing
            the file. A wedged encoder won't freeze the session forever.
            Default 600 seconds (10 minutes).
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
        short_take_seconds: float = 5.0,
        retake_window_seconds: float = 60.0,
        rename_timeout_seconds: float = 600.0,
        on_state_change: Callable[[SessionSnapshot], None] | None = None,
    ) -> None:
        self._obs = obs
        self._root = recordings_root
        self._raw_suffix = raw_suffix
        self._stability_interval = stability_interval
        self._stability_checks = stability_checks
        self._short_take_seconds = short_take_seconds
        self._retake_window_seconds = retake_window_seconds
        self._rename_timeout_seconds = rename_timeout_seconds
        self._on_state_change = on_state_change

        self._state = SessionState.IDLE
        self._armed: ArmedDeck | None = None
        self._last_output: Path | None = None
        self._error: str | None = None
        self._lock = threading.Lock()

        # Retake machinery (guarded by the session lock).
        self._recording_started_at: float | None = None
        self._retake_timer: threading.Timer | None = None

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
        lang: str = "en",
    ) -> None:
        """Arm a slide deck for the next recording.

        Can be called from ``IDLE``, ``ARMED``, or ``ARMED_AFTER_TAKE``
        (to switch decks mid-retake-window). Calling from
        ``ARMED_AFTER_TAKE`` cancels the retake timer — the window is
        specific to the deck that just finished, and switching decks
        means the user is moving on.

        Raises:
            RuntimeError: If a recording or rename is in progress.
        """
        with self._lock:
            if self._state not in (
                SessionState.IDLE,
                SessionState.ARMED,
                SessionState.ARMED_AFTER_TAKE,
            ):
                raise RuntimeError(
                    f"Cannot arm while in state '{self._state.value}'. "
                    "Wait for the current recording to finish."
                )
            self._cancel_retake_timer_locked()
            self._armed = ArmedDeck(course_slug, section_name, deck_name, part_number, lang)
            self._error = None
            self._state = SessionState.ARMED

        self._notify()

    def disarm(self) -> None:
        """Disarm the currently armed topic, returning to ``IDLE``.

        Can be called from ``ARMED`` or ``ARMED_AFTER_TAKE`` (the latter
        cancels the retake window early).

        Raises:
            RuntimeError: If a recording is in progress.
        """
        with self._lock:
            if self._state == SessionState.RECORDING:
                raise RuntimeError("Cannot disarm while recording is in progress.")
            if self._state == SessionState.RENAMING:
                raise RuntimeError("Cannot disarm while rename is in progress.")
            self._cancel_retake_timer_locked()
            self._armed = None
            self._state = SessionState.IDLE

        self._notify()

    def record(
        self,
        course_slug: str,
        section_name: str,
        deck_name: str,
        *,
        part_number: int = 0,
        lang: str = "en",
    ) -> None:
        """Arm a deck and start OBS recording in one operation.

        The two steps are deliberately *not* atomic: ``arm`` succeeds
        under the session lock, then ``obs.start_record`` is invoked
        outside the lock (OBS I/O must not block other callers).

        If OBS rejects the start request, the deck is left **armed** on
        purpose: the user can switch to OBS and start recording manually,
        or retry the Record button once OBS is reachable. The caller is
        responsible for surfacing the error in the UI.

        Raises:
            RuntimeError: If ``arm`` fails (already recording or mid-rename).
            ConnectionError: If OBS rejects the start request. The deck
                remains armed — callers should present a recoverable error.
        """
        self.arm(course_slug, section_name, deck_name, part_number=part_number, lang=lang)
        self._obs.start_record()

    def stop(self) -> None:
        """Ask OBS to stop the current recording.

        Pure convenience so the dashboard can offer a Stop button without
        leaving the web UI. The rest of the stop flow (STOPPED event →
        rename → transition to IDLE) is handled by the existing event
        pipeline.

        Raises:
            ConnectionError: If OBS is not connected or rejects the request.
        """
        self._obs.stop_record()

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

        OBS START during ``ARMED_AFTER_TAKE`` is treated as a retake of
        the same armed deck. OBS STOP within ``short_take_seconds`` of
        the start is treated as an accidental take: the file is moved
        to ``superseded/`` and the deck stays armed.
        """
        rename_args: tuple[Path, ArmedDeck] | None = None
        short_take_args: tuple[Path, ArmedDeck] | None = None

        with self._lock:
            if event.output_active:
                # Recording started (STARTED)
                if self._state in (SessionState.ARMED, SessionState.ARMED_AFTER_TAKE):
                    if self._state == SessionState.ARMED_AFTER_TAKE:
                        logger.info("Retake detected for {} (within window)", self._armed)
                    self._cancel_retake_timer_locked()
                    self._state = SessionState.RECORDING
                    self._recording_started_at = time.monotonic()
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
                    elapsed = self._elapsed_since_start_locked()
                    is_short_take = elapsed is not None and elapsed < self._short_take_seconds
                    self._recording_started_at = None

                    if is_short_take and event.output_path:
                        # Accidental start-then-stop — move to superseded/
                        # and stay armed on the same deck.
                        logger.info(
                            "Short take ({:.1f}s < {:.1f}s) — superseding and staying armed",
                            elapsed,
                            self._short_take_seconds,
                        )
                        self._state = SessionState.ARMED
                        short_take_args = (Path(event.output_path), self._armed)
                    elif event.output_path:
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
                    self._recording_started_at = None
                else:
                    logger.debug("Recording stopped event ignored (state={})", self._state.value)

        self._notify()

        if short_take_args:
            threading.Thread(
                target=self._handle_short_take,
                args=short_take_args,
                daemon=True,
                name="recording-short-take",
            ).start()
        elif rename_args:
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
        """Move the OBS output file into the structured ``to-process/`` tree.

        On success, transitions the session to :attr:`ARMED_AFTER_TAKE`
        and starts a timer that will disarm the deck if no retake
        arrives within ``retake_window_seconds``.
        """
        try:
            self._wait_for_stable(obs_output)

            rel_dir = recording_relative_dir(deck.course_slug, deck.section_name)
            target_dir = to_process_dir(self._root) / str(rel_dir)
            target_dir.mkdir(parents=True, exist_ok=True)

            target = _prepare_target_slot(
                target_dir,
                deck.deck_name,
                obs_output.suffix,
                deck.part_number,
                self._raw_suffix,
                self._root,
                lang=deck.lang,
            )

            shutil.move(str(obs_output), str(target))
            logger.info("Renamed {} → {}", obs_output.name, target)

            with self._lock:
                self._last_output = target
                # Keep _armed so a retake within the window can re-use it.
                self._state = SessionState.ARMED_AFTER_TAKE
                self._start_retake_timer_locked()

        except Exception as exc:
            logger.error("Failed to rename recording: {}", exc)
            with self._lock:
                self._error = str(exc)
                self._armed = None
                self._state = SessionState.IDLE

        self._notify()

    def _handle_short_take(self, obs_output: Path, deck: ArmedDeck) -> None:
        """Move an accidental short take to ``superseded/`` and log it.

        The session has already transitioned back to :attr:`ARMED` under
        the event-handler lock; this thread exists only to do the
        filesystem I/O without blocking the OBS callback thread.
        """
        try:
            self._wait_for_stable(obs_output)
        except Exception as exc:
            logger.warning(
                "Short-take file {} did not stabilise: {}. Leaving in place.",
                obs_output,
                exc,
            )
            return

        rel_dir = recording_relative_dir(deck.course_slug, deck.section_name)
        dest_dir = superseded_dir(self._root) / str(rel_dir)
        try:
            dest = _move_to_superseded_dir(obs_output, dest_dir)
            logger.info("Short take moved to {}", dest)
        except Exception as exc:
            logger.warning("Failed to move short-take {} to superseded/: {}", obs_output, exc)

    def _wait_for_stable(self, path: Path) -> None:
        """Poll file size until it stops changing.

        Bounded by :attr:`_rename_timeout_seconds` so a wedged encoder
        cannot freeze the session forever.

        Raises:
            FileNotFoundError: If the file does not exist.
            TimeoutError: If the file has not stabilised within the timeout.
        """
        prev_size = -1
        stable_count = 0
        deadline = time.monotonic() + self._rename_timeout_seconds

        while stable_count < self._stability_checks:
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"File {path} did not stabilise within {self._rename_timeout_seconds:.0f}s"
                )
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

    def _elapsed_since_start_locked(self) -> float | None:
        """Seconds since the most recent OBS STARTED event, or None if unknown.

        The caller must hold :attr:`_lock`.
        """
        if self._recording_started_at is None:
            return None
        return time.monotonic() - self._recording_started_at

    def _cancel_retake_timer_locked(self) -> None:
        """Cancel the retake-window timer if one is running.

        The caller must hold :attr:`_lock`. Safe to call when no timer
        is active.
        """
        timer = self._retake_timer
        self._retake_timer = None
        if timer is not None:
            timer.cancel()

    def _start_retake_timer_locked(self) -> None:
        """Arm a timer that will fire :meth:`_on_retake_window_expired`.

        The caller must hold :attr:`_lock`. Cancels any prior timer
        first so there is only ever one pending.
        """
        self._cancel_retake_timer_locked()
        timer = threading.Timer(self._retake_window_seconds, self._on_retake_window_expired)
        timer.daemon = True
        timer.name = "recording-retake-window"
        self._retake_timer = timer
        timer.start()

    def _on_retake_window_expired(self) -> None:
        """Fire when the retake window elapses without a new OBS recording.

        Transitions ``ARMED_AFTER_TAKE`` → ``IDLE``. Silently does
        nothing if the session has already moved on (e.g. the user
        disarmed, armed a different deck, or a retake arrived just
        before the timer fired).
        """
        with self._lock:
            if self._state != SessionState.ARMED_AFTER_TAKE:
                return
            logger.info("Retake window expired; disarming {}", self._armed)
            self._armed = None
            self._state = SessionState.IDLE
            self._retake_timer = None

        self._notify()
