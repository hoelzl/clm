"""Loaders for user-supplied pipeline override artifacts.

``clm harvest report``/``autopilot`` and the MCP harvest tools accept a
precomputed transcript (``--transcript``) or alignment (``--alignment``)
instead of running the expensive pipeline steps. These helpers parse and
validate those files, raising :class:`OverrideError` on malformed input so
each caller can convert it to its own error surface (Click usage error,
MCP error payload).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clm.voiceover.aligner import AlignmentResult
    from clm.voiceover.transcribe import Transcript


class OverrideError(ValueError):
    """A transcript/alignment override file is malformed or incomplete."""


def load_transcript_override(path: Path) -> Transcript:
    """Load a JSON transcript produced by ``clm harvest transcribe -o``.

    Accepts both the flat CLI output format (``{"language", "duration",
    "segments": [...]}``) and the canonical ``Transcript.to_dict()`` form,
    so users can pass either.
    """
    from clm.voiceover.transcribe import Transcript

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise OverrideError(f"Transcript override {path} is not a JSON object.")
    if "segments" not in data or "language" not in data or "duration" not in data:
        raise OverrideError(
            f"Transcript override {path} is missing required fields (segments, language, duration)."
        )
    return Transcript.from_dict(data)


def load_alignment_override(path: Path) -> AlignmentResult:
    """Load a precomputed :class:`AlignmentResult` from JSON.

    Expects the shape written by the alignment cache (as stored under the
    cache root's ``alignments/`` — see :func:`clm.voiceover.cache.decode_alignment`).
    Accepts either the inner artifact object or the full cache payload
    (with ``artifact`` wrapper).
    """
    from clm.voiceover.cache import decode_alignment

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise OverrideError(f"Alignment override {path} is not a JSON object.")
    # Cache files wrap the artifact; accept either form
    if "artifact" in data and isinstance(data["artifact"], dict):
        data = data["artifact"]
    if "slide_notes" not in data:
        raise OverrideError(f"Alignment override {path} is missing the 'slide_notes' field.")
    return decode_alignment(data)
