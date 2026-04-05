"""Filesystem watcher for the recording workflow.

Monitors ``to-process/`` for new files and triggers assembly
automatically.  Behaviour depends on the active processing backend:

- **external** — watches for ``.wav`` files (produced by an external
  tool such as iZotope RX 11).  When a ``.wav`` is stable and a
  matching raw video exists, assembly is triggered.

- **onnx** — watches for raw video files (``--RAW.{ext}``).  When a
  video is stable, the ONNX backend processes it to produce a ``.wav``,
  then assembly is triggered.

The watcher runs on a background thread via ``watchdog``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

from loguru import logger
from watchdog.events import FileCreatedEvent, FileMovedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from clm.recordings.processing.batch import VIDEO_EXTENSIONS
from clm.recordings.workflow.assembler import AssemblyResult, assemble_one
from clm.recordings.workflow.directories import (
    PendingPair,
    archive_dir,
    final_dir,
    to_process_dir,
)
from clm.recordings.workflow.naming import DEFAULT_RAW_SUFFIX, parse_raw_stem

from .backends_legacy import OnnxBackend, ProcessingBackend


class WatcherState:
    """Thread-safe container for watcher runtime state."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._processing: set[Path] = set()

    def try_claim(self, path: Path) -> bool:
        """Claim a file for processing.  Returns False if already claimed."""
        with self._lock:
            if path in self._processing:
                return False
            self._processing.add(path)
            return True

    def release(self, path: Path) -> None:
        with self._lock:
            self._processing.discard(path)


