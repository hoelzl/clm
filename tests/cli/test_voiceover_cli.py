"""Tests for the voiceover and polish CLI commands."""

from __future__ import annotations

from click.testing import CliRunner

from clm.cli.commands.voiceover import voiceover_group


class TestVoiceoverGroup:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(voiceover_group, ["--help"])
        assert result.exit_code == 0
        assert "voiceover" in result.output.lower() or "speaker-notes" in result.output.lower()

    def test_sync_help(self):
        runner = CliRunner()
        result = runner.invoke(voiceover_group, ["sync", "--help"])
        assert result.exit_code == 0
        assert "--lang" in result.output
        assert "--mode" in result.output
        assert "--dry-run" in result.output

    def test_transcribe_help(self):
        runner = CliRunner()
        result = runner.invoke(voiceover_group, ["transcribe", "--help"])
        assert result.exit_code == 0
        assert "--whisper-model" in result.output

    def test_detect_help(self):
        runner = CliRunner()
        result = runner.invoke(voiceover_group, ["detect", "--help"])
        assert result.exit_code == 0

    def test_identify_help(self):
        runner = CliRunner()
        result = runner.invoke(voiceover_group, ["identify", "--help"])
        assert result.exit_code == 0
        assert "--lang" in result.output


class TestPolishCommand:
    def test_help(self):
        from clm.cli.commands.polish import polish

        runner = CliRunner()
        result = runner.invoke(polish, ["--help"])
        assert result.exit_code == 0
        assert "--lang" in result.output
        assert "--dry-run" in result.output


class TestMainCliRegistration:
    def test_voiceover_registered(self):
        from clm.cli.main import cli

        command_names = list(cli.commands)
        assert "voiceover" in command_names

    def test_polish_registered(self):
        from clm.cli.main import cli

        command_names = list(cli.commands)
        assert "polish" in command_names
