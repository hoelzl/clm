"""Tests for :class:`ExternalAudioFirstBackend`.

The external backend inverts the usual trigger shape: it fires on
``--RAW.wav`` files (the output of an external audio tool like iZotope
RX 11) and resolves the matching raw video from the same directory.
These tests exercise that inversion and the happy/error paths.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.recordings.workflow.backends.base import JobContext, ProcessingBackend
from clm.recordings.workflow.backends.external import ExternalAudioFirstBackend
from clm.recordings.workflow.directories import ensure_root, to_process_dir
from clm.recordings.workflow.jobs import (
    JobState,
    ProcessingJob,
    ProcessingOptions,
)


class _RecordingContext:
    """Minimal :class:`JobContext` that records every reported snapshot."""

    def __init__(self, work_dir: Path) -> None:
        self.reports: list[tuple[JobState, float, str]] = []
        self._work_dir = work_dir

    @property
    def work_dir(self) -> Path:
        return self._work_dir

    def report(self, job: ProcessingJob) -> None:
        self.reports.append((job.state, job.progress, job.message))


@pytest.fixture()
def fake_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch ``assemble_one``'s ffmpeg helpers so tests don't shell out."""

    def fake_mux(ffmpeg, video, audio, output):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"fake muxed")

    monkeypatch.setattr(
        "clm.recordings.workflow.assembler.mux_video_audio",
        fake_mux,
    )
    monkeypatch.setattr(
        "clm.recordings.workflow.assembler.find_ffmpeg",
        lambda: Path("/fake/ffmpeg"),
    )


def _setup_pair(
    tmp_path: Path,
    *,
    create_video: bool = True,
    topic: str = "lecture",
) -> tuple[Path, Path, Path]:
    """Create a recording tree and return (root, wav_path, video_path)."""
    ensure_root(tmp_path)
    topic_dir = to_process_dir(tmp_path) / "py/week01"
    topic_dir.mkdir(parents=True, exist_ok=True)
    wav = topic_dir / f"{topic}--RAW.wav"
    video = topic_dir / f"{topic}--RAW.mp4"
    wav.write_bytes(b"processed audio")
    if create_video:
        video.write_bytes(b"raw video")
    return tmp_path, wav, video


# ------------------------------------------------------------------
# Capabilities & Protocol conformance
# ------------------------------------------------------------------


class TestExternalBackendCapabilities:
    def test_name_and_flags(self, tmp_path: Path):
        backend = ExternalAudioFirstBackend(root_dir=tmp_path)
        caps = backend.capabilities
        assert caps.name == "external"
        assert caps.is_synchronous is True
        assert caps.requires_internet is False
        assert caps.requires_api_key is False
        assert caps.video_in_video_out is False

    def test_supports_wav_extension(self, tmp_path: Path):
        backend = ExternalAudioFirstBackend(root_dir=tmp_path)
        assert ".wav" in backend.capabilities.supported_input_extensions

    def test_runtime_checkable_protocol_conformance(self, tmp_path: Path):
        backend = ExternalAudioFirstBackend(root_dir=tmp_path)
        assert isinstance(backend, ProcessingBackend)


# ------------------------------------------------------------------
# accepts_file — trigger inversion
# ------------------------------------------------------------------


class TestAcceptsFile:
    def test_accepts_raw_wav(self, tmp_path: Path):
        backend = ExternalAudioFirstBackend(root_dir=tmp_path)
        assert backend.accepts_file(Path("topic--RAW.wav")) is True

    def test_rejects_non_raw_wav(self, tmp_path: Path):
        backend = ExternalAudioFirstBackend(root_dir=tmp_path)
        assert backend.accepts_file(Path("topic.wav")) is False

    def test_rejects_raw_video(self, tmp_path: Path):
        """External backend is triggered by the .wav, NOT the video."""
        backend = ExternalAudioFirstBackend(root_dir=tmp_path)
        assert backend.accepts_file(Path("topic--RAW.mp4")) is False
        assert backend.accepts_file(Path("topic--RAW.mkv")) is False

    def test_rejects_unrelated_files(self, tmp_path: Path):
        backend = ExternalAudioFirstBackend(root_dir=tmp_path)
        assert backend.accepts_file(Path("notes.txt")) is False
        assert backend.accepts_file(Path("thumbnail.jpg")) is False

    def test_respects_custom_raw_suffix(self, tmp_path: Path):
        backend = ExternalAudioFirstBackend(root_dir=tmp_path, raw_suffix="--SRC")
        assert backend.accepts_file(Path("topic--SRC.wav")) is True
        assert backend.accepts_file(Path("topic--RAW.wav")) is False


# ------------------------------------------------------------------
# submit — happy path
# ------------------------------------------------------------------


