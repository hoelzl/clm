"""Subprocess worker for transcription.

This module is invoked as ``python -m clm.voiceover._transcribe_worker``
to run speech-to-text transcription in an isolated process.  Process
isolation is needed because ctranslate2 (used by faster-whisper) can
crash during CUDA context cleanup on Windows, terminating the host
process with exit code 127.  By running transcription in a child
process, the result is saved to a JSON file *before* cleanup begins,
so even if the subprocess crashes during shutdown the transcript is
preserved and the parent process is unaffected.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    """Entry point for the transcription subprocess."""
    parser = argparse.ArgumentParser(
        description="CLM transcription subprocess worker",
    )
    parser.add_argument("audio_path", help="Path to audio file to transcribe")
    parser.add_argument("output_path", help="Path to write the JSON transcript")
    parser.add_argument("--backend", default="faster-whisper")
    parser.add_argument("--model-size", default="large-v3")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--language", default=None)
    args = parser.parse_args(argv)

    from clm.voiceover.transcribe import create_backend

    backend = create_backend(
        args.backend,
        model_size=args.model_size,
        device=args.device,
    )
    transcript = backend.transcribe(
        Path(args.audio_path),
        language=args.language,
    )

    # Write result to JSON immediately, before potential CUDA cleanup crash
    data = transcript.to_dict()
    Path(args.output_path).write_text(
        json.dumps(data, ensure_ascii=False),
        encoding="utf-8",
    )

    # Explicit cleanup to reduce the window for CUDA crash
    del transcript
    del backend


if __name__ == "__main__":
    main()
    sys.exit(0)
