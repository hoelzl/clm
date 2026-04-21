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
        assert "--transcript" in result.output
        assert "--alignment" in result.output

    def test_group_help_shows_cache_flags(self):
        runner = CliRunner()
        result = runner.invoke(voiceover_group, ["--help"])
        assert result.exit_code == 0
        assert "--cache-root" in result.output
        assert "--no-cache" in result.output
        assert "--refresh-cache" in result.output

    def test_group_help_shows_cache_subgroup(self):
        runner = CliRunner()
        result = runner.invoke(voiceover_group, ["--help"])
        assert result.exit_code == 0
        assert "cache" in result.output

    def test_group_help_shows_trace_subgroup(self):
        runner = CliRunner()
        result = runner.invoke(voiceover_group, ["--help"])
        assert result.exit_code == 0
        assert "trace" in result.output

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

    def test_debug_group_help(self):
        runner = CliRunner()
        result = runner.invoke(voiceover_group, ["debug", "--help"])
        assert result.exit_code == 0
        assert "voiceover-commits" in result.output

    def test_voiceover_commits_help(self):
        runner = CliRunner()
        result = runner.invoke(voiceover_group, ["debug", "voiceover-commits", "--help"])
        assert result.exit_code == 0
        assert "--threshold" in result.output
        assert "--since" in result.output


class TestCacheSubgroup:
    def test_cache_help(self):
        runner = CliRunner()
        result = runner.invoke(voiceover_group, ["cache", "--help"])
        assert result.exit_code == 0
        assert "list" in result.output
        assert "prune" in result.output
        assert "clear" in result.output

    def test_cache_list_empty(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            voiceover_group,
            ["--cache-root", str(tmp_path / "empty"), "cache", "list"],
        )
        assert result.exit_code == 0
        assert "empty" in result.output.lower()

    def test_cache_list_with_entry(self, tmp_path):
        from clm.voiceover.cache import TranscribeConfig, TranscriptsCache, VideoKey
        from clm.voiceover.transcribe import Transcript

        video = tmp_path / "video.mp4"
        video.write_bytes(b"x" * 256)
        cache_root = tmp_path / "cache"
        tc = TranscriptsCache(cache_root)
        cfg = TranscribeConfig(
            backend="faster-whisper",
            model="large-v3",
            language="de",
            device_class="cpu",
        )
        tc.put(
            VideoKey.from_path(video),
            cfg,
            Transcript(segments=[], language="de", duration=1.0),
        )

        runner = CliRunner()
        result = runner.invoke(voiceover_group, ["--cache-root", str(cache_root), "cache", "list"])
        assert result.exit_code == 0
        assert "transcripts" in result.output

    def test_cache_clear_aborts_without_confirmation(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            voiceover_group,
            ["--cache-root", str(tmp_path / "cache"), "cache", "clear"],
            input="n\n",
        )
        assert result.exit_code != 0

    def test_cache_clear_with_yes(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            voiceover_group,
            ["--cache-root", str(tmp_path / "cache"), "cache", "clear", "--yes"],
        )
        assert result.exit_code == 0

    def test_cache_prune_requires_max_age(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            voiceover_group,
            ["--cache-root", str(tmp_path / "cache"), "cache", "prune"],
        )
        assert result.exit_code != 0


class TestTraceSubgroup:
    def test_trace_help(self):
        runner = CliRunner()
        result = runner.invoke(voiceover_group, ["trace", "--help"])
        assert result.exit_code == 0
        assert "show" in result.output

    def test_trace_show_renders_summary(self, tmp_path):
        from clm.voiceover.trace_log import TraceLog

        trace = TraceLog.create("slides.py", base_dir=tmp_path)
        trace.log_merge_call(
            slide_id="slides/1",
            language="en",
            baseline="",
            transcript="hello world",
            llm_merged="- hello world",
            rewrites=[],
            dropped_from_transcript=[],
        )

        runner = CliRunner()
        result = runner.invoke(voiceover_group, ["trace", "show", str(trace.path)])
        assert result.exit_code == 0
        # Rich truncates long table cells, so just check the summary line
        assert "clm.voiceover.trace/1" in result.output
        assert "1" in result.output and "entries" in result.output

    def test_trace_show_json(self, tmp_path):
        import json as jsonlib

        from clm.voiceover.trace_log import TraceLog

        trace = TraceLog.create("slides.py", base_dir=tmp_path)
        trace.log_merge_call(
            slide_id="slides/1",
            language="en",
            baseline="",
            transcript="x",
            llm_merged="- x",
            rewrites=[],
            dropped_from_transcript=[],
        )

        runner = CliRunner()
        result = runner.invoke(voiceover_group, ["trace", "show", str(trace.path), "--json"])
        assert result.exit_code == 0
        data = jsonlib.loads(result.output)
        assert len(data) == 1
        assert data[0]["slide_id"] == "slides/1"


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
