"""Tests for the recording processing pipeline.

These tests cover logic that doesn't require external binaries
(ffmpeg, deepFilter). Integration tests with real binaries are
marked with @pytest.mark.recordings.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from clm.recordings.processing.pipeline import ProcessingPipeline, ProcessingResult


class TestLoudnormParsing:
    """Test parsing of FFmpeg loudnorm measurement output."""

    def test_parses_typical_output(self):
        output = textwrap.dedent("""\
            [Parsed_loudnorm_2 @ 0x55f4c8]
            {
                "input_i" : "-24.50",
                "input_tp" : "-3.20",
                "input_lra" : "8.10",
                "input_thresh" : "-35.00",
                "output_i" : "-16.00",
                "output_tp" : "-1.50",
                "output_lra" : "7.80",
                "output_thresh" : "-26.50",
                "normalization_type" : "dynamic",
                "target_offset" : "0.00"
            }
        """)
        result = ProcessingPipeline._parse_loudnorm_json(output)
        assert result is not None
        assert result["input_i"] == "-24.50"
        assert result["input_tp"] == "-3.20"
        assert result["input_lra"] == "8.10"
        assert result["input_thresh"] == "-35.00"
        # Should not include output values.
        assert "output_i" not in result

    def test_parses_with_surrounding_noise(self):
        output = (
            "frame= 0 fps=0.0 q=0.0 size= 0kB time=00:00:00.00\n"
            "lots of other ffmpeg output here\n"
            "{\n"
            '    "input_i" : "-20.00",\n'
            '    "input_tp" : "-1.00",\n'
            '    "input_lra" : "5.00",\n'
            '    "input_thresh" : "-30.00",\n'
            '    "output_i" : "-16.00",\n'
            '    "output_tp" : "-1.50",\n'
            '    "output_lra" : "4.50",\n'
            '    "output_thresh" : "-26.50",\n'
            '    "normalization_type" : "dynamic",\n'
            '    "target_offset" : "0.00"\n'
            "}\n"
            "more output after\n"
        )
        result = ProcessingPipeline._parse_loudnorm_json(output)
        assert result is not None
        assert result["input_i"] == "-20.00"

    def test_returns_none_for_empty_output(self):
        assert ProcessingPipeline._parse_loudnorm_json("") is None

    def test_returns_none_for_no_json(self):
        output = "frame= 100 fps=50.0 q=0.0 size= 1234kB\n"
        assert ProcessingPipeline._parse_loudnorm_json(output) is None

    def test_returns_none_for_incomplete_json(self):
        output = '{"input_i": "-24.50", "input_tp": "-3.20"}'
        assert ProcessingPipeline._parse_loudnorm_json(output) is None


class TestProcessingResult:
    def test_model_dump(self):
        r = ProcessingResult(
            input_file=Path("/in/test.mkv"),
            output_file=Path("/out/test.mp4"),
            success=True,
            duration_seconds=120.5,
        )
        d = r.model_dump()
        assert d["success"] is True
        assert d["duration_seconds"] == 120.5
        assert d["error"] is None

    def test_failed_result(self):
        r = ProcessingResult(
            input_file=Path("/in/test.mkv"),
            output_file=Path("/out/test.mp4"),
            success=False,
            error="Something went wrong",
        )
        assert not r.success
        assert r.error == "Something went wrong"
        assert r.duration_seconds == 0.0
