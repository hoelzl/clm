"""JSONL trace log for voiceover merge operations.

Writes one line per LLM call to
``.clm/voiceover-traces/<topic>-<timestamp>.jsonl`` inside the working
directory. The log is independent of Langfuse and provides a durable,
local-first substrate for training-data extraction and offline analysis.

Schema v1 (``clm.voiceover.trace/1``) — the first explicitly-versioned
format — preserves every field from the original unversioned log and adds
a few structured fields useful for the upcoming ``compare`` / ``port``
features:

- ``schema`` — constant ``"clm.voiceover.trace/1"``.
- ``cell_index`` — per-slide index within the slide file (authoritative
  identifier alongside ``slide_id`` for callers that need stable ordering).
- ``transcript_segments`` — structured ``[{start, end, text}]`` rather
  than a flat string; helps ``compare`` line up spoken content with the
  polished baseline. Falls back to a single segment with ``start=0`` when
  the caller only provides flat text.
- ``added_from_baseline`` — symmetric counterpart to
  ``dropped_from_transcript``: material the LLM kept from the written
  baseline when it didn't appear in the transcript. Empty on legacy
  callers; to be populated by the merge prompt in a follow-up PR.
- ``model`` / ``mode`` — which LLM and pipeline mode produced the output.
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_V1 = "clm.voiceover.trace/1"


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
        cell_index: int | None = None,
        transcript_segments: list[dict] | None = None,
        added_from_baseline: list[str] | None = None,
        model: str | None = None,
        mode: str | None = None,
    ) -> None:
        """Append one merge-call entry to the trace log.

        All new v1 fields are optional; legacy callers that don't provide
        them get safe defaults (``cell_index=None``, a single-segment
        ``transcript_segments``, empty ``added_from_baseline``).
        """
        if transcript_segments is None:
            segments = [{"start": 0.0, "end": 0.0, "text": transcript}] if transcript else []
        else:
            segments = list(transcript_segments)

        entry: dict[str, Any] = {
            "schema": SCHEMA_V1,
            "kind": "merge",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "slide_file": self._slide_file,
            "slide_id": slide_id,
            "cell_index": cell_index,
            "language": language,
            "baseline": baseline,
            "transcript": transcript,
            "transcript_segments": segments,
            "llm_merged": llm_merged,
            "rewrites": rewrites,
            "dropped_from_transcript": dropped_from_transcript,
            "added_from_baseline": list(added_from_baseline) if added_from_baseline else [],
            "model": model,
            "mode": mode,
            "git_head": self._git_head,
        }
        if langfuse_trace_id:
            entry["langfuse_trace_id"] = langfuse_trace_id

        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def log_propagate_call(
        self,
        *,
        slide_id: str,
        source_language: str,
        target_language: str,
        source_baseline: str,
        source_merged: str,
        target_baseline: str,
        target_translated: str,
        corresponded_changes: list[dict],
        target_preserved_unchanged: bool,
        source_trace_id: str | None = None,
        langfuse_trace_id: str | None = None,
    ) -> None:
        """Append one propagate-call entry to the trace log.

        ``source_trace_id`` points to the Langfuse trace id of the source-
        language merge call that produced the deltas being propagated.
        """
        entry: dict[str, Any] = {
            "schema": SCHEMA_V1,
            "kind": "propagate",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "slide_file": self._slide_file,
            "slide_id": slide_id,
            "source_language": source_language,
            "target_language": target_language,
            "source_baseline": source_baseline,
            "source_merged": source_merged,
            "target_baseline": target_baseline,
            "target_translated": target_translated,
            "corresponded_changes": corresponded_changes,
            "target_preserved_unchanged": target_preserved_unchanged,
            "git_head": self._git_head,
        }
        if source_trace_id:
            entry["source_trace_id"] = source_trace_id
        if langfuse_trace_id:
            entry["langfuse_trace_id"] = langfuse_trace_id

        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_trace_entries(path: Path) -> list[dict]:
    """Read a trace log file and return its entries as a list of dicts.

    Entries without a ``schema`` field are returned as-is (legacy
    ``clm.voiceover.trace/0`` format) so ``trace show`` can inspect older
    logs alongside current ones.
    """
    entries: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("Skipping malformed trace line in %s: %s", path, exc)
                continue
            entries.append(entry)
    return entries
