"""faster-whisper decoder settings are configurable and reach the model (#1105).

``condition_on_previous_text``, ``no_speech_threshold`` and
``compression_ratio_threshold`` come from ``CLM_WHISPER_*`` (or an explicit
:class:`WhisperDecodeOptions`). The defaults are faster-whisper's own, so an
unconfigured run decodes as before and keeps its cache keys; non-default
settings move the transcript and alignment cache keys. The model is mocked:
no download, no audio.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clm.voiceover.cache import CachePolicy, TranscribeConfig, cached_transcribe
from clm.voiceover.harvest import alignment_cache_config
from clm.voiceover.transcribe import (
    ENV_COMPRESSION_RATIO_THRESHOLD,
    ENV_CONDITION_ON_PREVIOUS_TEXT,
    ENV_NO_SPEECH_THRESHOLD,
    FasterWhisperBackend,
    Transcript,
    TranscriptSegment,
    WhisperDecodeOptions,
)

_ENV = (ENV_CONDITION_ON_PREVIOUS_TEXT, ENV_NO_SPEECH_THRESHOLD, ENV_COMPRESSION_RATIO_THRESHOLD)


@pytest.fixture(autouse=True)
def _clean_whisper_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# Options and their environment surface
# ---------------------------------------------------------------------------


def test_defaults_are_faster_whispers_own() -> None:
    faster_whisper = pytest.importorskip("faster_whisper")
    params = inspect.signature(faster_whisper.WhisperModel.transcribe).parameters
    for name, value in WhisperDecodeOptions().transcribe_kwargs().items():
        assert params[name].default == value, name


def test_from_env_unset_is_default() -> None:
    assert WhisperDecodeOptions.from_env({}) == WhisperDecodeOptions()
    assert WhisperDecodeOptions().non_defaults() == {}


def test_from_env_parses_all_three() -> None:
    opts = WhisperDecodeOptions.from_env(
        {
            ENV_CONDITION_ON_PREVIOUS_TEXT: "false",
            ENV_NO_SPEECH_THRESHOLD: "0.45",
            ENV_COMPRESSION_RATIO_THRESHOLD: "none",
        }
    )
    assert opts == WhisperDecodeOptions(
        condition_on_previous_text=False,
        no_speech_threshold=0.45,
        compression_ratio_threshold=None,
    )
    assert opts.non_defaults() == {
        "condition_on_previous_text": False,
        "no_speech_threshold": 0.45,
        "compression_ratio_threshold": None,
    }


@pytest.mark.parametrize(
    ("name", "value"),
    [
        (ENV_CONDITION_ON_PREVIOUS_TEXT, "maybe"),
        (ENV_NO_SPEECH_THRESHOLD, "high"),
        (ENV_COMPRESSION_RATIO_THRESHOLD, "2,4"),
    ],
)
def test_from_env_rejects_garbage_naming_the_variable(name: str, value: str) -> None:
    with pytest.raises(ValueError, match=name):
        WhisperDecodeOptions.from_env({name: value})


# ---------------------------------------------------------------------------
# The settings reach WhisperModel.transcribe
# ---------------------------------------------------------------------------


def _model() -> MagicMock:
    model = MagicMock()
    seg = MagicMock(start=0.0, end=1.0, text=" Hallo. ")
    model.transcribe.return_value = (iter([seg]), MagicMock(language="de", duration=1.0))
    return model


@patch("clm.voiceover.transcribe.load_whisper_audio", return_value=[0.0])
@patch("clm.voiceover.transcribe.FasterWhisperBackend._get_model")
def test_default_settings_reach_model(mock_get_model, _audio, tmp_path: Path) -> None:
    model = mock_get_model.return_value = _model()
    FasterWhisperBackend(model_size="tiny").transcribe(tmp_path / "a.wav", language="de")
    _, kwargs = model.transcribe.call_args
    assert kwargs == {
        "language": "de",
        "beam_size": 5,
        "vad_filter": True,
        "condition_on_previous_text": True,
        "no_speech_threshold": 0.6,
        "compression_ratio_threshold": 2.4,
    }


@patch("clm.voiceover.transcribe.load_whisper_audio", return_value=[0.0])
@patch("clm.voiceover.transcribe.FasterWhisperBackend._get_model")
def test_env_settings_reach_model(
    mock_get_model, _audio, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The subprocess worker builds the backend without options: it must
    pick the settings up from the inherited environment."""
    monkeypatch.setenv(ENV_CONDITION_ON_PREVIOUS_TEXT, "0")
    monkeypatch.setenv(ENV_NO_SPEECH_THRESHOLD, "0.5")
    monkeypatch.setenv(ENV_COMPRESSION_RATIO_THRESHOLD, "2.0")
    model = mock_get_model.return_value = _model()
    FasterWhisperBackend(model_size="tiny").transcribe(tmp_path / "a.wav", language="de")
    _, kwargs = model.transcribe.call_args
    assert kwargs["condition_on_previous_text"] is False
    assert kwargs["no_speech_threshold"] == 0.5
    assert kwargs["compression_ratio_threshold"] == 2.0
    assert kwargs["vad_filter"] is True


