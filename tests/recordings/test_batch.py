"""Tests for batch processing utilities."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from clm.recordings.processing import batch as batch_module
from clm.recordings.processing.batch import (
    VIDEO_EXTENSIONS,
    BatchResult,
    find_video_files,
    process_batch,
)


class TestFindVideoFiles:
    def test_finds_common_formats(self, tmp_path: Path):
        (tmp_path / "video.mkv").touch()
        (tmp_path / "video.mp4").touch()
        (tmp_path / "video.avi").touch()
        (tmp_path / "readme.txt").touch()
        (tmp_path / "image.png").touch()

        files = find_video_files(tmp_path)
        names = {f.name for f in files}
        assert names == {"video.mkv", "video.mp4", "video.avi"}

    def test_case_insensitive(self, tmp_path: Path):
        (tmp_path / "video.MKV").touch()
        (tmp_path / "video.Mp4").touch()

        files = find_video_files(tmp_path)
        assert len(files) == 2

    def test_non_recursive_by_default(self, tmp_path: Path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (tmp_path / "top.mkv").touch()
        (sub / "nested.mkv").touch()

        files = find_video_files(tmp_path, recursive=False)
        assert len(files) == 1
        assert files[0].name == "top.mkv"

    def test_recursive(self, tmp_path: Path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (tmp_path / "top.mkv").touch()
        (sub / "nested.mkv").touch()

        files = find_video_files(tmp_path, recursive=True)
        assert len(files) == 2

    def test_sorted_output(self, tmp_path: Path):
        (tmp_path / "c.mkv").touch()
        (tmp_path / "a.mkv").touch()
        (tmp_path / "b.mkv").touch()

        files = find_video_files(tmp_path)
        names = [f.name for f in files]
        assert names == ["a.mkv", "b.mkv", "c.mkv"]

    def test_empty_directory(self, tmp_path: Path):
        files = find_video_files(tmp_path)
        assert files == []


class TestBatchResult:
    def test_total(self):
        from clm.recordings.processing.pipeline import ProcessingResult

        result = BatchResult(
            succeeded=[
                ProcessingResult(input_file=Path("a.mkv"), output_file=Path("a.mp4"), success=True)
            ],
            failed=[
                ProcessingResult(
                    input_file=Path("b.mkv"),
                    output_file=Path("b.mp4"),
                    success=False,
                    error="failed",
                )
            ],
            skipped=[Path("c.mkv")],
        )
        assert result.total == 3

    def test_summary(self):
        result = BatchResult()
        summary = result.summary()
        assert "0 files" in summary

    def test_summary_mentions_each_category(self):
        from clm.recordings.processing.pipeline import ProcessingResult

        result = BatchResult(
            succeeded=[
                ProcessingResult(input_file=Path("a.mkv"), output_file=Path("a.mp4"), success=True),
                ProcessingResult(input_file=Path("b.mkv"), output_file=Path("b.mp4"), success=True),
            ],
            skipped=[Path("c.mkv")],
        )
        summary = result.summary()
        assert "3 files" in summary
        assert "Succeeded: 2" in summary
        assert "Skipped:   1" in summary
        assert "Failed:    0" in summary
        # No failure detail section when there are no failures.
        assert "Failed files:" not in summary

    def test_summary_includes_failure_details(self):
        from clm.recordings.processing.pipeline import ProcessingResult

        result = BatchResult(
            failed=[
                ProcessingResult(
                    input_file=Path("broken.mkv"),
                    output_file=Path("broken.mp4"),
                    success=False,
                    error="ffmpeg exited 1",
                ),
            ],
        )
        summary = result.summary()
        assert "Failed files:" in summary
        assert "broken.mkv" in summary
        assert "ffmpeg exited 1" in summary

    def test_total_zero(self):
        assert BatchResult().total == 0


class TestFindVideoFilesExtensions:
    """Test the extensions parameter of find_video_files."""

    def test_default_matches_video_extensions_constant(self, tmp_path: Path):
        # Create one file per default extension and a non-video.
        for ext in VIDEO_EXTENSIONS:
            (tmp_path / f"clip{ext}").touch()
        (tmp_path / "notes.txt").touch()

        files = find_video_files(tmp_path)
        assert len(files) == len(VIDEO_EXTENSIONS)
        assert all(f.suffix.lower() in VIDEO_EXTENSIONS for f in files)

    def test_custom_extensions_overrides_default(self, tmp_path: Path):
        (tmp_path / "clip.mkv").touch()
        (tmp_path / "clip.wav").touch()
        (tmp_path / "clip.flac").touch()

        files = find_video_files(tmp_path, extensions={".wav", ".flac"})
        names = sorted(f.name for f in files)
        assert names == ["clip.flac", "clip.wav"]


@pytest.fixture
def fake_pipeline(monkeypatch: pytest.MonkeyPatch):
    """Replace ProcessingPipeline with a fake so process_batch doesn't touch binaries."""
    from clm.recordings.processing.pipeline import ProcessingResult

    instances: list[MagicMock] = []

    def _make_result(input_file: Path, output_file: Path, *, success: bool = True, error=None):
        return ProcessingResult(
            input_file=input_file,
            output_file=output_file,
            success=success,
            duration_seconds=1.0,
            error=error,
        )

    class FakePipeline:
        def __init__(self, config):
            self.config = config
            self._process = MagicMock()
            # Default: every process() call succeeds and creates an empty output file.
            self._process.side_effect = self._default_process
            instances.append(self)

        def _default_process(self, input_file: Path, output_file: Path, *, on_step=None):
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_bytes(b"processed")
            return _make_result(input_file, output_file, success=True)

        def process(self, input_file: Path, output_file: Path, *, on_step=None):
            return self._process(input_file, output_file, on_step=on_step)

    monkeypatch.setattr(batch_module, "ProcessingPipeline", FakePipeline)
    return instances


