"""Extract training data from voiceover merge trace logs.

Reads JSONL trace logs written by :mod:`clm.voiceover.trace_log` and
correlates each entry with the current slide file state to produce
training triples suitable for fine-tuning or LoRA training.

Two kinds of training signal fall out:

1. ``(baseline + transcript) → human_final``: end-to-end supervised
   training for a merge model.
2. ``(baseline + transcript + llm_output) → human_final``: correction
   training for a critic/editor model.

Entries where ``human_final == llm_output`` (no hand edits after the
LLM merge) are emitted with an empty ``delta_vs_llm`` — these are
valid positive training examples.
"""

from __future__ import annotations

import difflib
import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class TraceEntry:
    """A single parsed entry from a JSONL trace log."""

    slide_file: str
    slide_id: str
    language: str
    baseline: str
    transcript: str
    llm_merged: str
    rewrites: list[dict] = field(default_factory=list)
    dropped_from_transcript: list[str] = field(default_factory=list)
    git_head: str | None = None
    langfuse_trace_id: str | None = None
    timestamp: str = ""


@dataclass
class TrainingTriple:
    """One training example extracted from a trace entry."""

    slide_file: str
    slide_id: str
    language: str
    input_baseline: str
    input_transcript: str
    llm_output: str
    human_final: str
    delta_vs_llm: str
    rewrites: list[dict] = field(default_factory=list)
    dropped_from_transcript: list[str] = field(default_factory=list)
    git_head: str | None = None
    timestamp: str = ""

    def to_dict(self) -> dict:
        """Serialize to a dict suitable for JSONL output."""
        return {
            "slide_file": self.slide_file,
            "slide_id": self.slide_id,
            "language": self.language,
            "input": {
                "baseline": self.input_baseline,
                "transcript": self.input_transcript,
            },
            "llm_output": self.llm_output,
            "human_final": self.human_final,
            "delta_vs_llm": self.delta_vs_llm,
            "rewrites": self.rewrites,
            "dropped_from_transcript": self.dropped_from_transcript,
            "git_head": self.git_head,
            "timestamp": self.timestamp,
        }


