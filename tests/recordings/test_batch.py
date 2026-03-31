"""Tests for batch processing utilities."""

from __future__ import annotations

from pathlib import Path

from clm.recordings.processing.batch import BatchResult, find_video_files


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
