"""Unit tests for ``clm.recordings.processing.compare``.

The FFmpeg extraction is stubbed; only argv construction, base64 framing,
and HTML rendering are checked here.
"""

from __future__ import annotations

import base64
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from clm.recordings.processing import compare as compare_module
from clm.recordings.processing.compare import (
    audio_to_base64,
    extract_audio_segment,
    generate_comparison_html,
)


class TestExtractAudioSegment:
    def _fake_result(self) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    def test_argv_without_segment_flags(self, tmp_path: Path):
        with patch(
            "clm.recordings.processing.compare.run_subprocess",
            return_value=self._fake_result(),
        ) as mock_run:
            extract_audio_segment(
                ffmpeg=Path("/usr/bin/ffmpeg"),
                input_file=tmp_path / "in.mp4",
                output_file=tmp_path / "out.wav",
                start_seconds=0,
                duration_seconds=0,
            )

        args, _ = mock_run.call_args
        argv = args[0]
        assert argv[0] == Path("/usr/bin/ffmpeg")
        assert "-i" in argv
        # Without start/duration, -ss and -t must be absent.
        assert "-ss" not in argv
        assert "-t" not in argv
        # WAV output codec is always set.
        assert "pcm_s16le" in argv
        assert argv[-1] == tmp_path / "out.wav"

    def test_argv_with_segment_flags(self, tmp_path: Path):
        with patch(
            "clm.recordings.processing.compare.run_subprocess",
            return_value=self._fake_result(),
        ) as mock_run:
            extract_audio_segment(
                ffmpeg=Path("/usr/bin/ffmpeg"),
                input_file=tmp_path / "in.mp4",
                output_file=tmp_path / "out.wav",
                start_seconds=15,
                duration_seconds=30,
            )

        args, _ = mock_run.call_args
        argv = args[0]
        assert "-ss" in argv
        assert argv[argv.index("-ss") + 1] == "15"
        assert "-t" in argv
        assert argv[argv.index("-t") + 1] == "30"


class TestAudioToBase64:
    def test_encodes_round_trip(self, tmp_path: Path):
        raw = b"RIFF\x00fake-wav\xff\xaa"
        f = tmp_path / "clip.wav"
        f.write_bytes(raw)

        encoded = audio_to_base64(f)
        assert isinstance(encoded, str)
        assert base64.b64decode(encoded) == raw

    def test_empty_file(self, tmp_path: Path):
        f = tmp_path / "empty.wav"
        f.write_bytes(b"")
        assert audio_to_base64(f) == ""


class TestGenerateComparisonHtml:
    def test_without_original(self):
        html = generate_comparison_html(
            original_b64=None,
            version_a_b64="AAAA",
            version_b_b64="BBBB",
            label_a="iZotope RX",
            label_b="DeepFilterNet",
        )
        assert "<!DOCTYPE html>" in html
        assert "iZotope RX" in html
        assert "DeepFilterNet" in html
        assert "AAAA" in html
        assert "BBBB" in html
        # No "Original" card when no original is supplied.
        assert "Original (unprocessed)" not in html

    def test_with_original(self):
        html = generate_comparison_html(
            original_b64="ORIG",
            version_a_b64="AAAA",
            version_b_b64="BBBB",
            label_a="A-label",
            label_b="B-label",
        )
        assert "Original (unprocessed)" in html
        assert "ORIG" in html
        assert "A-label" in html
        assert "B-label" in html

    def test_labels_appear_in_reveal_script(self):
        """Reveal-text substitution must bake both labels into the JS block."""
        html = generate_comparison_html(
            original_b64=None,
            version_a_b64="aa",
            version_b_b64="bb",
            label_a="Variant-A",
            label_b="Variant-B",
        )
        # The reveal script references labels twice each (swap vs. no-swap).
        assert html.count("Variant-A") >= 2
        assert html.count("Variant-B") >= 2

    @pytest.mark.parametrize(
        "label_a,label_b",
        [("", ""), ("a" * 200, "b" * 200)],
    )
    def test_accepts_edge_case_labels(self, label_a: str, label_b: str):
        html = generate_comparison_html(
            original_b64=None,
            version_a_b64="x",
            version_b_b64="y",
            label_a=label_a,
            label_b=label_b,
        )
        assert "<html" in html
