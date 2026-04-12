"""Tests for voiceover trace log module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from clm.voiceover.trace_log import TraceLog


class TestTraceLogCreate:
    def test_creates_trace_dir(self, tmp_path):
        trace = TraceLog.create("slides_intro.py", base_dir=tmp_path)
        traces_dir = tmp_path / ".clm" / "voiceover-traces"
        assert traces_dir.exists()

    def test_log_path_contains_stem_and_timestamp(self, tmp_path):
        trace = TraceLog.create("slides_intro.py", base_dir=tmp_path)
        assert "slides_intro" in trace.path.name
        assert trace.path.suffix == ".jsonl"

    def test_log_path_in_correct_directory(self, tmp_path):
        trace = TraceLog.create("slides_test.py", base_dir=tmp_path)
        assert trace.path.parent == tmp_path / ".clm" / "voiceover-traces"


class TestTraceLogWrite:
    def test_writes_jsonl_line(self, tmp_path):
        trace = TraceLog.create("slides_test.py", base_dir=tmp_path)
        trace.log_merge_call(
            slide_id="slides_test/1",
            language="de",
            baseline="- existing",
            transcript="new content",
            llm_merged="- existing\n- new content",
            rewrites=[],
            dropped_from_transcript=["willkommen zurück"],
        )

        lines = trace.path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1

        entry = json.loads(lines[0])
        assert entry["slide_id"] == "slides_test/1"
        assert entry["language"] == "de"
        assert entry["baseline"] == "- existing"
        assert entry["transcript"] == "new content"
        assert entry["llm_merged"] == "- existing\n- new content"
        assert entry["rewrites"] == []
        assert entry["dropped_from_transcript"] == ["willkommen zurück"]

    def test_multiple_writes_append(self, tmp_path):
        trace = TraceLog.create("slides_test.py", base_dir=tmp_path)

        for i in range(3):
            trace.log_merge_call(
                slide_id=f"slides_test/{i}",
                language="en",
                baseline=f"- baseline {i}",
                transcript=f"transcript {i}",
                llm_merged=f"- merged {i}",
                rewrites=[],
                dropped_from_transcript=[],
            )

        lines = trace.path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3

        for i, line in enumerate(lines):
            entry = json.loads(line)
            assert entry["slide_id"] == f"slides_test/{i}"

    def test_includes_timestamp(self, tmp_path):
        trace = TraceLog.create("slides_test.py", base_dir=tmp_path)
        trace.log_merge_call(
            slide_id="s/1",
            language="en",
            baseline="",
            transcript="x",
            llm_merged="- x",
            rewrites=[],
            dropped_from_transcript=[],
        )

        entry = json.loads(trace.path.read_text(encoding="utf-8").strip())
        assert "timestamp" in entry
        assert "T" in entry["timestamp"]  # ISO format

    def test_includes_slide_file(self, tmp_path):
        trace = TraceLog.create("slides_intro.py", base_dir=tmp_path)
        trace.log_merge_call(
            slide_id="s/1",
            language="en",
            baseline="",
            transcript="x",
            llm_merged="- x",
            rewrites=[],
            dropped_from_transcript=[],
        )

        entry = json.loads(trace.path.read_text(encoding="utf-8").strip())
        assert entry["slide_file"] == "slides_intro.py"

    def test_includes_git_head(self, tmp_path):
        with patch("clm.voiceover.trace_log._get_git_head", return_value="abc123"):
            trace = TraceLog.create("slides_test.py", base_dir=tmp_path)

        trace.log_merge_call(
            slide_id="s/1",
            language="en",
            baseline="",
            transcript="x",
            llm_merged="- x",
            rewrites=[],
            dropped_from_transcript=[],
        )

        entry = json.loads(trace.path.read_text(encoding="utf-8").strip())
        assert entry["git_head"] == "abc123"

    def test_git_head_none_when_not_in_repo(self, tmp_path):
        with patch("clm.voiceover.trace_log._get_git_head", return_value=None):
            trace = TraceLog.create("slides_test.py", base_dir=tmp_path)

        trace.log_merge_call(
            slide_id="s/1",
            language="en",
            baseline="",
            transcript="x",
            llm_merged="- x",
            rewrites=[],
            dropped_from_transcript=[],
        )

        entry = json.loads(trace.path.read_text(encoding="utf-8").strip())
        assert entry["git_head"] is None

    def test_langfuse_trace_id_included_when_set(self, tmp_path):
        trace = TraceLog.create("slides_test.py", base_dir=tmp_path)
        trace.log_merge_call(
            slide_id="s/1",
            language="en",
            baseline="",
            transcript="x",
            llm_merged="- x",
            rewrites=[],
            dropped_from_transcript=[],
            langfuse_trace_id="lf-abc123",
        )

        entry = json.loads(trace.path.read_text(encoding="utf-8").strip())
        assert entry["langfuse_trace_id"] == "lf-abc123"

    def test_langfuse_trace_id_omitted_when_none(self, tmp_path):
        trace = TraceLog.create("slides_test.py", base_dir=tmp_path)
        trace.log_merge_call(
            slide_id="s/1",
            language="en",
            baseline="",
            transcript="x",
            llm_merged="- x",
            rewrites=[],
            dropped_from_transcript=[],
        )

        entry = json.loads(trace.path.read_text(encoding="utf-8").strip())
        assert "langfuse_trace_id" not in entry

    def test_rewrites_structure(self, tmp_path):
        trace = TraceLog.create("slides_test.py", base_dir=tmp_path)
        trace.log_merge_call(
            slide_id="s/1",
            language="en",
            baseline="- wrong fact",
            transcript="corrected",
            llm_merged="- corrected fact",
            rewrites=[
                {
                    "original": "- wrong fact",
                    "revised": "- corrected fact",
                    "transcript_evidence": "the trainer corrected this",
                }
            ],
            dropped_from_transcript=["hello", "goodbye"],
        )

        entry = json.loads(trace.path.read_text(encoding="utf-8").strip())
        assert len(entry["rewrites"]) == 1
        assert entry["rewrites"][0]["original"] == "- wrong fact"
        assert len(entry["dropped_from_transcript"]) == 2


class TestTraceLogRequiredFields:
    """Verify all required fields are present in every log entry."""

    REQUIRED_FIELDS = {
        "timestamp",
        "slide_file",
        "slide_id",
        "language",
        "baseline",
        "transcript",
        "llm_merged",
        "rewrites",
        "dropped_from_transcript",
        "git_head",
    }

    def test_all_required_fields_present(self, tmp_path):
        with patch("clm.voiceover.trace_log._get_git_head", return_value="deadbeef"):
            trace = TraceLog.create("slides_test.py", base_dir=tmp_path)

        trace.log_merge_call(
            slide_id="s/1",
            language="de",
            baseline="- test",
            transcript="test transcript",
            llm_merged="- merged test",
            rewrites=[],
            dropped_from_transcript=[],
        )

        entry = json.loads(trace.path.read_text(encoding="utf-8").strip())
        missing = self.REQUIRED_FIELDS - set(entry.keys())
        assert not missing, f"Missing required fields: {missing}"
