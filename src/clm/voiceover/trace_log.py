"""JSONL trace log for voiceover merge operations.

Writes one line per LLM call to
``.clm/voiceover-traces/<topic>-<timestamp>.jsonl`` inside the working
directory. The log is independent of Langfuse and provides a durable,
local-first substrate for training data extraction (Phase 4).
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_git_head() -> str | None:
    """Return the current HEAD commit hash, or None if not in a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


class TraceLog:
    """Append-only JSONL trace log for voiceover merge operations.

    Usage::

        trace = TraceLog.create("slides_intro.py")
        trace.log_merge_call(
            slide_id="slides_intro/3",
            language="de",
            baseline="- existing bullet",
            transcript="the trainer said...",
            llm_merged="- merged bullet",
            rewrites=[],
            dropped_from_transcript=["willkommen zurück"],
        )
    """

    def __init__(self, path: Path, slide_file: str, git_head: str | None):
        self._path = path
        self._slide_file = slide_file
        self._git_head = git_head

    @classmethod
    def create(cls, slide_file: str, base_dir: Path | None = None) -> TraceLog:
        """Create a new trace log for a sync invocation.

        Args:
            slide_file: Name of the slide file being synced.
            base_dir: Base directory for ``.clm/``. Defaults to cwd.

        Returns:
            A new TraceLog instance.
        """
        base = base_dir or Path.cwd()
        traces_dir = base / ".clm" / "voiceover-traces"
        traces_dir.mkdir(parents=True, exist_ok=True)

        stem = Path(slide_file).stem
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        log_path = traces_dir / f"{stem}-{timestamp}.jsonl"

        git_head = _get_git_head()

        logger.info("Trace log: %s", log_path)
        return cls(path=log_path, slide_file=slide_file, git_head=git_head)

    @property
    def path(self) -> Path:
        return self._path

    def log_merge_call(
        self,
        *,
        slide_id: str,
        language: str,
        baseline: str,
        transcript: str,
        llm_merged: str,
        rewrites: list[dict],
        dropped_from_transcript: list[str],
        langfuse_trace_id: str | None = None,
    ) -> None:
        """Append one merge-call entry to the trace log."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "slide_file": self._slide_file,
            "slide_id": slide_id,
            "language": language,
            "baseline": baseline,
            "transcript": transcript,
            "llm_merged": llm_merged,
            "rewrites": rewrites,
            "dropped_from_transcript": dropped_from_transcript,
            "git_head": self._git_head,
        }
        if langfuse_trace_id:
            entry["langfuse_trace_id"] = langfuse_trace_id

        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
