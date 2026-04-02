"""Tests for recordings workflow assembler."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clm.recordings.workflow.assembler import (
    AssemblyBatchResult,
    AssemblyResult,
    assemble_all,
    assemble_one,
    mux_video_audio,
)
from clm.recordings.workflow.directories import PendingPair, ensure_root

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def recording_root(tmp_path: Path) -> Path:
    """A tmp recordings root with the three-tier structure."""
    root = tmp_path / "recordings"
    ensure_root(root)
    return root


def _make_pair(root: Path, rel_dir: str, stem: str) -> PendingPair:
    """Create a fake raw video + audio pair in to-process/."""
    tp = root / "to-process" / rel_dir
    tp.mkdir(parents=True, exist_ok=True)
    video = tp / f"{stem}--RAW.mp4"
    audio = tp / f"{stem}--RAW.wav"
    video.write_bytes(b"fake video content")
    audio.write_bytes(b"fake audio content")
    return PendingPair(
        video=video,
        audio=audio,
        relative_dir=Path(rel_dir),
    )


# ---------------------------------------------------------------------------
# mux_video_audio
# ---------------------------------------------------------------------------


class TestMuxVideoAudio:
    @patch("clm.recordings.workflow.assembler.run_subprocess")
    def test_calls_ffmpeg_with_correct_args(self, mock_run, tmp_path: Path):
        ffmpeg = Path("/usr/bin/ffmpeg")
        video = tmp_path / "in.mp4"
        audio = tmp_path / "in.wav"
        output = tmp_path / "out" / "final.mp4"

        mux_video_audio(ffmpeg, video, audio, output)

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == ffmpeg
        assert str(video) in [str(a) for a in args]
        assert str(audio) in [str(a) for a in args]
        assert str(output) in [str(a) for a in args]
        assert "-c:v" in [str(a) for a in args]
        assert "copy" in [str(a) for a in args]

    @patch("clm.recordings.workflow.assembler.run_subprocess")
    def test_creates_output_directory(self, mock_run, tmp_path: Path):
        ffmpeg = Path("/usr/bin/ffmpeg")
        output = tmp_path / "deep" / "nested" / "out.mp4"

        mux_video_audio(ffmpeg, tmp_path / "v.mp4", tmp_path / "a.wav", output)

        assert output.parent.is_dir()


# ---------------------------------------------------------------------------
# assemble_one
# ---------------------------------------------------------------------------


class TestAssembleOne:
    @patch("clm.recordings.workflow.assembler.run_subprocess")
    def test_success(self, mock_run, recording_root: Path):
        pair = _make_pair(recording_root, "course/section", "topic")
        final = recording_root / "final"
        archive = recording_root / "archive"

        result = assemble_one(pair, final, archive, ffmpeg=Path("ffmpeg"))

        assert result.success is True
        assert result.output_file == final / "course" / "section" / "topic.mp4"
        # Originals should be archived
        assert not pair.video.exists()
        assert not pair.audio.exists()
        assert (archive / "course" / "section" / "topic--RAW.mp4").exists()
        assert (archive / "course" / "section" / "topic--RAW.wav").exists()

    @patch(
        "clm.recordings.workflow.assembler.run_subprocess", side_effect=RuntimeError("ffmpeg died")
    )
    def test_failure(self, mock_run, recording_root: Path):
        pair = _make_pair(recording_root, "course/section", "topic")

        result = assemble_one(
            pair,
            recording_root / "final",
            recording_root / "archive",
            ffmpeg=Path("ffmpeg"),
        )

        assert result.success is False
        assert "ffmpeg died" in result.error
        # Originals should NOT be archived on failure
        assert pair.video.exists()
        assert pair.audio.exists()

    @patch("clm.recordings.workflow.assembler.run_subprocess")
    def test_custom_output_ext(self, mock_run, recording_root: Path):
        pair = _make_pair(recording_root, "course/section", "topic")

        result = assemble_one(
            pair,
            recording_root / "final",
            recording_root / "archive",
            ffmpeg=Path("ffmpeg"),
            output_ext=".mkv",
        )

        assert result.output_file.suffix == ".mkv"


# ---------------------------------------------------------------------------
# assemble_all
# ---------------------------------------------------------------------------


class TestAssembleAll:
    @patch("clm.recordings.workflow.assembler.find_ffmpeg", return_value=Path("ffmpeg"))
    @patch("clm.recordings.workflow.assembler.run_subprocess")
    def test_assembles_all_pairs(self, mock_run, mock_ffmpeg, recording_root: Path):
        _make_pair(recording_root, "course/s1", "alpha")
        _make_pair(recording_root, "course/s2", "beta")

        result = assemble_all(recording_root)

        assert len(result.succeeded) == 2
        assert len(result.failed) == 0

    @patch("clm.recordings.workflow.assembler.find_ffmpeg", return_value=Path("ffmpeg"))
    @patch("clm.recordings.workflow.assembler.run_subprocess")
    def test_calls_progress_callback(self, mock_run, mock_ffmpeg, recording_root: Path):
        _make_pair(recording_root, "course/section", "topic")
        callback = MagicMock()

        assemble_all(recording_root, on_pair=callback)

        callback.assert_called_once()
        args = callback.call_args[0]
        assert args[0] == 0  # index
        assert args[2] == 1  # total

    @patch("clm.recordings.workflow.assembler.find_ffmpeg", return_value=Path("ffmpeg"))
    def test_no_pairs(self, mock_ffmpeg, recording_root: Path):
        result = assemble_all(recording_root)
        assert len(result.results) == 0

    @patch("clm.recordings.workflow.assembler.find_ffmpeg", return_value=Path("ffmpeg"))
    @patch("clm.recordings.workflow.assembler.run_subprocess", side_effect=RuntimeError("boom"))
    def test_partial_failure(self, mock_run, mock_ffmpeg, recording_root: Path):
        _make_pair(recording_root, "course/s1", "alpha")
        _make_pair(recording_root, "course/s2", "beta")

        result = assemble_all(recording_root)

        assert len(result.failed) == 2
        assert "boom" in result.summary()


# ---------------------------------------------------------------------------
# AssemblyBatchResult
# ---------------------------------------------------------------------------


class TestAssemblyBatchResult:
    def test_summary_no_failures(self):
        batch = AssemblyBatchResult(
            results=[
                AssemblyResult(video=Path("a.mp4"), output_file=Path("a_out.mp4"), success=True),
                AssemblyResult(video=Path("b.mp4"), output_file=Path("b_out.mp4"), success=True),
            ]
        )
        s = batch.summary()
        assert "Succeeded: 2" in s
        assert "Failed:    0" in s

    def test_summary_with_failures(self):
        batch = AssemblyBatchResult(
            results=[
                AssemblyResult(video=Path("a.mp4"), output_file=Path("a_out.mp4"), success=True),
                AssemblyResult(
                    video=Path("b.mp4"),
                    output_file=Path("b_out.mp4"),
                    success=False,
                    error="oops",
                ),
            ]
        )
        s = batch.summary()
        assert "Failed:    1" in s
        assert "oops" in s


# ---------------------------------------------------------------------------
# Integration test (requires real ffmpeg)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAssembleIntegration:
    """End-to-end assembly with real FFmpeg.

    Skipped unless ``pytest -m integration`` is used.
    """

    def test_assemble_real_files(self, recording_root: Path):
        """Create a minimal valid video + audio, assemble them."""
        import shutil
        import subprocess

        if shutil.which("ffmpeg") is None:
            pytest.skip("ffmpeg not installed")

        # Generate a 1-second silent video and audio via ffmpeg
        tp = recording_root / "to-process" / "test-course" / "test-section"
        tp.mkdir(parents=True)

        video = tp / "intro--RAW.mp4"
        audio = tp / "intro--RAW.wav"

        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=black:s=320x240:d=1",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=48000:cl=mono",
                "-t",
                "1",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                str(video),
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=48000:cl=mono",
                "-t",
                "1",
                str(audio),
            ],
            check=True,
            capture_output=True,
        )

        result = assemble_all(recording_root)

        assert len(result.succeeded) == 1
        output = recording_root / "final" / "test-course" / "test-section" / "intro.mp4"
        assert output.is_file()
        assert output.stat().st_size > 0

        # Originals archived
        assert (
            recording_root / "archive" / "test-course" / "test-section" / "intro--RAW.mp4"
        ).is_file()
        assert (
            recording_root / "archive" / "test-course" / "test-section" / "intro--RAW.wav"
        ).is_file()