class TestSubmitHappyPath:
    def test_pairs_wav_with_video_and_assembles(
        self,
        tmp_path: Path,
        fake_ffmpeg: None,
    ):
        root, wav, video = _setup_pair(tmp_path)

        backend = ExternalAudioFirstBackend(root_dir=root)
        ctx = _RecordingContext(tmp_path)
        final_path = root / "final" / "py/week01" / "lecture.mp4"

        job = backend.submit(
            wav,
            final_path,
            options=ProcessingOptions(),
            ctx=ctx,
        )

        assert job.state == JobState.COMPLETED
        assert job.progress == 1.0
        assert job.message == "Done"
        assert job.error is None
        # Assembler produces a real file at the planned path.
        assert job.final_path.exists()

    def test_finds_mkv_video(self, tmp_path: Path, fake_ffmpeg: None):
        """VIDEO_EXTENSIONS covers mkv as well as mp4."""
        ensure_root(tmp_path)
        topic_dir = to_process_dir(tmp_path) / "course"
        topic_dir.mkdir(parents=True, exist_ok=True)
        wav = topic_dir / "lecture--RAW.wav"
        mkv = topic_dir / "lecture--RAW.mkv"
        wav.write_bytes(b"audio")
        mkv.write_bytes(b"video")

        backend = ExternalAudioFirstBackend(root_dir=tmp_path)
        ctx = _RecordingContext(tmp_path)

        job = backend.submit(
            wav,
            tmp_path / "final" / "course" / "lecture.mp4",
            options=ProcessingOptions(),
            ctx=ctx,
        )

        assert job.state == JobState.COMPLETED

    def test_relative_dir_computed_from_to_process(
        self,
        tmp_path: Path,
        fake_ffmpeg: None,
    ):
        root, wav, _video = _setup_pair(tmp_path)

        backend = ExternalAudioFirstBackend(root_dir=root)
        ctx = _RecordingContext(tmp_path)
        job = backend.submit(
            wav,
            root / "final" / "py/week01" / "lecture.mp4",
            options=ProcessingOptions(),
            ctx=ctx,
        )

        assert job.relative_dir == Path("py/week01")

    def test_reports_progress_via_context(
        self,
        tmp_path: Path,
        fake_ffmpeg: None,
    ):
        root, wav, _video = _setup_pair(tmp_path)

        backend = ExternalAudioFirstBackend(root_dir=root)
        ctx = _RecordingContext(tmp_path)
        backend.submit(
            wav,
            root / "final" / "py/week01" / "lecture.mp4",
            options=ProcessingOptions(),
            ctx=ctx,
        )

        # At least two reports: initial ASSEMBLING, final COMPLETED.
        states = [s for (s, _, _) in ctx.reports]
        assert JobState.ASSEMBLING in states
        assert states[-1] == JobState.COMPLETED


# ------------------------------------------------------------------
# submit — failure paths
# ------------------------------------------------------------------


class TestSubmitFailures:
    def test_missing_video_marks_job_failed(self, tmp_path: Path):
        root, wav, _video = _setup_pair(tmp_path, create_video=False)

        backend = ExternalAudioFirstBackend(root_dir=root)
        ctx = _RecordingContext(tmp_path)
        job = backend.submit(
            wav,
            root / "final" / "py/week01" / "lecture.mp4",
            options=ProcessingOptions(),
            ctx=ctx,
        )

        assert job.state == JobState.FAILED
        assert job.error is not None
        assert "No matching raw video" in job.error
        # Last report reflects the failed state so the dashboard sees it.
        assert ctx.reports[-1][0] == JobState.FAILED

    def test_assembly_failure_marks_failed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        root, wav, _video = _setup_pair(tmp_path)

        def boom(*args, **kwargs):
            raise RuntimeError("mux boom")

        monkeypatch.setattr(
            "clm.recordings.workflow.assembler.mux_video_audio",
            boom,
        )
        monkeypatch.setattr(
            "clm.recordings.workflow.assembler.find_ffmpeg",
            lambda: Path("/fake/ffmpeg"),
        )

        backend = ExternalAudioFirstBackend(root_dir=root)
        ctx = _RecordingContext(tmp_path)
        job = backend.submit(
            wav,
            root / "final" / "py/week01" / "lecture.mp4",
            options=ProcessingOptions(),
            ctx=ctx,
        )

        assert job.state == JobState.FAILED
        assert job.error is not None
        assert "mux boom" in job.error or "Assembly failed" in job.error


# ------------------------------------------------------------------
# poll / cancel — inherited from AudioFirstBackend
# ------------------------------------------------------------------


class TestProtocolSurface:
    def test_poll_is_noop(self, tmp_path: Path):
        backend = ExternalAudioFirstBackend(root_dir=tmp_path)
        ctx = _RecordingContext(tmp_path)
        job = ProcessingJob(
            backend_name="external",
            raw_path=tmp_path / "a--RAW.wav",
            final_path=tmp_path / "a.mp4",
            relative_dir=Path(),
            state=JobState.COMPLETED,
        )
        assert backend.poll(job, ctx=ctx) is job

    def test_cancel_is_noop(self, tmp_path: Path):
        backend = ExternalAudioFirstBackend(root_dir=tmp_path)
        ctx = _RecordingContext(tmp_path)
        job = ProcessingJob(
            backend_name="external",
            raw_path=tmp_path / "a--RAW.wav",
            final_path=tmp_path / "a.mp4",
            relative_dir=Path(),
        )
        backend.cancel(job, ctx=ctx)  # must not raise


# ------------------------------------------------------------------
# Sanity: _RecordingContext conforms to the JobContext protocol
# ------------------------------------------------------------------


class TestRecordingContextConformance:
    def test_is_a_job_context(self, tmp_path: Path):
        ctx = _RecordingContext(tmp_path)
        assert isinstance(ctx, JobContext)
