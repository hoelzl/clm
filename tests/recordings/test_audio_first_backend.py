"""Tests for the :class:`AudioFirstBackend` Template Method."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from clm.recordings.workflow.backends.audio_first import AudioFirstBackend
from clm.recordings.workflow.backends.base import JobContext
from clm.recordings.workflow.directories import ensure_root, to_process_dir
from clm.recordings.workflow.jobs import (
    BackendCapabilities,
    JobState,
    ProcessingJob,
    ProcessingOptions,
)


class _RecordingContext:
    """Minimal JobContext that just records every reported snapshot."""

    def __init__(self, work_dir: Path) -> None:
        self.reports: list[tuple[JobState, float, str]] = []
        self._work_dir = work_dir

    @property
    def work_dir(self) -> Path:
        return self._work_dir

    def report(self, job: ProcessingJob) -> None:
        self.reports.append((job.state, job.progress, job.message))


class _StubAudioBackend(AudioFirstBackend):
    """Audio-first backend for tests that writes a placeholder WAV."""

    capabilities = BackendCapabilities(
        name="stub",
        display_name="Stub",
        is_synchronous=True,
    )

    def __init__(
        self,
        *,
        root_dir: Path,
        produce_audio: Callable[[Path], None] | None = None,
        fail_produce: bool = False,
    ) -> None:
        super().__init__(name="stub", root_dir=root_dir)
        self._produce = produce_audio
        self._fail_produce = fail_produce

    def accepts_file(self, path: Path) -> bool:
        return path.suffix == ".mp4"

    def _produce_audio(self, raw, output_wav, *, options, ctx, job):
        if self._fail_produce:
            raise RuntimeError("produce boom")
        job.message = "stub producing"
        job.progress = 0.5
        ctx.report(job)
        if self._produce is not None:
            self._produce(output_wav)
        else:
            output_wav.write_bytes(b"fake audio data")


def _setup_recording_tree(tmp_path: Path) -> tuple[Path, Path]:
    """Create to-process/final/archive dirs and return (root, raw_path)."""
    ensure_root(tmp_path)
    topic_dir = to_process_dir(tmp_path) / "py/week01"
    topic_dir.mkdir(parents=True, exist_ok=True)
    raw_path = topic_dir / "lecture--RAW.mp4"
    raw_path.write_bytes(b"fake video data")
    return tmp_path, raw_path


class TestAudioFirstBackendHappyPath:
    def test_successful_submit_reaches_completed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        root, raw = _setup_recording_tree(tmp_path)

        # Skip the real ffmpeg mux; pretend it succeeded and produced a file.
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

        backend = _StubAudioBackend(root_dir=root)
        ctx = _RecordingContext(tmp_path)
        final = root / "final" / "py/week01" / "lecture.mp4"

        job = backend.submit(
            raw,
            final,
            options=ProcessingOptions(),
            ctx=ctx,
        )

        assert job.state == JobState.COMPLETED
        assert job.progress == 1.0
        assert job.message == "Done"
        assert job.error is None

        # Reports cover: initial PROCESSING, stub's 0.5 progress,
        # ASSEMBLING, and the final COMPLETED snapshot.
        states = [s for (s, _, _) in ctx.reports]
        assert JobState.PROCESSING in states
        assert JobState.ASSEMBLING in states
        assert states[-1] == JobState.COMPLETED

    def test_relative_dir_computed_from_to_process(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        root, raw = _setup_recording_tree(tmp_path)
        monkeypatch.setattr(
            "clm.recordings.workflow.assembler.mux_video_audio",
            lambda *a, **k: a[3].write_bytes(b"x"),
        )
        monkeypatch.setattr(
            "clm.recordings.workflow.assembler.find_ffmpeg",
            lambda: Path("/fake/ffmpeg"),
        )

        backend = _StubAudioBackend(root_dir=root)
        ctx = _RecordingContext(tmp_path)
        job = backend.submit(
            raw,
            root / "final" / "py/week01" / "lecture.mp4",
            options=ProcessingOptions(),
            ctx=ctx,
        )

        assert job.relative_dir == Path("py/week01")


class TestAudioFirstBackendFailures:
    def test_produce_audio_exception_marks_failed(self, tmp_path: Path):
        root, raw = _setup_recording_tree(tmp_path)

        backend = _StubAudioBackend(root_dir=root, fail_produce=True)
        ctx = _RecordingContext(tmp_path)

        job = backend.submit(
            raw,
            root / "final" / "lecture.mp4",
            options=ProcessingOptions(),
            ctx=ctx,
        )

        assert job.state == JobState.FAILED
        assert "produce boom" in (job.error or "")
        # The final snapshot in ctx.reports must reflect the FAILED state
        # so observers are notified, not just the caller.
        assert ctx.reports[-1][0] == JobState.FAILED

    def test_assembly_failure_marks_failed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        root, raw = _setup_recording_tree(tmp_path)

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

        backend = _StubAudioBackend(root_dir=root)
        ctx = _RecordingContext(tmp_path)
        job = backend.submit(
            raw,
            root / "final" / "lecture.mp4",
            options=ProcessingOptions(),
            ctx=ctx,
        )

        assert job.state == JobState.FAILED
        assert "mux boom" in (job.error or "") or "Assembly failed" in (job.error or "")


class TestAudioFirstBackendProtocol:
    def test_poll_is_noop(self, tmp_path: Path):
        backend = _StubAudioBackend(root_dir=tmp_path)
        ctx = _RecordingContext(tmp_path)
        job = ProcessingJob(
            backend_name="stub",
            raw_path=tmp_path / "a--RAW.mp4",
            final_path=tmp_path / "a.mp4",
            relative_dir=Path(),
            state=JobState.PROCESSING,
        )
        assert backend.poll(job, ctx=ctx) is job

    def test_cancel_is_noop(self, tmp_path: Path):
        backend = _StubAudioBackend(root_dir=tmp_path)
        ctx = _RecordingContext(tmp_path)
        job = ProcessingJob(
            backend_name="stub",
            raw_path=tmp_path / "a--RAW.mp4",
            final_path=tmp_path / "a.mp4",
            relative_dir=Path(),
        )
        # Should not raise.
        backend.cancel(job, ctx=ctx)

    def test_runtime_checkable_protocol_conformance(self, tmp_path: Path):
        from clm.recordings.workflow.backends.base import ProcessingBackend

        backend = _StubAudioBackend(root_dir=tmp_path)
        assert isinstance(backend, ProcessingBackend)

    def test_stub_context_implements_job_context(self, tmp_path: Path):
        ctx = _RecordingContext(tmp_path)
        assert isinstance(ctx, JobContext)