class RecordingsWatcher:
    """Filesystem watcher that monitors ``to-process/`` and triggers assembly.

    Args:
        root_dir: Recordings root (``to-process/``, ``final/``, ``archive/``).
        backend: ``"external"`` or ``"onnx"`` (or a :class:`ProcessingBackend`
            instance for the onnx path).
        raw_suffix: Suffix identifying raw files (default ``--RAW``).
        stability_interval: Seconds between file-size polls.
        stability_checks: Consecutive identical readings = stable.
        on_assembled: Callback after a successful assembly.
        on_processing: Callback when processing starts for a file.
        on_error: Callback on processing/assembly error.
    """

    def __init__(
        self,
        root_dir: Path,
        *,
        backend: str | ProcessingBackend = "external",
        raw_suffix: str = DEFAULT_RAW_SUFFIX,
        stability_interval: float = 2.0,
        stability_checks: int = 3,
        on_assembled: Callable[[AssemblyResult], None] | None = None,
        on_processing: Callable[[Path], None] | None = None,
        on_error: Callable[[Path, str], None] | None = None,
    ) -> None:
        self._root = root_dir
        self._raw_suffix = raw_suffix
        self._stability_interval = stability_interval
        self._stability_checks = stability_checks
        self._on_assembled = on_assembled
        self._on_processing = on_processing
        self._on_error = on_error

        # Resolve backend
        if isinstance(backend, str):
            if backend == "onnx":
                self._backend: ProcessingBackend | None = OnnxBackend()
                self._mode = "onnx"
            else:
                self._backend = None
                self._mode = "external"
        else:
            self._backend = backend
            self._mode = "onnx"

        self._observer: Observer | None = None  # type: ignore[valid-type]
        self._state = WatcherState()

    @property
    def running(self) -> bool:
        return self._observer is not None and self._observer.is_alive()

    @property
    def mode(self) -> str:
        return self._mode

    def start(self) -> None:
        """Start watching ``to-process/`` for new files."""
        if self.running:
            return

        watch_dir = to_process_dir(self._root)
        watch_dir.mkdir(parents=True, exist_ok=True)

        handler = _WatchHandler(self)
        self._observer = Observer()
        self._observer.schedule(handler, str(watch_dir), recursive=True)
        self._observer.daemon = True
        self._observer.start()
        logger.info(
            "Watcher started (mode={}, dir={})",
            self._mode,
            watch_dir,
        )

    def stop(self) -> None:
        """Stop the watcher."""
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5.0)
            self._observer = None
            logger.info("Watcher stopped")

    # ------------------------------------------------------------------
    # Called by the event handler (on background threads)
    # ------------------------------------------------------------------

    def _on_file_event(self, path: Path) -> None:
        """Respond to a new or moved file in ``to-process/``."""
        if self._mode == "external":
            self._handle_external(path)
        else:
            self._handle_onnx(path)

    def _handle_external(self, path: Path) -> None:
        """External mode: react to new ``.wav`` files."""
        if path.suffix.lower() != ".wav":
            return

        # Must be a raw-suffixed wav
        _, is_raw = parse_raw_stem(path.stem, self._raw_suffix)
        if not is_raw:
            return

        if not self._state.try_claim(path):
            return

        threading.Thread(
            target=self._process_external_wav,
            args=(path,),
            daemon=True,
            name=f"watcher-ext-{path.stem}",
        ).start()

    def _handle_onnx(self, path: Path) -> None:
        """ONNX mode: react to new raw video files."""
        if path.suffix.lower() not in VIDEO_EXTENSIONS:
            return

        _, is_raw = parse_raw_stem(path.stem, self._raw_suffix)
        if not is_raw:
            return

        if not self._state.try_claim(path):
            return

        threading.Thread(
            target=self._process_onnx_video,
            args=(path,),
            daemon=True,
            name=f"watcher-onnx-{path.stem}",
        ).start()

    # ------------------------------------------------------------------
    # Background processing threads
    # ------------------------------------------------------------------

    def _process_external_wav(self, wav_path: Path) -> None:
        """Wait for the .wav to stabilise, find matching video, assemble."""
        try:
            self._wait_for_stable(wav_path)

            # Find matching raw video
            video = self._find_matching_video(wav_path)
            if video is None:
                logger.debug("No matching video for {}, skipping", wav_path.name)
                return

            self._assemble_pair(video, wav_path)

        except Exception as exc:
            logger.error("Watcher error (external) for {}: {}", wav_path.name, exc)
            if self._on_error:
                self._on_error(wav_path, str(exc))
        finally:
            self._state.release(wav_path)

    def _process_onnx_video(self, video_path: Path) -> None:
        """Wait for video to stabilise, run ONNX, assemble."""
        try:
            self._wait_for_stable(video_path)

            if self._on_processing:
                self._on_processing(video_path)

            # Produce the .wav alongside the video
            wav_path = video_path.with_name(f"{video_path.stem}.wav")

            assert self._backend is not None
            self._backend.process(video_path, wav_path)
            logger.info("ONNX processing complete: {}", wav_path.name)

            self._assemble_pair(video_path, wav_path)

        except Exception as exc:
            logger.error("Watcher error (onnx) for {}: {}", video_path.name, exc)
            if self._on_error:
                self._on_error(video_path, str(exc))
        finally:
            self._state.release(video_path)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_matching_video(self, wav_path: Path) -> Path | None:
        """Find a raw video file matching the given .wav file."""
        stem = wav_path.stem  # e.g. "topic--RAW"
        for ext in VIDEO_EXTENSIONS:
            candidate = wav_path.with_name(f"{stem}{ext}")
            if candidate.is_file():
                return candidate
        return None

    def _assemble_pair(self, video: Path, audio: Path) -> None:
        """Build a PendingPair and run assembly."""
        tp = to_process_dir(self._root)
        fl = final_dir(self._root)
        ar = archive_dir(self._root)

        relative_dir = video.parent.relative_to(tp)
        pair = PendingPair(
            video=video,
            audio=audio,
            relative_dir=relative_dir,
            raw_suffix=self._raw_suffix,
        )

        result = assemble_one(pair, fl, ar)

        if self._on_assembled:
            self._on_assembled(result)

        if result.success:
            logger.info("Watcher assembled: {}", result.output_file)
        else:
            logger.error("Watcher assembly failed for {}: {}", video.name, result.error)
            if self._on_error:
                self._on_error(video, result.error or "Assembly failed")

    def _wait_for_stable(self, path: Path) -> None:
        """Poll file size until it stops changing.

        Raises:
            FileNotFoundError: If the file disappears during polling.
        """
        prev_size = -1
        stable_count = 0

        while stable_count < self._stability_checks:
            if not path.exists():
                raise FileNotFoundError(f"File disappeared during stability check: {path}")

            size = path.stat().st_size
            if size == prev_size and size > 0:
                stable_count += 1
            else:
                stable_count = 0
            prev_size = size

            if stable_count < self._stability_checks:
                time.sleep(self._stability_interval)


class _WatchHandler(FileSystemEventHandler):
    """Watchdog event handler that delegates to :class:`RecordingsWatcher`."""

    def __init__(self, watcher: RecordingsWatcher) -> None:
        super().__init__()
        self._watcher = watcher

    def on_created(self, event: FileCreatedEvent) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        self._watcher._on_file_event(Path(str(event.src_path)))

    def on_moved(self, event: FileMovedEvent) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        # Treat the destination as a new file
        self._watcher._on_file_event(Path(str(event.dest_path)))
