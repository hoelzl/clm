"""Tests for voiceover training data extraction module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from clm.voiceover.training_export import (
    TraceEntry,
    TrainingTriple,
    _compute_delta,
    _read_voiceover_for_slide,
    extract_training_data,
    read_trace_log,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SLIDE_FILE_CONTENT = """\
# j2 from 'macros.j2' import header
# {{ header("Test", "Test") }}

# %% [markdown] lang="de" tags=["slide"]
# ## Slide 1 Title

# %% [markdown] lang="de" tags=["voiceover"]
# - Existing bullet from hand edit
# - Another edited bullet

# %% [markdown] lang="de" tags=["slide"]
# ## Slide 2 Title

# %% [markdown] lang="de" tags=["voiceover"]
# - Slide 2 voiceover after editing

# %% [markdown] lang="de" tags=["slide"]
# ## Slide 3 No Voiceover
"""


def _make_trace_entry(
    slide_id: str = "slides_test/1",
    baseline: str = "- original baseline",
    transcript: str = "the trainer said something new",
    llm_merged: str = "- original baseline\n- something new",
    git_head: str | None = "abc123",
    **kwargs,
) -> dict:
    """Build a trace log entry dict."""
    entry = {
        "timestamp": "2026-04-12T01:20:20Z",
        "slide_file": "slides_test.py",
        "slide_id": slide_id,
        "language": "de",
        "baseline": baseline,
        "transcript": transcript,
        "llm_merged": llm_merged,
        "rewrites": [],
        "dropped_from_transcript": [],
        "git_head": git_head,
    }
    entry.update(kwargs)
    return entry


def _write_trace_log(tmp_path: Path, entries: list[dict]) -> Path:
    """Write a JSONL trace log and return its path."""
    # Simulate the .clm/voiceover-traces/ directory structure
    traces_dir = tmp_path / ".clm" / "voiceover-traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    log_path = traces_dir / "slides_test-20260412-012020.jsonl"
    lines = [json.dumps(e, ensure_ascii=False) for e in entries]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log_path


# ---------------------------------------------------------------------------
# read_trace_log
# ---------------------------------------------------------------------------


class TestReadTraceLog:
    def test_reads_single_entry(self, tmp_path):
        entry = _make_trace_entry()
        log_path = _write_trace_log(tmp_path, [entry])

        entries = read_trace_log(log_path)
        assert len(entries) == 1
        assert entries[0].slide_id == "slides_test/1"
        assert entries[0].language == "de"
        assert entries[0].baseline == "- original baseline"

    def test_reads_multiple_entries(self, tmp_path):
        entries_data = [
            _make_trace_entry(slide_id="slides_test/1"),
            _make_trace_entry(slide_id="slides_test/2"),
            _make_trace_entry(slide_id="slides_test/3"),
        ]
        log_path = _write_trace_log(tmp_path, entries_data)

        entries = read_trace_log(log_path)
        assert len(entries) == 3
        assert [e.slide_id for e in entries] == [
            "slides_test/1",
            "slides_test/2",
            "slides_test/3",
        ]

    def test_skips_malformed_lines(self, tmp_path):
        traces_dir = tmp_path / ".clm" / "voiceover-traces"
        traces_dir.mkdir(parents=True, exist_ok=True)
        log_path = traces_dir / "bad.jsonl"
        log_path.write_text(
            json.dumps(_make_trace_entry()) + "\n"
            "not valid json\n" + json.dumps(_make_trace_entry(slide_id="slides_test/2")) + "\n",
            encoding="utf-8",
        )

        entries = read_trace_log(log_path)
        assert len(entries) == 2

    def test_skips_empty_lines(self, tmp_path):
        traces_dir = tmp_path / ".clm" / "voiceover-traces"
        traces_dir.mkdir(parents=True, exist_ok=True)
        log_path = traces_dir / "empty.jsonl"
        log_path.write_text(
            json.dumps(_make_trace_entry())
            + "\n\n\n"
            + json.dumps(_make_trace_entry(slide_id="slides_test/2"))
            + "\n",
            encoding="utf-8",
        )

        entries = read_trace_log(log_path)
        assert len(entries) == 2

    def test_preserves_langfuse_trace_id(self, tmp_path):
        entry = _make_trace_entry(langfuse_trace_id="lf-xyz")
        log_path = _write_trace_log(tmp_path, [entry])

        entries = read_trace_log(log_path)
        assert entries[0].langfuse_trace_id == "lf-xyz"

    def test_handles_missing_optional_fields(self, tmp_path):
        # Minimal entry with only required fields
        minimal = {
            "slide_file": "slides.py",
            "slide_id": "slides/1",
            "language": "en",
            "baseline": "",
            "transcript": "text",
            "llm_merged": "- text",
        }
        log_path = _write_trace_log(tmp_path, [minimal])

        entries = read_trace_log(log_path)
        assert len(entries) == 1
        assert entries[0].git_head is None
        assert entries[0].rewrites == []
        assert entries[0].dropped_from_transcript == []
        assert entries[0].langfuse_trace_id is None

    def test_preserves_rewrites_structure(self, tmp_path):
        entry = _make_trace_entry(
            rewrites=[
                {
                    "original": "- wrong",
                    "revised": "- correct",
                    "transcript_evidence": "actually...",
                }
            ],
            dropped_from_transcript=["willkommen zurück"],
        )
        log_path = _write_trace_log(tmp_path, [entry])

        entries = read_trace_log(log_path)
        assert len(entries[0].rewrites) == 1
        assert entries[0].rewrites[0]["original"] == "- wrong"
        assert entries[0].dropped_from_transcript == ["willkommen zurück"]


# ---------------------------------------------------------------------------
# _compute_delta
# ---------------------------------------------------------------------------


class TestComputeDelta:
    def test_identical_returns_empty(self):
        assert _compute_delta("- bullet one\n- bullet two", "- bullet one\n- bullet two") == ""

    def test_identical_ignoring_trailing_whitespace(self):
        assert _compute_delta("- bullet one\n- bullet two  ", "- bullet one\n- bullet two") == ""

    def test_different_returns_unified_diff(self):
        delta = _compute_delta("- old bullet", "- new bullet")
        assert delta != ""
        assert "--- llm_output" in delta
        assert "+++ human_final" in delta
        assert "- old bullet" in delta or "-- old bullet" in delta
        assert "+ new bullet" in delta or "+- new bullet" in delta

    def test_addition_in_diff(self):
        delta = _compute_delta("- bullet one", "- bullet one\n- added bullet")
        assert delta != ""
        assert "added bullet" in delta

    def test_empty_strings_identical(self):
        assert _compute_delta("", "") == ""


# ---------------------------------------------------------------------------
# _read_voiceover_for_slide
# ---------------------------------------------------------------------------


class TestReadVoiceoverForSlide:
    def test_reads_existing_voiceover(self, tmp_path):
        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text(SLIDE_FILE_CONTENT, encoding="utf-8")

        text = _read_voiceover_for_slide(slide_file, "slides_test/1", "de")
        assert text is not None
        assert "Existing bullet from hand edit" in text

    def test_returns_none_for_missing_file(self, tmp_path):
        slide_file = tmp_path / "nonexistent.py"
        result = _read_voiceover_for_slide(slide_file, "s/1", "de")
        assert result is None

    def test_returns_none_for_missing_slide(self, tmp_path):
        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text(SLIDE_FILE_CONTENT, encoding="utf-8")

        # Slide index 99 doesn't exist
        result = _read_voiceover_for_slide(slide_file, "slides_test/99", "de")
        assert result is None

    def test_returns_empty_for_slide_without_voiceover(self, tmp_path):
        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text(SLIDE_FILE_CONTENT, encoding="utf-8")

        # Slide 3 has no voiceover cell
        result = _read_voiceover_for_slide(slide_file, "slides_test/3", "de")
        assert result is not None
        assert result == ""

    def test_reads_correct_slide_by_index(self, tmp_path):
        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text(SLIDE_FILE_CONTENT, encoding="utf-8")

        text = _read_voiceover_for_slide(slide_file, "slides_test/2", "de")
        assert text is not None
        assert "Slide 2 voiceover" in text

    def test_invalid_slide_id_format(self, tmp_path):
        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text(SLIDE_FILE_CONTENT, encoding="utf-8")

        result = _read_voiceover_for_slide(slide_file, "invalid", "de")
        assert result is None


# ---------------------------------------------------------------------------
# TrainingTriple.to_dict
# ---------------------------------------------------------------------------


class TestTrainingTripleToDict:
    def test_serializes_all_fields(self):
        triple = TrainingTriple(
            slide_file="slides.py",
            slide_id="slides/1",
            language="de",
            input_baseline="- baseline",
            input_transcript="transcript text",
            llm_output="- merged",
            human_final="- hand edited",
            delta_vs_llm="--- a\n+++ b\n",
            rewrites=[{"original": "x", "revised": "y"}],
            dropped_from_transcript=["hello"],
            git_head="abc123",
            timestamp="2026-04-12T01:20:20Z",
        )
        d = triple.to_dict()
        assert d["slide_file"] == "slides.py"
        assert d["input"]["baseline"] == "- baseline"
        assert d["input"]["transcript"] == "transcript text"
        assert d["llm_output"] == "- merged"
        assert d["human_final"] == "- hand edited"
        assert d["delta_vs_llm"] == "--- a\n+++ b\n"
        assert len(d["rewrites"]) == 1
        assert d["git_head"] == "abc123"

    def test_json_serializable(self):
        triple = TrainingTriple(
            slide_file="s.py",
            slide_id="s/1",
            language="en",
            input_baseline="",
            input_transcript="t",
            llm_output="- t",
            human_final="- t",
            delta_vs_llm="",
        )
        # Should not raise
        json.dumps(triple.to_dict(), ensure_ascii=False)

    def test_positive_example_has_empty_delta(self):
        triple = TrainingTriple(
            slide_file="s.py",
            slide_id="s/1",
            language="en",
            input_baseline="",
            input_transcript="t",
            llm_output="- t",
            human_final="- t",
            delta_vs_llm="",
        )
        d = triple.to_dict()
        assert d["delta_vs_llm"] == ""


# ---------------------------------------------------------------------------
# extract_training_data (integration)
# ---------------------------------------------------------------------------


class TestExtractTrainingData:
    def test_basic_extraction(self, tmp_path):
        """Normal case: trace entry matches slide file, human edited."""
        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text(SLIDE_FILE_CONTENT, encoding="utf-8")

        entry = _make_trace_entry(
            slide_id="slides_test/1",
            llm_merged="- LLM produced this",
        )
        log_path = _write_trace_log(tmp_path, [entry])

        with patch(
            "clm.voiceover.training_export._git_commit_exists",
            return_value=True,
        ):
            triples = extract_training_data(log_path, base_dir=tmp_path)

        assert len(triples) == 1
        assert triples[0].slide_id == "slides_test/1"
        assert triples[0].llm_output == "- LLM produced this"
        assert "Existing bullet from hand edit" in triples[0].human_final
        # LLM output != human final, so delta should be non-empty
        assert triples[0].delta_vs_llm != ""

    def test_positive_example_no_edits(self, tmp_path):
        """human_final == llm_output → empty delta_vs_llm."""
        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text(SLIDE_FILE_CONTENT, encoding="utf-8")

        # Read what the parser will return for slide 2
        from clm.notebooks.slide_parser import parse_slides

        groups = parse_slides(slide_file, "de")
        slide2_text = ""
        for sg in groups:
            if sg.index == 2:
                for cell in sg.notes_cells:
                    if "voiceover" in cell.metadata.tags:
                        slide2_text = cell.text_content()
                break

        entry = _make_trace_entry(
            slide_id="slides_test/2",
            llm_merged=slide2_text,
        )
        log_path = _write_trace_log(tmp_path, [entry])

        with patch(
            "clm.voiceover.training_export._git_commit_exists",
            return_value=True,
        ):
            triples = extract_training_data(log_path, base_dir=tmp_path)

        assert len(triples) == 1
        assert triples[0].delta_vs_llm == ""

    def test_skips_unreachable_git_head(self, tmp_path):
        """Entries with unreachable git_head are skipped with warning."""
        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text(SLIDE_FILE_CONTENT, encoding="utf-8")

        entry = _make_trace_entry(git_head="deadbeef123")
        log_path = _write_trace_log(tmp_path, [entry])

        with patch(
            "clm.voiceover.training_export._git_commit_exists",
            return_value=False,
        ):
            triples = extract_training_data(log_path, base_dir=tmp_path)

        assert len(triples) == 0

    def test_no_check_git_skips_validation(self, tmp_path):
        """check_git_head=False bypasses the reachability check."""
        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text(SLIDE_FILE_CONTENT, encoding="utf-8")

        entry = _make_trace_entry(git_head="deadbeef123")
        log_path = _write_trace_log(tmp_path, [entry])

        # Don't mock _git_commit_exists — it should not be called
        triples = extract_training_data(log_path, base_dir=tmp_path, check_git_head=False)

        assert len(triples) == 1

    def test_skips_missing_slide_file(self, tmp_path):
        """Entries whose slide file doesn't exist are skipped."""
        entry = _make_trace_entry(slide_id="slides_test/1")
        log_path = _write_trace_log(tmp_path, [entry])
        # No slide file written to tmp_path

        with patch(
            "clm.voiceover.training_export._git_commit_exists",
            return_value=True,
        ):
            triples = extract_training_data(log_path, base_dir=tmp_path)

        assert len(triples) == 0

    def test_skips_missing_slide_in_file(self, tmp_path):
        """Entries for a slide index not present in the file are skipped."""
        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text(SLIDE_FILE_CONTENT, encoding="utf-8")

        entry = _make_trace_entry(slide_id="slides_test/99")
        log_path = _write_trace_log(tmp_path, [entry])

        with patch(
            "clm.voiceover.training_export._git_commit_exists",
            return_value=True,
        ):
            triples = extract_training_data(log_path, base_dir=tmp_path)

        assert len(triples) == 0

    def test_multiple_entries(self, tmp_path):
        """Multiple trace entries produce multiple triples."""
        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text(SLIDE_FILE_CONTENT, encoding="utf-8")

        entries = [
            _make_trace_entry(slide_id="slides_test/1", llm_merged="- llm slide 1"),
            _make_trace_entry(slide_id="slides_test/2", llm_merged="- llm slide 2"),
        ]
        log_path = _write_trace_log(tmp_path, entries)

        with patch(
            "clm.voiceover.training_export._git_commit_exists",
            return_value=True,
        ):
            triples = extract_training_data(log_path, base_dir=tmp_path)

        assert len(triples) == 2
        assert triples[0].slide_id == "slides_test/1"
        assert triples[1].slide_id == "slides_test/2"

    def test_null_git_head_not_checked(self, tmp_path):
        """Entries with git_head=None are not checked for reachability."""
        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text(SLIDE_FILE_CONTENT, encoding="utf-8")

        entry = _make_trace_entry(git_head=None)
        log_path = _write_trace_log(tmp_path, [entry])

        # check_git_head=True but git_head is None → should not call _git_commit_exists
        with patch(
            "clm.voiceover.training_export._git_commit_exists",
        ) as mock_check:
            triples = extract_training_data(log_path, base_dir=tmp_path)

        mock_check.assert_not_called()
        assert len(triples) == 1

    def test_default_base_dir_from_trace_path(self, tmp_path):
        """base_dir defaults to trace log's grandparent (project root)."""
        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text(SLIDE_FILE_CONTENT, encoding="utf-8")

        entry = _make_trace_entry()
        log_path = _write_trace_log(tmp_path, [entry])

        with patch(
            "clm.voiceover.training_export._git_commit_exists",
            return_value=True,
        ):
            # Don't pass base_dir — should infer from trace log path
            triples = extract_training_data(log_path)

        assert len(triples) == 1

    def test_preserves_metadata_in_triples(self, tmp_path):
        """Rewrites, dropped phrases, and timestamps are preserved."""
        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text(SLIDE_FILE_CONTENT, encoding="utf-8")

        entry = _make_trace_entry(
            rewrites=[{"original": "- x", "revised": "- y"}],
            dropped_from_transcript=["willkommen"],
            timestamp="2026-04-12T01:20:20Z",
        )
        log_path = _write_trace_log(tmp_path, [entry])

        with patch(
            "clm.voiceover.training_export._git_commit_exists",
            return_value=True,
        ):
            triples = extract_training_data(log_path, base_dir=tmp_path)

        assert len(triples) == 1
        assert triples[0].rewrites == [{"original": "- x", "revised": "- y"}]
        assert triples[0].dropped_from_transcript == ["willkommen"]
        assert triples[0].timestamp == "2026-04-12T01:20:20Z"

    def test_slide_without_voiceover_gives_empty_human_final(self, tmp_path):
        """A slide with no voiceover cell produces empty human_final."""
        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text(SLIDE_FILE_CONTENT, encoding="utf-8")

        entry = _make_trace_entry(
            slide_id="slides_test/3",
            llm_merged="- something the LLM wrote",
        )
        log_path = _write_trace_log(tmp_path, [entry])

        with patch(
            "clm.voiceover.training_export._git_commit_exists",
            return_value=True,
        ):
            triples = extract_training_data(log_path, base_dir=tmp_path)

        assert len(triples) == 1
        assert triples[0].human_final == ""
        # LLM wrote something but human removed it → non-empty delta
        assert triples[0].delta_vs_llm != ""