def read_trace_log(path: Path) -> list[TraceEntry]:
    """Read and parse a JSONL trace log file.

    Args:
        path: Path to the ``.jsonl`` trace log.

    Returns:
        List of parsed trace entries.

    Raises:
        FileNotFoundError: If the trace log does not exist.
    """
    entries: list[TraceEntry] = []
    with path.open(encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("Skipping malformed JSON at %s:%d: %s", path, line_num, exc)
                continue

            # Skip non-merge entries (e.g. kind="propagate" from Item 2).
            # Legacy entries without "kind" are treated as merges.
            if data.get("kind", "merge") != "merge":
                continue

            entries.append(
                TraceEntry(
                    slide_file=data.get("slide_file", ""),
                    slide_id=data.get("slide_id", ""),
                    language=data.get("language", ""),
                    baseline=data.get("baseline", ""),
                    transcript=data.get("transcript", ""),
                    llm_merged=data.get("llm_merged", ""),
                    rewrites=data.get("rewrites", []),
                    dropped_from_transcript=data.get("dropped_from_transcript", []),
                    git_head=data.get("git_head"),
                    langfuse_trace_id=data.get("langfuse_trace_id"),
                    timestamp=data.get("timestamp", ""),
                )
            )
    return entries


def _git_commit_exists(commit: str) -> bool:
    """Check whether a git commit is reachable in the current repo."""
    try:
        result = subprocess.run(
            ["git", "cat-file", "-t", commit],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0 and "commit" in result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _read_voiceover_for_slide(
    slide_file: Path,
    slide_id: str,
    language: str,
    tag: str = "voiceover",
) -> str | None:
    """Read the current voiceover text for a specific slide.

    Parses the slide file at its current state on disk and extracts
    the voiceover/notes text for the slide matching ``slide_id``.

    Args:
        slide_file: Path to the ``.py`` slide file.
        slide_id: Slide identifier in ``"stem/index"`` format.
        language: Language code (``"de"`` or ``"en"``).
        tag: Cell tag to read (``"voiceover"`` or ``"notes"``).

    Returns:
        The voiceover text for the slide, or ``None`` if the slide
        file does not exist or the slide is not found.
    """
    if not slide_file.exists():
        return None

    from clm.notebooks.slide_parser import parse_slides

    try:
        slide_groups = parse_slides(slide_file, language)
    except Exception as exc:
        logger.warning("Failed to parse %s: %s", slide_file, exc)
        return None

    # Extract slide index from slide_id ("stem/index" format)
    try:
        target_idx = int(slide_id.rsplit("/", 1)[-1])
    except (ValueError, IndexError):
        logger.warning("Cannot parse slide index from slide_id=%s", slide_id)
        return None

    for sg in slide_groups:
        if sg.index == target_idx:
            # Extract text from cells matching the target tag
            parts = []
            for cell in sg.notes_cells:
                if tag in cell.metadata.tags:
                    text = cell.text_content()
                    if text:
                        parts.append(text)
            return "\n".join(parts) if parts else ""

    return None


def _compute_delta(llm_output: str, human_final: str) -> str:
    """Compute a unified diff between LLM output and the human-edited final.

    Returns an empty string when they are identical (positive training
    example — no hand edits needed).
    """
    if llm_output.strip() == human_final.strip():
        return ""

    diff_lines = difflib.unified_diff(
        llm_output.splitlines(keepends=True),
        human_final.splitlines(keepends=True),
        fromfile="llm_output",
        tofile="human_final",
    )
    return "".join(diff_lines)


def extract_training_data(
    trace_log_path: Path,
    *,
    base_dir: Path | None = None,
    tag: str = "voiceover",
    check_git_head: bool = True,
) -> list[TrainingTriple]:
    """Extract training triples from a trace log.

    For each entry in the trace log, reads the current slide file state
    to determine the ``human_final`` text, then computes the delta
    between the LLM output and the human edit.

    Args:
        trace_log_path: Path to the ``.jsonl`` trace log file.
        base_dir: Base directory for resolving slide file paths.
            Defaults to the trace log's grandparent directory
            (i.e. the project root, assuming the trace log is at
            ``.clm/voiceover-traces/...``).
        tag: Cell tag to read from slide files.
        check_git_head: Whether to verify that the ``git_head``
            commit is reachable. If True, entries with unreachable
            commits are skipped with a warning.

    Returns:
        List of training triples, one per trace entry that could
        be successfully correlated.
    """
    entries = read_trace_log(trace_log_path)

    if base_dir is None:
        # Trace logs are at .clm/voiceover-traces/<file>.jsonl
        # so grandparent is the project root
        base_dir = trace_log_path.parent.parent.parent

    triples: list[TrainingTriple] = []
    skipped_git = 0
    skipped_missing = 0

    for entry in entries:
        # Check git_head reachability
        if check_git_head and entry.git_head:
            if not _git_commit_exists(entry.git_head):
                logger.warning(
                    "Skipping %s: git_head %s is unreachable",
                    entry.slide_id,
                    entry.git_head,
                )
                skipped_git += 1
                continue

        # Resolve slide file path
        slide_file = base_dir / entry.slide_file

        # Read current voiceover state for this slide
        human_final = _read_voiceover_for_slide(slide_file, entry.slide_id, entry.language, tag=tag)

        if human_final is None:
            logger.warning(
                "Skipping %s: slide file %s not found or slide not present",
                entry.slide_id,
                slide_file,
            )
            skipped_missing += 1
            continue

        delta = _compute_delta(entry.llm_merged, human_final)

        triples.append(
            TrainingTriple(
                slide_file=entry.slide_file,
                slide_id=entry.slide_id,
                language=entry.language,
                input_baseline=entry.baseline,
                input_transcript=entry.transcript,
                llm_output=entry.llm_merged,
                human_final=human_final,
                delta_vs_llm=delta,
                rewrites=entry.rewrites,
                dropped_from_transcript=entry.dropped_from_transcript,
                git_head=entry.git_head,
                timestamp=entry.timestamp,
            )
        )

    if skipped_git:
        logger.info("Skipped %d entries with unreachable git_head commits", skipped_git)
    if skipped_missing:
        logger.info("Skipped %d entries with missing slide files/slides", skipped_missing)

    return triples
