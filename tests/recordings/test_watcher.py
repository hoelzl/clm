"""Tests for the recordings filesystem watcher."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from clm.recordings.workflow.directories import ensure_root
from clm.recordings.workflow.watcher import RecordingsWatcher, WatcherState, _WatchHandler

# ------------------------------------------------------------------
# WatcherState
# ------------------------------------------------------------------


class TestWatcherState:
    def test_try_claim_returns_true_first_time(self):
        state = WatcherState()
        assert state.try_claim(Path("/a.wav")) is True

    def test_try_claim_returns_false_if_already_claimed(self):
        state = WatcherState()
        state.try_claim(Path("/a.wav"))
        assert state.try_claim(Path("/a.wav")) is False

    def test_release_allows_reclaim(self):
        state = WatcherState()
        p = Path("/a.wav")
        state.try_claim(p)
        state.release(p)
        assert state.try_claim(p) is True

    def test_release_unclaimed_path_is_safe(self):
        state = WatcherState()
        state.release(Path("/nonexistent"))  # should not raise

    def test_concurrent_claims(self):
        """Two threads racing to claim the same path — only one wins."""
        state = WatcherState()
        p = Path("/race.wav")
        results: list[bool] = []
        barrier = threading.Barrier(2)

        def claim():
            barrier.wait()
            results.append(state.try_claim(p))

        t1 = threading.Thread(target=claim)
        t2 = threading.Thread(target=claim)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert sorted(results) == [False, True]


# ------------------------------------------------------------------
# RecordingsWatcher — construction
# ------------------------------------------------------------------


class TestWatcherInit:
    def test_default_mode_is_external(self, tmp_path: Path):
        watcher = RecordingsWatcher(tmp_path)
        assert watcher.mode == "external"
        assert watcher._backend is None

    def test_onnx_mode_from_string(self, tmp_path: Path):
        watcher = RecordingsWatcher(tmp_path, backend="onnx")
        assert watcher.mode == "onnx"
        assert watcher._backend is not None

    def test_onnx_mode_from_backend_instance(self, tmp_path: Path):
        from clm.recordings.workflow.backends_legacy import OnnxBackend

        backend = OnnxBackend()
        watcher = RecordingsWatcher(tmp_path, backend=backend)
        assert watcher.mode == "onnx"
        assert watcher._backend is backend

    def test_not_running_initially(self, tmp_path: Path):
        watcher = RecordingsWatcher(tmp_path)
        assert watcher.running is False


# ------------------------------------------------------------------
# RecordingsWatcher — start / stop
# ------------------------------------------------------------------


class TestWatcherStartStop:
    def test_start_creates_to_process_dir(self, tmp_path: Path):
        watcher = RecordingsWatcher(tmp_path)
        watcher.start()
        try:
            assert (tmp_path / "to-process").is_dir()
            assert watcher.running is True
        finally:
            watcher.stop()

    def test_stop_marks_not_running(self, tmp_path: Path):
        watcher = RecordingsWatcher(tmp_path)
        watcher.start()
        watcher.stop()
        assert watcher.running is False

    def test_start_is_idempotent(self, tmp_path: Path):
        watcher = RecordingsWatcher(tmp_path)
        watcher.start()
        observer1 = watcher._observer
        watcher.start()  # should not create a second observer
        assert watcher._observer is observer1
        watcher.stop()

    def test_stop_when_not_started_is_safe(self, tmp_path: Path):
        watcher = RecordingsWatcher(tmp_path)
        watcher.stop()  # should not raise


# ------------------------------------------------------------------
# RecordingsWatcher — stability detection
# ------------------------------------------------------------------


class TestStabilityDetection:
    def test_stable_file_passes(self, tmp_path: Path):
        f = tmp_path / "test.wav"
        f.write_bytes(b"data")

        watcher = RecordingsWatcher(tmp_path, stability_interval=0.01, stability_checks=2)
        watcher._wait_for_stable(f)  # should not raise

    def test_missing_file_raises(self, tmp_path: Path):
        watcher = RecordingsWatcher(tmp_path, stability_interval=0.01, stability_checks=2)
        with pytest.raises(FileNotFoundError, match="disappeared"):
            watcher._wait_for_stable(tmp_path / "missing.wav")

    def test_empty_file_waits(self, tmp_path: Path):
        """Empty files (size 0) are never considered stable."""
        f = tmp_path / "empty.wav"
        f.write_bytes(b"")

        watcher = RecordingsWatcher(tmp_path, stability_interval=0.01, stability_checks=2)

        # Write data after a tiny delay to make the file non-empty
        def grow():
            time.sleep(0.05)
            f.write_bytes(b"real data")

        t = threading.Thread(target=grow, daemon=True)
        t.start()
        watcher._wait_for_stable(f)
        t.join()
        assert f.stat().st_size > 0


# ------------------------------------------------------------------
# RecordingsWatcher — external mode event handling
# ------------------------------------------------------------------


class TestExternalModeEvents:
    def _make_root(self, tmp_path: Path) -> Path:
        root = tmp_path / "recordings"
        ensure_root(root)
        return root

    def test_ignores_non_wav_files(self, tmp_path: Path):
        root = self._make_root(tmp_path)
        watcher = RecordingsWatcher(root, stability_interval=0.01, stability_checks=1)

        # A .mp4 file should be ignored in external mode
        mp4 = root / "to-process" / "topic--RAW.mp4"
        mp4.write_bytes(b"video data")

        watcher._on_file_event(mp4)
        # No error, no processing — just ignored

    def test_ignores_non_raw_wav(self, tmp_path: Path):
        root = self._make_root(tmp_path)
        watcher = RecordingsWatcher(root, stability_interval=0.01, stability_checks=1)

        # A wav without --RAW suffix should be ignored
        wav = root / "to-process" / "topic.wav"
        wav.write_bytes(b"audio data")

        watcher._on_file_event(wav)

    @patch("clm.recordings.workflow.watcher.assemble_one")
    def test_assembles_when_video_exists(self, mock_assemble: MagicMock, tmp_path: Path):
        root = self._make_root(tmp_path)
        tp = root / "to-process"

        # Create matching pair
        video = tp / "topic--RAW.mp4"
        audio = tp / "topic--RAW.wav"
        video.write_bytes(b"video")
        audio.write_bytes(b"audio")

        mock_assemble.return_value = MagicMock(success=True, output_file=Path("out.mp4"))
        on_assembled = MagicMock()

        watcher = RecordingsWatcher(
            root,
            stability_interval=0.01,
            stability_checks=1,
            on_assembled=on_assembled,
        )
        # Directly call the handler synchronously
        watcher._process_external_wav(audio)

        mock_assemble.assert_called_once()
        on_assembled.assert_called_once()

    @patch("clm.recordings.workflow.watcher.assemble_one")
    def test_skips_when_no_matching_video(self, mock_assemble: MagicMock, tmp_path: Path):
        root = self._make_root(tmp_path)
        tp = root / "to-process"

        # Only audio, no video
        audio = tp / "topic--RAW.wav"
        audio.write_bytes(b"audio")

        watcher = RecordingsWatcher(root, stability_interval=0.01, stability_checks=1)
        watcher._process_external_wav(audio)

        mock_assemble.assert_not_called()

    def test_error_callback_on_failure(self, tmp_path: Path):
        root = self._make_root(tmp_path)
        on_error = MagicMock()

        watcher = RecordingsWatcher(
            root,
            stability_interval=0.01,
            stability_checks=1,
            on_error=on_error,
        )
        # Non-existent file → FileNotFoundError in stability check
        watcher._process_external_wav(root / "to-process" / "missing--RAW.wav")

        on_error.assert_called_once()
        assert "disappeared" in on_error.call_args[0][1]


# ------------------------------------------------------------------
# RecordingsWatcher — ONNX mode event handling
# ------------------------------------------------------------------


class TestOnnxModeEvents:
    def _make_root(self, tmp_path: Path) -> Path:
        root = tmp_path / "recordings"
        ensure_root(root)
        return root

    def test_ignores_non_video_files(self, tmp_path: Path):
        root = self._make_root(tmp_path)
        mock_backend = MagicMock()
        watcher = RecordingsWatcher(
            root, backend=mock_backend, stability_interval=0.01, stability_checks=1
        )

        wav = root / "to-process" / "topic--RAW.wav"
        wav.write_bytes(b"audio")

        watcher._on_file_event(wav)
        mock_backend.process.assert_not_called()

    def test_ignores_non_raw_video(self, tmp_path: Path):
        root = self._make_root(tmp_path)
        mock_backend = MagicMock()
        watcher = RecordingsWatcher(
            root, backend=mock_backend, stability_interval=0.01, stability_checks=1
        )

        video = root / "to-process" / "topic.mp4"
        video.write_bytes(b"video")

        watcher._on_file_event(video)
        mock_backend.process.assert_not_called()

    @patch("clm.recordings.workflow.watcher.assemble_one")
    def test_processes_and_assembles_raw_video(self, mock_assemble: MagicMock, tmp_path: Path):
        root = self._make_root(tmp_path)
        tp = root / "to-process"

        video = tp / "topic--RAW.mp4"
        video.write_bytes(b"video data")

        mock_backend = MagicMock()

        # The backend writes a .wav as a side effect
        def fake_process(v, out):
            out.write_bytes(b"processed audio")

        mock_backend.process.side_effect = fake_process

        mock_assemble.return_value = MagicMock(success=True, output_file=Path("out.mp4"))
        on_processing = MagicMock()
        on_assembled = MagicMock()

        watcher = RecordingsWatcher(
            root,
            backend=mock_backend,
            stability_interval=0.01,
            stability_checks=1,
            on_processing=on_processing,
            on_assembled=on_assembled,
        )
        watcher._process_onnx_video(video)

        mock_backend.process.assert_called_once()
        on_processing.assert_called_once_with(video)
        mock_assemble.assert_called_once()
        on_assembled.assert_called_once()

    def test_error_callback_on_backend_failure(self, tmp_path: Path):
        root = self._make_root(tmp_path)
        tp = root / "to-process"

        video = tp / "topic--RAW.mp4"
        video.write_bytes(b"video data")

        mock_backend = MagicMock()
        mock_backend.process.side_effect = RuntimeError("ONNX crash")
        on_error = MagicMock()

        watcher = RecordingsWatcher(
            root,
            backend=mock_backend,
            stability_interval=0.01,
            stability_checks=1,
            on_error=on_error,
        )
        watcher._process_onnx_video(video)

        on_error.assert_called_once()
        assert "ONNX crash" in on_error.call_args[0][1]

    @patch("clm.recordings.workflow.watcher.assemble_one")
    def test_assembly_error_triggers_callback(self, mock_assemble: MagicMock, tmp_path: Path):
        root = self._make_root(tmp_path)
        tp = root / "to-process"

        video = tp / "topic--RAW.mp4"
        video.write_bytes(b"video data")

        mock_backend = MagicMock()
        mock_backend.process.side_effect = lambda v, o: o.write_bytes(b"audio")

        mock_assemble.return_value = MagicMock(
            success=False, output_file=Path("out.mp4"), error="mux failed"
        )
        on_error = MagicMock()

        watcher = RecordingsWatcher(
            root,
            backend=mock_backend,
            stability_interval=0.01,
            stability_checks=1,
            on_error=on_error,
        )
        watcher._process_onnx_video(video)

        on_error.assert_called_once()
        assert "mux failed" in on_error.call_args[0][1]


# ------------------------------------------------------------------
# RecordingsWatcher — matching video lookup
# ------------------------------------------------------------------


class TestFindMatchingVideo:
    def test_finds_mp4(self, tmp_path: Path):
        wav = tmp_path / "topic--RAW.wav"
        mp4 = tmp_path / "topic--RAW.mp4"
        wav.write_bytes(b"audio")
        mp4.write_bytes(b"video")

        watcher = RecordingsWatcher(tmp_path)
        assert watcher._find_matching_video(wav) == mp4

    def test_finds_mkv(self, tmp_path: Path):
        wav = tmp_path / "topic--RAW.wav"
        mkv = tmp_path / "topic--RAW.mkv"
        wav.write_bytes(b"audio")
        mkv.write_bytes(b"video")

        watcher = RecordingsWatcher(tmp_path)
        assert watcher._find_matching_video(wav) == mkv

    def test_returns_none_when_no_video(self, tmp_path: Path):
        wav = tmp_path / "topic--RAW.wav"
        wav.write_bytes(b"audio")

        watcher = RecordingsWatcher(tmp_path)
        assert watcher._find_matching_video(wav) is None


# ------------------------------------------------------------------
# RecordingsWatcher — subdirectory support
# ------------------------------------------------------------------


class TestSubdirectorySupport:
    @patch("clm.recordings.workflow.watcher.assemble_one")
    def test_external_assembles_in_subdirectory(self, mock_assemble: MagicMock, tmp_path: Path):
        root = tmp_path / "recordings"
        ensure_root(root)
        tp = root / "to-process"
        sub = tp / "course" / "section"
        sub.mkdir(parents=True)

        video = sub / "topic--RAW.mp4"
        audio = sub / "topic--RAW.wav"
        video.write_bytes(b"video")
        audio.write_bytes(b"audio")

        mock_assemble.return_value = MagicMock(success=True, output_file=Path("out.mp4"))

        watcher = RecordingsWatcher(root, stability_interval=0.01, stability_checks=1)
        watcher._process_external_wav(audio)

        mock_assemble.assert_called_once()
        pair = mock_assemble.call_args[0][0]
        assert pair.relative_dir == Path("course/section")


# ------------------------------------------------------------------
# _WatchHandler
# ------------------------------------------------------------------


class TestWatchHandler:
    def test_on_created_delegates_to_watcher(self, tmp_path: Path):
        watcher = RecordingsWatcher(tmp_path)
        watcher._on_file_event = MagicMock()

        handler = _WatchHandler(watcher)
        event = MagicMock(is_directory=False, src_path=str(tmp_path / "test.wav"))
        handler.on_created(event)

        watcher._on_file_event.assert_called_once_with(tmp_path / "test.wav")

    def test_on_created_ignores_directories(self, tmp_path: Path):
        watcher = RecordingsWatcher(tmp_path)
        watcher._on_file_event = MagicMock()

        handler = _WatchHandler(watcher)
        event = MagicMock(is_directory=True, src_path=str(tmp_path / "subdir"))
        handler.on_created(event)

        watcher._on_file_event.assert_not_called()

    def test_on_moved_uses_dest_path(self, tmp_path: Path):
        watcher = RecordingsWatcher(tmp_path)
        watcher._on_file_event = MagicMock()

        handler = _WatchHandler(watcher)
        event = MagicMock(
            is_directory=False,
            src_path=str(tmp_path / "old.wav"),
            dest_path=str(tmp_path / "new--RAW.wav"),
        )
        handler.on_moved(event)

        watcher._on_file_event.assert_called_once_with(tmp_path / "new--RAW.wav")


# ------------------------------------------------------------------
# Integration: live watcher with real filesystem events
# ------------------------------------------------------------------


class TestWatcherLiveEvents:
    """Tests that exercise the real watchdog Observer with short timeouts."""

    @patch("clm.recordings.workflow.watcher.assemble_one")
    def test_external_detects_new_wav(self, mock_assemble: MagicMock, tmp_path: Path):
        root = tmp_path / "recordings"
        ensure_root(root)
        tp = root / "to-process"

        # Pre-create the matching video
        video = tp / "lecture--RAW.mp4"
        video.write_bytes(b"video content")

        mock_assemble.return_value = MagicMock(success=True, output_file=Path("out.mp4"))
        assembled_event = threading.Event()

        def on_assembled(result):
            assembled_event.set()

        watcher = RecordingsWatcher(
            root,
            stability_interval=0.05,
            stability_checks=2,
            on_assembled=on_assembled,
        )
        watcher.start()

        try:
            # Create the .wav — watcher should detect it
            wav = tp / "lecture--RAW.wav"
            wav.write_bytes(b"processed audio content")

            # Wait for assembly (with timeout)
            assert assembled_event.wait(timeout=5.0), "Watcher did not trigger assembly"
            mock_assemble.assert_called_once()
        finally:
            watcher.stop()

    @patch("clm.recordings.workflow.watcher.assemble_one")
    def test_onnx_detects_new_video(self, mock_assemble: MagicMock, tmp_path: Path):
        root = tmp_path / "recordings"
        ensure_root(root)
        tp = root / "to-process"

        mock_backend = MagicMock()
        mock_backend.process.side_effect = lambda v, o: o.write_bytes(b"processed")

        mock_assemble.return_value = MagicMock(success=True, output_file=Path("out.mp4"))
        assembled_event = threading.Event()

        def on_assembled(result):
            assembled_event.set()

        watcher = RecordingsWatcher(
            root,
            backend=mock_backend,
            stability_interval=0.05,
            stability_checks=2,
            on_assembled=on_assembled,
        )
        watcher.start()

        try:
            video = tp / "lecture--RAW.mp4"
            video.write_bytes(b"raw video content")

            assert assembled_event.wait(timeout=5.0), "Watcher did not trigger assembly"
            mock_backend.process.assert_called_once()
            mock_assemble.assert_called_once()
        finally:
            watcher.stop()