# ---------------------------------------------------------------------------
# CLI subcommand
# ---------------------------------------------------------------------------


class TestExtractTrainingDataCLI:
    def test_help_text(self):
        from click.testing import CliRunner

        from clm.cli.commands.voiceover import voiceover_group

        runner = CliRunner()
        result = runner.invoke(voiceover_group, ["extract-training-data", "--help"])
        assert result.exit_code == 0
        assert "training" in result.output.lower()
        assert "--no-check-git" in result.output
        assert "--tag" in result.output

    def test_basic_invocation(self, tmp_path):
        from click.testing import CliRunner

        from clm.cli.commands.voiceover import voiceover_group

        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text(SLIDE_FILE_CONTENT, encoding="utf-8")

        entry = _make_trace_entry(llm_merged="- from LLM")
        log_path = _write_trace_log(tmp_path, [entry])

        runner = CliRunner()
        with patch(
            "clm.voiceover.training_export._git_commit_exists",
            return_value=True,
        ):
            result = runner.invoke(
                voiceover_group,
                [
                    "extract-training-data",
                    str(log_path),
                    "--base-dir",
                    str(tmp_path),
                    "--no-check-git",
                ],
            )

        assert result.exit_code == 0
        # Output should contain JSON
        output_lines = [line for line in result.output.strip().split("\n") if line.startswith("{")]
        assert len(output_lines) == 1
        data = json.loads(output_lines[0])
        assert data["slide_id"] == "slides_test/1"
        assert "input" in data
        assert "llm_output" in data

    def test_output_to_file(self, tmp_path):
        from click.testing import CliRunner

        from clm.cli.commands.voiceover import voiceover_group

        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text(SLIDE_FILE_CONTENT, encoding="utf-8")

        entry = _make_trace_entry(llm_merged="- from LLM")
        log_path = _write_trace_log(tmp_path, [entry])
        output_file = tmp_path / "output.jsonl"

        runner = CliRunner()
        result = runner.invoke(
            voiceover_group,
            [
                "extract-training-data",
                str(log_path),
                "--base-dir",
                str(tmp_path),
                "--no-check-git",
                "-o",
                str(output_file),
            ],
        )

        assert result.exit_code == 0
        assert output_file.exists()
        data = json.loads(output_file.read_text(encoding="utf-8").strip())
        assert data["slide_id"] == "slides_test/1"

    def test_no_results_message(self, tmp_path):
        from click.testing import CliRunner

        from clm.cli.commands.voiceover import voiceover_group

        # Trace log referencing a slide file that doesn't exist
        entry = _make_trace_entry()
        log_path = _write_trace_log(tmp_path, [entry])

        runner = CliRunner()
        result = runner.invoke(
            voiceover_group,
            [
                "extract-training-data",
                str(log_path),
                "--base-dir",
                str(tmp_path),
                "--no-check-git",
            ],
        )

        assert result.exit_code == 0
        assert "No training triples" in result.output