@patch("clm.voiceover.transcribe.load_whisper_audio", return_value=[0.0])
@patch("clm.voiceover.transcribe.FasterWhisperBackend._get_model")
def test_explicit_options_win_over_env(
    mock_get_model, _audio, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_NO_SPEECH_THRESHOLD, "0.1")
    model = mock_get_model.return_value = _model()
    backend = FasterWhisperBackend(
        model_size="tiny", decode_options=WhisperDecodeOptions(no_speech_threshold=0.7)
    )
    backend.transcribe(tmp_path / "a.wav", language="de")
    _, kwargs = model.transcribe.call_args
    assert kwargs["no_speech_threshold"] == 0.7


# ---------------------------------------------------------------------------
# Cache keys
# ---------------------------------------------------------------------------


def test_default_transcribe_config_dict_is_unchanged() -> None:
    """Transcripts cached before #1105's settings existed stay valid."""
    cfg = TranscribeConfig(backend="faster-whisper", model="m", language="de", device_class="cpu")
    assert cfg.as_dict() == {
        "backend": "faster-whisper",
        "model": "m",
        "language": "de",
        "device_class": "cpu",
    }


def _transcript() -> Transcript:
    return Transcript(segments=[TranscriptSegment(0.0, 1.0, "Hallo")], language="de", duration=1.0)


def test_non_default_settings_miss_the_transcript_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"video")
    policy = CachePolicy(cache_root=tmp_path / "cache")
    calls: list[int] = []

    def run() -> tuple[Transcript, bool]:
        return cached_transcribe(
            video,
            policy=policy,
            transcribe_fn=lambda: calls.append(1) or _transcript(),
            backend_name="faster-whisper",
            model_size="tiny",
            language="de",
            device="cpu",
        )

    assert run()[1] is False
    assert run()[1] is True  # default settings: cached
    monkeypatch.setenv(ENV_CONDITION_ON_PREVIOUS_TEXT, "false")
    assert run()[1] is False  # different decode: re-transcribed
    assert run()[1] is True
    assert len(calls) == 2


def test_other_backends_ignore_the_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from clm.voiceover.cache import decode_cache_key

    monkeypatch.setenv(ENV_CONDITION_ON_PREVIOUS_TEXT, "false")
    assert decode_cache_key("cohere") == ()
    assert decode_cache_key("faster-whisper") == (("condition_on_previous_text", False),)


def test_alignment_config_names_non_default_settings_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert "asr_decode" not in alignment_cache_config("de", multi_part=False)
    monkeypatch.setenv(ENV_NO_SPEECH_THRESHOLD, "0.4")
    assert alignment_cache_config("de", multi_part=False)["asr_decode"] == {
        "no_speech_threshold": 0.4
    }
