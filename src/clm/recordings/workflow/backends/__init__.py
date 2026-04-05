"""Pluggable post-processing backends for the recording workflow.

This package is the new home for backend implementations. It supersedes
``clm.recordings.workflow.backends_legacy`` (which is still in place
during the Phase A/B transition and will be deleted in Phase D).

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

A factory (``make_backend``) will be added alongside the full
implementations in Phase C; for now the module just re-exports the base
Protocol and the Template Method ABC.
"""

from __future__ import annotations

from clm.recordings.workflow.backends.audio_first import AudioFirstBackend
from clm.recordings.workflow.backends.base import JobContext, ProcessingBackend

__all__ = [
    "AudioFirstBackend",
    "JobContext",
    "ProcessingBackend",
]
