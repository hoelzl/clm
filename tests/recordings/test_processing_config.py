"""Tests for the recording processing pipeline configuration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clm.recordings.processing.config import AudioFilterConfig, PipelineConfig


class TestAudioFilterConfig:
    def test_defaults(self):
        cfg = AudioFilterConfig()
        assert cfg.highpass_freq == 80
        assert cfg.loudnorm_target == -16.0

    def test_compressor_filter_string(self):
        cfg = AudioFilterConfig()
        f = cfg.compressor_filter
        assert "compand=" in f
        assert "attacks=0.05" in f
        assert "gain=5.0" in f

    def test_loudnorm_filter_string(self):
        cfg = AudioFilterConfig()
        f = cfg.loudnorm_filter
        assert "loudnorm=" in f
        assert "I=-16.0" in f
        assert "TP=-1.5" in f
        assert "LRA=11.0" in f

    def test_custom_values(self):
        cfg = AudioFilterConfig(highpass_freq=120, loudnorm_target=-20.0)
        assert cfg.highpass_freq == 120
        assert cfg.loudnorm_target == -20.0


class TestPipelineConfig:
    def test_defaults(self):
        cfg = PipelineConfig()
        assert cfg.deepfilter_atten_lim == 35.0
        assert cfg.sample_rate == 48000
        assert cfg.video_codec == "copy"
        assert cfg.output_extension == "mp4"
        assert cfg.keep_temp is False
        assert isinstance(cfg.audio_filters, AudioFilterConfig)

    def test_json_roundtrip(self, tmp_path: Path):
        cfg = PipelineConfig(
            deepfilter_atten_lim=50.0,
            sample_rate=44100,
            audio_filters=AudioFilterConfig(highpass_freq=120),
        )
        path = tmp_path / "config.json"
        path.write_text(cfg.model_dump_json(indent=2))

        loaded = PipelineConfig.model_validate_json(path.read_text())
        assert loaded.deepfilter_atten_lim == 50.0
        assert loaded.sample_rate == 44100
        assert loaded.audio_filters.highpass_freq == 120

    def test_dump_produces_valid_json(self):
        cfg = PipelineConfig()
        data = json.loads(cfg.model_dump_json())
        assert "deepfilter_atten_lim" in data
        assert "audio_filters" in data
        assert "highpass_freq" in data["audio_filters"]

    def test_partial_config_uses_defaults(self):
        """Partial JSON should fill in defaults for missing keys."""
        partial = '{"deepfilter_atten_lim": 42.0, "audio_filters": {"highpass_freq": 100}}'
        loaded = PipelineConfig.model_validate_json(partial)
        assert loaded.deepfilter_atten_lim == 42.0
        assert loaded.sample_rate == 48000  # default
        assert loaded.audio_filters.highpass_freq == 100
        assert loaded.audio_filters.loudnorm_target == -16.0  # default
