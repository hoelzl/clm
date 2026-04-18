"""Tests for the ``clm voiceover`` CLI command group.

These tests focus on the command layer itself (argument parsing, option
handling, flow control). The voiceover backend implementations (Whisper,
Cohere, Granite, OCR, LLM merge) are stubbed via ``sys.modules`` so the
tests run fast and do not require the ``[voiceover]`` extra dependencies.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from click.testing import CliRunner

from clm.cli.commands import voiceover as voiceover_module
from clm.cli.commands.voiceover import (
    _display_merge_summary,
    _display_notes_summary,
    _emit_dry_run_diff,
    _extract_baseline,
    _get_git_user_name,
    _has_boundary,
    _parse_range,
    _polish_notes,
    voiceover_group,
)

# ---------------------------------------------------------------------------
# Pure helpers — easy targets for unit tests.
# ---------------------------------------------------------------------------


class TestParseRange:
    def test_single_number(self):
        assert _parse_range("7") == (7, 7)

    def test_pair(self):
        assert _parse_range("5-12") == (5, 12)


class TestExtractBaseline:
    def _cell(self, tags: list[str], text: str):
        cell = MagicMock()
        cell.metadata.tags = tags
        cell.text_content = MagicMock(return_value=text)
        return cell

    def test_extracts_tagged_cells_only(self):
        sg = MagicMock()
        sg.notes_cells = [
            self._cell(["voiceover"], "voice line"),
            self._cell(["notes"], "notes line"),
        ]
        assert _extract_baseline(sg, tag="voiceover") == "voice line"
        assert _extract_baseline(sg, tag="notes") == "notes line"

    def test_joins_multiple_cells_with_newlines(self):
        sg = MagicMock()
        sg.notes_cells = [
            self._cell(["voiceover"], "first"),
            self._cell(["voiceover"], "second"),
        ]
        assert _extract_baseline(sg, tag="voiceover") == "first\nsecond"

    def test_ignores_empty_text(self):
        sg = MagicMock()
        sg.notes_cells = [
            self._cell(["voiceover"], ""),
            self._cell(["voiceover"], "actual"),
        ]
        assert _extract_baseline(sg, tag="voiceover") == "actual"

    def test_no_matching_tag_returns_empty(self):
        sg = MagicMock()
        sg.notes_cells = [self._cell(["notes"], "x")]
        assert _extract_baseline(sg, tag="voiceover") == ""


class TestHasBoundary:
    def test_returns_false_when_slide_not_in_alignment(self):
        alignment = MagicMock(slide_notes={})
        assert _has_boundary(alignment, 5) is False

    def test_returns_true_when_slide_present(self):
        alignment = MagicMock(slide_notes={5: []})
        assert _has_boundary(alignment, 5) is True


class TestGetGitUserName:
    def test_returns_name_on_success(self, monkeypatch: pytest.MonkeyPatch):
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = "Jane Doe\n"
        fake_run = MagicMock(return_value=fake_result)
        monkeypatch.setattr(voiceover_module, "__name__", voiceover_module.__name__)
        # The helper imports subprocess locally — patch globally.
        import subprocess

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert _get_git_user_name() == "Jane Doe"

    def test_returns_none_when_git_missing(self, monkeypatch: pytest.MonkeyPatch):
        import subprocess

        def raise_fnf(*_a, **_kw):
            raise FileNotFoundError

        monkeypatch.setattr(subprocess, "run", raise_fnf)
        assert _get_git_user_name() is None

    def test_returns_none_on_timeout(self, monkeypatch: pytest.MonkeyPatch):
        import subprocess

        def raise_timeout(*_a, **_kw):
            raise subprocess.TimeoutExpired(cmd="git", timeout=5)

        monkeypatch.setattr(subprocess, "run", raise_timeout)
        assert _get_git_user_name() is None

    def test_returns_none_on_nonzero_exit(self, monkeypatch: pytest.MonkeyPatch):
        import subprocess

        fake_result = MagicMock(returncode=1, stdout="")
        monkeypatch.setattr(subprocess, "run", MagicMock(return_value=fake_result))
        assert _get_git_user_name() is None

    def test_returns_none_on_empty_name(self, monkeypatch: pytest.MonkeyPatch):
        import subprocess

        fake_result = MagicMock(returncode=0, stdout="   \n")
        monkeypatch.setattr(subprocess, "run", MagicMock(return_value=fake_result))
        assert _get_git_user_name() is None


class TestDisplaySummaries:
    """Smoke tests for the display helpers — they should not raise."""

    def test_display_notes_summary_empty_map(self):
        _display_notes_summary({}, [])

    def test_display_notes_summary_with_data(self):
        sg = MagicMock()
        sg.index = 1
        sg.title = "Intro"
        _display_notes_summary({1: "body"}, [sg])

    def test_display_merge_summary_empty(self):
        _display_merge_summary([], [])

    def test_display_merge_summary_with_rewrites(self):
        result = MagicMock()
        result.slide_id = "deck/3"
        result.merged_bullets = "merged body " * 10
        result.rewrites = [{"original": "a", "revised": "b"}]
        sg = MagicMock()
        sg.index = 3
        sg.title = "Slide 3"
        _display_merge_summary([result], [sg])

    def test_display_merge_summary_bad_slide_id(self):
        result = MagicMock()
        result.slide_id = "no-slash-no-number"
        result.merged_bullets = "text"
        result.rewrites = []
        _display_merge_summary([result], [])


class TestPolishNotes:
    @pytest.mark.asyncio
    async def test_polishes_each_slide(self, monkeypatch):
        sg1 = MagicMock()
        sg1.index = 1
        sg1.text_content = "content 1"
        sg2 = MagicMock()
        sg2.index = 2
        sg2.text_content = "content 2"

        async def fake_polish(text, content, **_):
            return text.upper()

        fake_polish_module = MagicMock()
        fake_polish_module.polish_text = fake_polish
        monkeypatch.setitem(sys.modules, "clm.notebooks.polish", fake_polish_module)

        notes = {1: "alpha", 2: "beta"}
        result = await _polish_notes(notes, [sg1, sg2], lang="de")

        assert result == {1: "ALPHA", 2: "BETA"}

    @pytest.mark.asyncio
    async def test_model_kwarg_forwarded(self, monkeypatch):
        captured_kwargs: list[dict] = []

        sg = MagicMock()
        sg.index = 1
        sg.text_content = "c"

        async def fake_polish(text, content, **kwargs):
            captured_kwargs.append(kwargs)
            return text

        fake_polish_module = MagicMock()
        fake_polish_module.polish_text = fake_polish
        monkeypatch.setitem(sys.modules, "clm.notebooks.polish", fake_polish_module)

        await _polish_notes({1: "hello"}, [sg], model="my-model", lang="en")
        assert captured_kwargs == [{"model": "my-model"}]


class TestEmitDryRunDiff:
    def test_no_change_short_circuits(self, tmp_path: Path, monkeypatch):
        slides = tmp_path / "deck.py"
        slides.write_text("original")

        fake_writer_module = MagicMock()
        fake_writer_module.update_narrative = MagicMock(return_value="original")
        monkeypatch.setitem(sys.modules, "clm.notebooks.slide_writer", fake_writer_module)

        # Should not raise; produces "No changes" message via console.
        _emit_dry_run_diff(slides, {1: "x"}, "de", "voiceover", [])

    def test_diff_output_and_rewrite_warning(self, tmp_path: Path, monkeypatch):
        slides = tmp_path / "deck.py"
        slides.write_text("line one\nline two\n")

        fake_writer_module = MagicMock()
        fake_writer_module.update_narrative = MagicMock(return_value="line one\nupdated two\n")
        monkeypatch.setitem(sys.modules, "clm.notebooks.slide_writer", fake_writer_module)

        result = MagicMock()
        result.slide_id = "deck/1"
        result.rewrites = [{"original": "foo", "revised": "bar"}]

        # Just verify it runs without error.
        _emit_dry_run_diff(slides, {1: "x"}, "de", "voiceover", [result])


# ---------------------------------------------------------------------------
# Top-level group help.
# ---------------------------------------------------------------------------


class TestVoiceoverGroupHelp:
    def test_group_help(self):
        runner = CliRunner()
        result = runner.invoke(voiceover_group, ["--help"])
        assert result.exit_code == 0

    def test_sync_help(self):
        runner = CliRunner()
        result = runner.invoke(voiceover_group, ["sync", "--help"])
        assert result.exit_code == 0
        assert "--mode" in result.output
        assert "--overwrite" in result.output
        assert "--whisper-model" in result.output

    def test_transcribe_help(self):
        runner = CliRunner()
        result = runner.invoke(voiceover_group, ["transcribe", "--help"])
        assert result.exit_code == 0

    def test_detect_help(self):
        runner = CliRunner()
        result = runner.invoke(voiceover_group, ["detect", "--help"])
        assert result.exit_code == 0

    def test_identify_help(self):
        runner = CliRunner()
        result = runner.invoke(voiceover_group, ["identify", "--help"])
        assert result.exit_code == 0

    def test_extract_training_data_help(self):
        runner = CliRunner()
        result = runner.invoke(voiceover_group, ["extract-training-data", "--help"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# sync command — UsageError and mode gating.
# ---------------------------------------------------------------------------


class TestSyncUsageErrors:
    def test_verbatim_without_overwrite_errors(self, tmp_path: Path):
        slides = tmp_path / "deck.py"
        slides.write_text("# slide")
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")

        runner = CliRunner()
        result = runner.invoke(
            voiceover_group,
            [
                "sync",
                str(slides),
                str(video),
                "--lang",
                "de",
                "--mode",
                "verbatim",
            ],
        )

        assert result.exit_code != 0
        assert "verbatim" in result.output.lower()


# ---------------------------------------------------------------------------
# transcribe command — stub out the backend.
# ---------------------------------------------------------------------------


def _make_transcript(language: str = "de", duration: float = 12.5, text: str = "hello world"):
    from clm.voiceover.transcribe import Transcript, TranscriptSegment

    return Transcript(
        language=language,
        duration=duration,
        segments=[TranscriptSegment(start=0.0, end=duration, text=text)],
    )


class TestTranscribeCommand:
    def test_prints_json_when_no_output(self, tmp_path: Path, monkeypatch):
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")

        fake = MagicMock(return_value=_make_transcript())
        # transcribe_video is imported at call time from clm.voiceover.transcribe;
        # stub the module entry.
        fake_module = MagicMock()
        fake_module.transcribe_video = fake
        monkeypatch.setitem(sys.modules, "clm.voiceover.transcribe", fake_module)

        runner = CliRunner()
        result = runner.invoke(
            voiceover_group,
            ["transcribe", str(video), "--lang", "de"],
        )

        assert result.exit_code == 0, result.output
        fake.assert_called_once()
        # The output contains JSON with the language field.
        assert '"language"' in result.output
        assert '"de"' in result.output

    def test_writes_output_file(self, tmp_path: Path, monkeypatch):
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")
        out_file = tmp_path / "transcript.json"

        fake = MagicMock(return_value=_make_transcript(text="bonjour"))
        fake_module = MagicMock()
        fake_module.transcribe_video = fake
        monkeypatch.setitem(sys.modules, "clm.voiceover.transcribe", fake_module)

        runner = CliRunner()
        result = runner.invoke(
            voiceover_group,
            ["transcribe", str(video), "-o", str(out_file)],
        )

        assert result.exit_code == 0, result.output
        data = json.loads(out_file.read_text(encoding="utf-8"))
        assert data["language"] == "de"
        assert data["segments"][0]["text"] == "bonjour"


# ---------------------------------------------------------------------------
# detect command — stub detect_transitions.
# ---------------------------------------------------------------------------


class TestDetectCommand:
    def test_prints_table_when_no_output(self, tmp_path: Path, monkeypatch):
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")

        event = MagicMock()
        event.timestamp = 10.0
        event.peak_diff = 0.5
        event.confidence = 0.9
        fake_detect = MagicMock(return_value=([event], []))

        fake_keyframes = MagicMock()
        fake_keyframes.detect_transitions = fake_detect
        monkeypatch.setitem(sys.modules, "clm.voiceover.keyframes", fake_keyframes)

        runner = CliRunner()
        result = runner.invoke(voiceover_group, ["detect", str(video)])

        assert result.exit_code == 0, result.output
        assert "1 transitions" in result.output

    def test_writes_json_to_output_file(self, tmp_path: Path, monkeypatch):
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")
        out = tmp_path / "events.json"

        event = MagicMock()
        event.timestamp = 5.0
        event.peak_diff = 0.1
        event.confidence = 0.8
        fake_keyframes = MagicMock()
        fake_keyframes.detect_transitions = MagicMock(return_value=([event], []))
        monkeypatch.setitem(sys.modules, "clm.voiceover.keyframes", fake_keyframes)

        runner = CliRunner()
        result = runner.invoke(voiceover_group, ["detect", str(video), "-o", str(out)])

        assert result.exit_code == 0
        data = json.loads(out.read_text())
        assert len(data) == 1
        assert data[0]["timestamp"] == 5.0


# ---------------------------------------------------------------------------
# identify command.
# ---------------------------------------------------------------------------


class TestIdentifyCommand:
    def test_writes_timeline_to_output(self, tmp_path: Path, monkeypatch):
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")
        slides = tmp_path / "deck.py"
        slides.write_text("# slide")
        out = tmp_path / "timeline.json"

        # Fake slide_parser module
        slide_mock = MagicMock()
        slide_mock.index = 1
        slide_mock.title = "Title"
        fake_parser = MagicMock()
        fake_parser.parse_slides = MagicMock(return_value=[slide_mock])
        monkeypatch.setitem(sys.modules, "clm.notebooks.slide_parser", fake_parser)

        # Fake keyframes
        fake_keyframes = MagicMock()
        fake_keyframes.detect_transitions = MagicMock(return_value=([MagicMock()], []))
        monkeypatch.setitem(sys.modules, "clm.voiceover.keyframes", fake_keyframes)

        # Fake matcher
        timeline_entry = MagicMock()
        timeline_entry.slide_index = 1
        timeline_entry.start_time = 0.0
        timeline_entry.end_time = 10.0
        timeline_entry.match_score = 90.0
        match_result = MagicMock(timeline=[timeline_entry])
        fake_matcher = MagicMock()
        fake_matcher.match_events_to_slides = MagicMock(return_value=match_result)
        monkeypatch.setitem(sys.modules, "clm.voiceover.matcher", fake_matcher)

        runner = CliRunner()
        result = runner.invoke(
            voiceover_group,
            ["identify", str(video), str(slides), "--lang", "de", "-o", str(out)],
        )

        assert result.exit_code == 0, result.output
        data = json.loads(out.read_text())
        assert data[0]["slide_index"] == 1
        assert data[0]["match_score"] == 90.0


# ---------------------------------------------------------------------------
# extract-training-data command.
# ---------------------------------------------------------------------------


class TestExtractTrainingDataCommand:
    def test_no_triples_returns_zero(self, tmp_path: Path, monkeypatch):
        trace_log = tmp_path / "trace.jsonl"
        trace_log.write_text("")

        fake_training = MagicMock()
        fake_training.extract_training_data = MagicMock(return_value=[])
        monkeypatch.setitem(sys.modules, "clm.voiceover.training_export", fake_training)

        runner = CliRunner()
        result = runner.invoke(voiceover_group, ["extract-training-data", str(trace_log)])

        assert result.exit_code == 0, result.output
        assert "No training triples" in result.output

    def test_writes_triples_to_output(self, tmp_path: Path, monkeypatch):
        trace_log = tmp_path / "trace.jsonl"
        trace_log.write_text("")
        out = tmp_path / "train.jsonl"

        triple = MagicMock()
        triple.delta_vs_llm = ""
        triple.to_dict = MagicMock(return_value={"baseline": "a", "llm_output": "b"})

        edited_triple = MagicMock()
        edited_triple.delta_vs_llm = "diff"
        edited_triple.to_dict = MagicMock(return_value={"baseline": "c", "llm_output": "d"})

        fake_training = MagicMock()
        fake_training.extract_training_data = MagicMock(return_value=[triple, edited_triple])
        monkeypatch.setitem(sys.modules, "clm.voiceover.training_export", fake_training)

        runner = CliRunner()
        result = runner.invoke(
            voiceover_group,
            ["extract-training-data", str(trace_log), "-o", str(out)],
        )

        assert result.exit_code == 0, result.output
        lines = out.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["baseline"] == "a"
        # Counts are reported.
        assert "2 training triple" in result.output
        assert "1 positive" in result.output
        assert "1 with hand edits" in result.output

    def test_no_output_and_no_output_file(self, tmp_path: Path, monkeypatch):
        trace_log = tmp_path / "trace.jsonl"
        trace_log.write_text("")

        triple = MagicMock()
        triple.delta_vs_llm = "diff"
        triple.to_dict = MagicMock(return_value={"slide_id": "a/2"})

        fake_training = MagicMock()
        fake_training.extract_training_data = MagicMock(return_value=[triple])
        monkeypatch.setitem(sys.modules, "clm.voiceover.training_export", fake_training)

        runner = CliRunner()
        result = runner.invoke(
            voiceover_group,
            ["extract-training-data", str(trace_log), "--no-check-git", "--tag", "notes"],
        )

        assert result.exit_code == 0, result.output

    def test_prints_to_stdout_when_no_output(self, tmp_path: Path, monkeypatch):
        trace_log = tmp_path / "trace.jsonl"
        trace_log.write_text("")

        triple = MagicMock()
        triple.delta_vs_llm = ""
        triple.to_dict = MagicMock(return_value={"slide_id": "a/1"})

        fake_training = MagicMock()
        fake_training.extract_training_data = MagicMock(return_value=[triple])
        monkeypatch.setitem(sys.modules, "clm.voiceover.training_export", fake_training)

        runner = CliRunner()
        result = runner.invoke(voiceover_group, ["extract-training-data", str(trace_log)])

        assert result.exit_code == 0, result.output
        assert '"slide_id": "a/1"' in result.output
