"""Tests for glob expansion of VIDEO positional arguments in ``clm voiceover sync``.

The sync command accepts multiple videos; on Windows shells globs are not
expanded before the process starts, so the CLI does the expansion itself.
These tests verify the expansion helper and the CLI-level behaviour.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from clm.cli.commands.voiceover import _expand_video_args, voiceover_group


class TestExpandVideoArgs:
    """Unit tests for _expand_video_args — the glob + literal expansion helper."""

    def test_expands_single_glob(self, tmp_path, monkeypatch):
        (tmp_path / "Teil 1.mp4").write_text("x")
        (tmp_path / "Teil 2.mp4").write_text("x")
        (tmp_path / "Teil 3.mp4").write_text("x")
        (tmp_path / "other.mp4").write_text("x")
        monkeypatch.chdir(tmp_path)

        result = _expand_video_args(("Teil *.mp4",))

        assert [p.name for p in result] == ["Teil 1.mp4", "Teil 2.mp4", "Teil 3.mp4"]

    def test_natural_sort_parts(self, tmp_path, monkeypatch):
        """Teil 2 comes before Teil 10 (digit-aware comparison)."""
        (tmp_path / "Teil 10.mp4").write_text("x")
        (tmp_path / "Teil 1.mp4").write_text("x")
        (tmp_path / "Teil 2.mp4").write_text("x")
        monkeypatch.chdir(tmp_path)

        result = _expand_video_args(("Teil *.mp4",))

        assert [p.name for p in result] == ["Teil 1.mp4", "Teil 2.mp4", "Teil 10.mp4"]

    def test_mixes_literal_and_glob_preserves_order(self, tmp_path, monkeypatch):
        (tmp_path / "intro.mp4").write_text("x")
        (tmp_path / "Teil 1.mp4").write_text("x")
        (tmp_path / "Teil 2.mp4").write_text("x")
        (tmp_path / "outro.mp4").write_text("x")
        monkeypatch.chdir(tmp_path)

        result = _expand_video_args(("intro.mp4", "Teil *.mp4", "outro.mp4"))

        assert [p.name for p in result] == [
            "intro.mp4",
            "Teil 1.mp4",
            "Teil 2.mp4",
            "outro.mp4",
        ]

    def test_glob_no_match_raises_bad_parameter(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        import click

        with pytest.raises(click.BadParameter) as excinfo:
            _expand_video_args(("missing*.mp4",))

        assert "missing*.mp4" in str(excinfo.value)

    def test_literal_missing_raises_bad_parameter(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        import click

        with pytest.raises(click.BadParameter) as excinfo:
            _expand_video_args(("nonexistent.mp4",))

        assert "nonexistent.mp4" in str(excinfo.value)

    def test_question_mark_glob(self, tmp_path, monkeypatch):
        (tmp_path / "a.mp4").write_text("x")
        (tmp_path / "b.mp4").write_text("x")
        monkeypatch.chdir(tmp_path)

        result = _expand_video_args(("?.mp4",))

        assert sorted(p.name for p in result) == ["a.mp4", "b.mp4"]

    def test_bracket_glob(self, tmp_path, monkeypatch):
        (tmp_path / "part1.mp4").write_text("x")
        (tmp_path / "part2.mp4").write_text("x")
        (tmp_path / "part3.mp4").write_text("x")
        monkeypatch.chdir(tmp_path)

        result = _expand_video_args(("part[12].mp4",))

        assert [p.name for p in result] == ["part1.mp4", "part2.mp4"]

    def test_absolute_path_literal(self, tmp_path):
        video = tmp_path / "video.mp4"
        video.write_text("x")

        result = _expand_video_args((str(video),))

        assert result == [video]

    def test_absolute_path_glob(self, tmp_path):
        (tmp_path / "a.mp4").write_text("x")
        (tmp_path / "b.mp4").write_text("x")

        pattern = str(tmp_path / "*.mp4")
        result = _expand_video_args((pattern,))

        assert sorted(p.name for p in result) == ["a.mp4", "b.mp4"]


class TestSyncCliGlobIntegration:
    """CLI-level tests that verify the sync command accepts glob patterns."""

    def test_sync_cli_expands_glob_videos(self, tmp_path, monkeypatch):
        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text('# %% [markdown] lang="de" tags=["slide"]\n# Title\n')

        (tmp_path / "Teil 1.mp4").write_text("fake")
        (tmp_path / "Teil 2.mp4").write_text("fake")
        (tmp_path / "Teil 3.mp4").write_text("fake")
        monkeypatch.chdir(tmp_path)

        received: list[Path] = []

        def fake_build_parts(paths):
            received.extend(paths)
            raise SystemExit(99)  # stop the pipeline

        with patch("clm.voiceover.timeline.build_parts", side_effect=fake_build_parts):
            runner = CliRunner()
            result = runner.invoke(
                voiceover_group,
                [
                    "sync",
                    str(slide_file),
                    "Teil *.mp4",
                    "--lang",
                    "de",
                ],
            )

        assert [p.name for p in received] == ["Teil 1.mp4", "Teil 2.mp4", "Teil 3.mp4"]
        assert result.exit_code == 99

    def test_sync_cli_glob_no_match_errors(self, tmp_path, monkeypatch):
        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text('# %% [markdown] lang="de" tags=["slide"]\n# Title\n')
        monkeypatch.chdir(tmp_path)

        runner = CliRunner()
        result = runner.invoke(
            voiceover_group,
            [
                "sync",
                str(slide_file),
                "nomatch*.mp4",
                "--lang",
                "de",
            ],
        )

        assert result.exit_code != 0
        assert "nomatch*.mp4" in result.output
        assert "no files match" in result.output.lower()

    def test_sync_cli_literal_missing_errors(self, tmp_path, monkeypatch):
        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text('# %% [markdown] lang="de" tags=["slide"]\n# Title\n')
        monkeypatch.chdir(tmp_path)

        runner = CliRunner()
        result = runner.invoke(
            voiceover_group,
            [
                "sync",
                str(slide_file),
                "missing.mp4",
                "--lang",
                "de",
            ],
        )

        assert result.exit_code != 0
        assert "missing.mp4" in result.output
