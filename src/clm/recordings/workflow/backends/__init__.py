"""Pluggable post-processing backends for the recording workflow.

This package is the home for backend implementations. The legacy
``clm.recordings.workflow.backends_legacy`` module is still on disk
until Phase D deletes it; runtime code no longer imports from it.

The :class:`~clm.recordings.workflow.backends.base.ProcessingBackend`
Protocol defines the contract. Two families of backends implement it:

* **Audio-first backends** (:class:`OnnxAudioFirstBackend`,
  :class:`ExternalAudioFirstBackend`) extend
  :class:`~clm.recordings.workflow.backends.audio_first.AudioFirstBackend`
  — a Template Method ABC that captures the shared
  "produce .wav → mux → archive" flow.

* **Video-in/video-out backends** (:class:`AuphonicBackend`) implement
  the Protocol directly because their flow (upload → poll → download)
  does not share structure with the audio-first family.

The :func:`make_backend` factory constructs the backend selected by the
user's :class:`~clm.infrastructure.config.RecordingsConfig`.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from clm.recordings.workflow.backends.audio_first import AudioFirstBackend
from clm.recordings.workflow.backends.auphonic import AuphonicBackend
from clm.recordings.workflow.backends.auphonic_client import AuphonicClient
from clm.recordings.workflow.backends.base import JobContext, ProcessingBackend
from clm.recordings.workflow.backends.external import ExternalAudioFirstBackend
from clm.recordings.workflow.backends.onnx import OnnxAudioFirstBackend

if TYPE_CHECKING:
    from clm.infrastructure.config import RecordingsConfig


def make_backend(
    config: RecordingsConfig,
    *,
    root_dir: Path,
) -> ProcessingBackend:
    """Construct the backend selected by *config.processing_backend*.

    Args:
        config: Recordings section of the user's CLM configuration.
        root_dir: Resolved recordings root directory (the parent of
            ``to-process/``, ``final/``, ``archive/``). Supplied
            separately because callers may override the config's value
            with a CLI flag.

    Returns:
        A backend instance ready to be passed to :class:`JobManager`.

    Raises:
        ValueError: If the backend name is unknown. (Config-level
            validation in :class:`RecordingsConfig` raises a clearer
            error for the ``auphonic``-without-``api_key`` case before
            the factory is reached.)
    """
    name = config.processing_backend
    raw_suffix = config.raw_suffix

    if name == "onnx":
        return OnnxAudioFirstBackend(root_dir=root_dir, raw_suffix=raw_suffix)
    if name == "external":
        return ExternalAudioFirstBackend(root_dir=root_dir, raw_suffix=raw_suffix)
    if name == "auphonic":
        auphonic_cfg = config.auphonic
        # RecordingsConfig's model_validator enforces api_key when the
        # auphonic backend is selected, but defend against callers who
        # construct this dict programmatically without going through
        # validation.
        if not auphonic_cfg.api_key:
            raise ValueError(
                "Cannot construct AuphonicBackend without recordings.auphonic.api_key. "
                "Set it via the TOML config or CLM_RECORDINGS__AUPHONIC__API_KEY."
            )
        client = AuphonicClient(
            api_key=auphonic_cfg.api_key,
            base_url=auphonic_cfg.base_url,
            chunk_size=auphonic_cfg.upload_chunk_size,
        )
        return AuphonicBackend(
            client=client,
            root_dir=root_dir,
            raw_suffix=raw_suffix,
            preset=auphonic_cfg.preset,
            poll_timeout_minutes=auphonic_cfg.poll_timeout_minutes,
            request_cut_list_default=auphonic_cfg.request_cut_list,
        )
    raise ValueError(
        f"Unknown processing backend: {name!r}. Supported values: 'onnx', 'external', 'auphonic'."
    )


__all__ = [
    "AudioFirstBackend",
    "AuphonicBackend",
    "AuphonicClient",
    "ExternalAudioFirstBackend",
    "JobContext",
    "OnnxAudioFirstBackend",
    "ProcessingBackend",
    "make_backend",
]
