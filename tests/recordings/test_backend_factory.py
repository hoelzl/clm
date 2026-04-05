"""Tests for :func:`make_backend` — the backend factory."""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.infrastructure.config import AuphonicConfig, RecordingsConfig
from clm.recordings.workflow.backends import (
    AuphonicBackend,
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

    def test_auphonic_backend(self, tmp_path: Path):
        config = RecordingsConfig(
            processing_backend="auphonic",
            auphonic=AuphonicConfig(api_key="secret"),
        )
        backend = make_backend(config, root_dir=tmp_path)
        assert isinstance(backend, AuphonicBackend)
        assert backend.capabilities.name == "auphonic"
        assert backend.capabilities.is_synchronous is False
        assert backend.capabilities.requires_api_key is True

    def test_result_conforms_to_protocol(self, tmp_path: Path):
        configs = [
            RecordingsConfig(processing_backend="external"),
            RecordingsConfig(processing_backend="onnx"),
            RecordingsConfig(
                processing_backend="auphonic",
                auphonic=AuphonicConfig(api_key="secret"),
            ),
        ]
        for config in configs:
            backend = make_backend(config, root_dir=tmp_path)
            assert isinstance(backend, ProcessingBackend)


class TestMakeBackendErrors:
    def test_auphonic_without_api_key_rejected_at_config_level(self, tmp_path: Path):
        # The config model_validator rejects this combination before
        # the factory is ever reached — users get a clear error at
        # startup instead of a cryptic runtime failure.
        with pytest.raises(ValueError, match="auphonic"):
            RecordingsConfig(processing_backend="auphonic")

    def test_unknown_backend_name_rejected_by_config(self, tmp_path: Path):
        with pytest.raises(ValueError, match="banana"):
            RecordingsConfig(processing_backend="banana")


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

    def test_auphonic_config_flows_through(self, tmp_path: Path):
        config = RecordingsConfig(
            processing_backend="auphonic",
            auphonic=AuphonicConfig(
                api_key="sekret",
                preset="CLM Lecture Recording",
                poll_timeout_minutes=240,
                request_cut_list=True,
            ),
        )
        backend = make_backend(config, root_dir=tmp_path)
        assert isinstance(backend, AuphonicBackend)
        assert backend._preset == "CLM Lecture Recording"
        assert backend._poll_timeout_minutes == 240
        assert backend._request_cut_list_default is True
        assert backend._client._api_key == "sekret"