class TestProcessBatch:
    def test_empty_directory_returns_empty_result(self, tmp_path: Path, fake_pipeline):
        input_dir = tmp_path / "in"
        input_dir.mkdir()
        output_dir = tmp_path / "out"

        result = process_batch(input_dir, output_dir)

        assert result.total == 0
        assert result.succeeded == []
        assert result.failed == []
        assert result.skipped == []

    def test_creates_output_directory(self, tmp_path: Path, fake_pipeline):
        input_dir = tmp_path / "in"
        input_dir.mkdir()
        output_dir = tmp_path / "nested" / "out"

        process_batch(input_dir, output_dir)

        assert output_dir.is_dir()

    def test_processes_all_videos(self, tmp_path: Path, fake_pipeline):
        input_dir = tmp_path / "in"
        input_dir.mkdir()
        (input_dir / "a.mkv").touch()
        (input_dir / "b.mp4").touch()
        output_dir = tmp_path / "out"

        result = process_batch(input_dir, output_dir)

        assert len(result.succeeded) == 2
        assert len(result.failed) == 0
        assert len(result.skipped) == 0

    def test_output_filename_uses_suffix_and_extension(self, tmp_path: Path, fake_pipeline):
        input_dir = tmp_path / "in"
        input_dir.mkdir()
        (input_dir / "lecture.mkv").touch()
        output_dir = tmp_path / "out"

        result = process_batch(input_dir, output_dir, suffix="_clean")

        expected = output_dir / "lecture_clean.mp4"
        assert result.succeeded[0].output_file == expected
        assert expected.is_file()

    def test_skips_when_output_already_exists(self, tmp_path: Path, fake_pipeline):
        input_dir = tmp_path / "in"
        input_dir.mkdir()
        (input_dir / "video.mkv").touch()
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        # Pre-create output with the default suffix.
        (output_dir / "video_final.mp4").write_bytes(b"already done")

        result = process_batch(input_dir, output_dir)

        assert result.skipped == [input_dir / "video.mkv"]
        assert result.succeeded == []
        # The fake pipeline's process method should not have been invoked.
        assert fake_pipeline[0]._process.call_count == 0

    def test_collects_failures_separately(self, tmp_path: Path, fake_pipeline):
        from clm.recordings.processing.pipeline import ProcessingResult

        input_dir = tmp_path / "in"
        input_dir.mkdir()
        good = input_dir / "good.mkv"
        bad = input_dir / "bad.mkv"
        good.touch()
        bad.touch()
        output_dir = tmp_path / "out"

        def side_effect(input_file, output_file, *, on_step=None):
            if input_file.name == "bad.mkv":
                return ProcessingResult(
                    input_file=input_file,
                    output_file=output_file,
                    success=False,
                    error="boom",
                )
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_bytes(b"ok")
            return ProcessingResult(
                input_file=input_file,
                output_file=output_file,
                success=True,
            )

        # Patch the fake pipeline instance AFTER process_batch constructs it —
        # actually, fake_pipeline is constructed inside process_batch. Patch the
        # class's _default_process via side_effect upfront by subclassing is
        # hard; simpler: patch ProcessingPipeline itself.
        from clm.recordings.processing import batch as batch_module_inner

        class PipelineWithFailure:
            def __init__(self, config):
                self.config = config

            def process(self, input_file, output_file, *, on_step=None):
                return side_effect(input_file, output_file, on_step=on_step)

        # Replace the monkeypatched class from the fixture.
        batch_module_inner.ProcessingPipeline = PipelineWithFailure

        result = process_batch(input_dir, output_dir)

        succeeded_inputs = {r.input_file.name for r in result.succeeded}
        failed_inputs = {r.input_file.name for r in result.failed}
        assert succeeded_inputs == {"good.mkv"}
        assert failed_inputs == {"bad.mkv"}

    def test_on_file_callback_invoked_per_file(self, tmp_path: Path, fake_pipeline):
        input_dir = tmp_path / "in"
        input_dir.mkdir()
        (input_dir / "a.mkv").touch()
        (input_dir / "b.mkv").touch()
        output_dir = tmp_path / "out"

        calls: list[tuple[int, str, int]] = []

        def on_file(i: int, f: Path, total: int) -> None:
            calls.append((i, f.name, total))

        process_batch(input_dir, output_dir, on_file=on_file)

        assert calls == [(0, "a.mkv", 2), (1, "b.mkv", 2)]

    def test_on_step_passed_through_to_pipeline(self, tmp_path: Path, fake_pipeline):
        input_dir = tmp_path / "in"
        input_dir.mkdir()
        (input_dir / "a.mkv").touch()
        output_dir = tmp_path / "out"

        def on_step(step: int, name: str, total: int) -> None:
            pass

        process_batch(input_dir, output_dir, on_step=on_step)

        # The fake pipeline recorded the on_step kwarg.
        args, kwargs = fake_pipeline[0]._process.call_args
        assert kwargs["on_step"] is on_step

    def test_recursive_flag_descends_subdirs(self, tmp_path: Path, fake_pipeline):
        input_dir = tmp_path / "in"
        sub = input_dir / "sub"
        sub.mkdir(parents=True)
        (input_dir / "top.mkv").touch()
        (sub / "nested.mkv").touch()
        output_dir = tmp_path / "out"

        result = process_batch(input_dir, output_dir, recursive=True)

        assert len(result.succeeded) == 2

    def test_uses_default_config_when_none_passed(self, tmp_path: Path, fake_pipeline):
        input_dir = tmp_path / "in"
        input_dir.mkdir()
        (input_dir / "a.mkv").touch()
        output_dir = tmp_path / "out"

        process_batch(input_dir, output_dir)

        # The fake pipeline instance received a PipelineConfig (the default).
        from clm.recordings.processing.config import PipelineConfig

        assert isinstance(fake_pipeline[0].config, PipelineConfig)

    def test_custom_config_is_used(self, tmp_path: Path, fake_pipeline):
        from clm.recordings.processing.config import PipelineConfig

        input_dir = tmp_path / "in"
        input_dir.mkdir()
        (input_dir / "a.mkv").touch()
        output_dir = tmp_path / "out"
        config = PipelineConfig(output_extension="mkv")

        result = process_batch(input_dir, output_dir, config=config)

        assert fake_pipeline[0].config is config
        # The output file name reflects the custom extension.
        assert result.succeeded[0].output_file.suffix == ".mkv"
