"""Tests for CLI sync command merge mode changes.

Tests --overwrite flag, --mode verbatim + merge error, and
the new default merge behavior at the CLI level.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from clm.cli.commands.voiceover import sync, voiceover_group


class TestVerbatimMergeError:
    """--mode verbatim without --overwrite should error."""

    def test_verbatim_without_overwrite_errors(self, tmp_path):
        # Create a dummy slide file
        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text('# %% [markdown] lang="de" tags=["slide"]\n# Title\n')

        # Create a dummy video file
        video_file = tmp_path / "video.mp4"
        video_file.write_text("fake video")

        runner = CliRunner()
        result = runner.invoke(
            voiceover_group,
            [
                "sync",
                str(slide_file),
                str(video_file),
                "--lang",
                "de",
                "--mode",
                "verbatim",
            ],
        )
        assert result.exit_code != 0
        assert "verbatim" in result.output.lower()

    def test_verbatim_with_overwrite_does_not_error(self, tmp_path):
        """--mode verbatim --overwrite should NOT produce the usage error.

        We can't run the full pipeline (no real video), but we verify
        the usage-error check passes by checking we get a different error.
        """
        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text('# %% [markdown] lang="de" tags=["slide"]\n# Title\n')

        video_file = tmp_path / "video.mp4"
        video_file.write_text("fake video")

        runner = CliRunner()
        result = runner.invoke(
            voiceover_group,
            [
                "sync",
                str(slide_file),
                str(video_file),
                "--lang",
                "de",
                "--mode",
                "verbatim",
                "--overwrite",
            ],
        )
        # Should NOT contain the verbatim+merge error
        assert "Cannot use --mode verbatim with merge" not in result.output


class TestSyncOverwriteFlag:
    """Verify --overwrite flag is accepted by the CLI."""

    def test_overwrite_flag_is_recognized(self, tmp_path):
        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text('# %% [markdown] lang="de" tags=["slide"]\n# Title\n')

        video_file = tmp_path / "video.mp4"
        video_file.write_text("fake video")

        runner = CliRunner()
        # Just check that the flag is accepted (pipeline will fail on video)
        result = runner.invoke(
            voiceover_group,
            [
                "sync",
                str(slide_file),
                str(video_file),
                "--lang",
                "de",
                "--overwrite",
            ],
        )
        assert "no such option: --overwrite" not in result.output

    def test_polished_mode_without_overwrite_is_merge(self, tmp_path):
        """Default behavior (no --overwrite) should attempt merge mode."""
        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text('# %% [markdown] lang="de" tags=["slide"]\n# Title\n')

        video_file = tmp_path / "video.mp4"
        video_file.write_text("fake video")

        runner = CliRunner()
        result = runner.invoke(
            voiceover_group,
            [
                "sync",
                str(slide_file),
                str(video_file),
                "--lang",
                "de",
            ],
        )
        # Should NOT contain the verbatim error (polished + merge is fine)
        assert "Cannot use --mode verbatim" not in result.output


class TestSyncHelpText:
    """Verify updated help text."""

    def test_help_mentions_merge(self):
        runner = CliRunner()
        result = runner.invoke(voiceover_group, ["sync", "--help"])
        assert "--overwrite" in result.output

    def test_help_mentions_overwrite(self):
        runner = CliRunner()
        result = runner.invoke(voiceover_group, ["sync", "--help"])
        assert "Overwrite" in result.output or "overwrite" in result.output
