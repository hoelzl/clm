"""Tests for the recordings CLI command registration and basic help."""

from __future__ import annotations

from click.testing import CliRunner

from clm.cli.commands.recordings import recordings_group


class TestRecordingsGroup:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(recordings_group, ["--help"])
        assert result.exit_code == 0
        assert "recordings" in result.output.lower() or "Manage video" in result.output

    def test_subcommands_listed(self):
        runner = CliRunner()
        result = runner.invoke(recordings_group, ["--help"])
        assert result.exit_code == 0
        assert "check" in result.output
        assert "process" in result.output
        assert "batch" in result.output
        assert "status" in result.output
        assert "compare" in result.output

    def test_recordings_registered_in_cli(self):
        from clm.cli.main import cli

        command_names = list(cli.commands)
        assert "recordings" in command_names


class TestRecordingsConfig:
    def test_recordings_config_in_clm_config(self):
        """RecordingsConfig should be accessible via CLM's config system."""
        from clm.infrastructure.config import ClmConfig, RecordingsConfig

        config = ClmConfig()
        assert isinstance(config.recordings, RecordingsConfig)
        assert config.recordings.auto_process is False
        assert config.recordings.active_course == ""
        assert config.recordings.processing.deepfilter_atten_lim == 35.0
        assert config.recordings.processing.sample_rate == 48000
