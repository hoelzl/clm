"""Tests for :func:`make_backend` — the Phase B backend factory."""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.infrastructure.config import RecordingsConfig
from clm.recordings.workflow.backends import (
    ExternalAudioFirstBackend,
    OnnxAudioFirstBackend,
    ProcessingBackend,
    make_backend,
)


class TestMakeBackendDispatch:
    def test_external_backend(self, tmp_path: Path):
        config = RecordingsConfig(processing_backend="external")
        backend = make_backend(config, root_dir=tmp_path)
        assert isinstance(backend, ExternalAudioFirstBackend)
        assert backend.capabilities.name == "external"

    def test_onnx_backend(self, tmp_path: Path):
        config = RecordingsConfig(processing_backend="onnx")
        backend = make_backend(config, root_dir=tmp_path)
        assert isinstance(backend, OnnxAudioFirstBackend)
        assert backend.capabilities.name == "onnx"

    def test_result_conforms_to_protocol(self, tmp_path: Path):
        for name in ("external", "onnx"):
            config = RecordingsConfig(processing_backend=name)
            backend = make_backend(config, root_dir=tmp_path)
            assert isinstance(backend, ProcessingBackend)


class TestMakeBackendErrors:
    def test_auphonic_not_implemented(self, tmp_path: Path):
        config = RecordingsConfig(processing_backend="auphonic")
        with pytest.raises(NotImplementedError, match="auphonic"):
            make_backend(config, root_dir=tmp_path)

    def test_unknown_backend_name(self, tmp_path: Path):
        config = RecordingsConfig(processing_backend="banana")
        with pytest.raises(ValueError, match="banana"):
            make_backend(config, root_dir=tmp_path)


class TestMakeBackendHonoursConfig:
    def test_raw_suffix_passed_through(self, tmp_path: Path):
        config = RecordingsConfig(processing_backend="external", raw_suffix="--SRC")
        backend = make_backend(config, root_dir=tmp_path)
        assert isinstance(backend, ExternalAudioFirstBackend)
        # accepts_file uses the configured suffix
        assert backend.accepts_file(Path("topic--SRC.wav")) is True
        assert backend.accepts_file(Path("topic--RAW.wav")) is False

    def test_root_dir_is_stored(self, tmp_path: Path):
        config = RecordingsConfig(processing_backend="onnx")
        backend = make_backend(config, root_dir=tmp_path)
        assert isinstance(backend, OnnxAudioFirstBackend)
        # The backend keeps a reference to the supplied root_dir; the
        # audio-first template uses it for the to-process path lookup.
        assert backend._root_dir == tmp_path
