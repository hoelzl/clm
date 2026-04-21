"""Scratch-dir and git-extraction helpers for ``clm voiceover backfill``.

The command itself is a thin composition of ``identify-rev`` →
``sync-at-rev`` → ``port-voiceover`` (see
``docs/proposals/VOICEOVER_BACKFILL.md`` §3). The helpers here handle
the filesystem plumbing so the CLI layer stays readable:

* :func:`extract_slide_file_at_rev` — ``git show <rev>:<path>`` into a
  scratch file, no working-tree mutation.
* :func:`plan_scratch_dir` — create
  ``.clm/voiceover-backfill/<topic>-<YYYYMMDD-HHMMSS>/`` (gitignored via
  the project-wide ``.clm/`` rule).
* :func:`compute_port_patch` — unified-diff text between the current
  target file and the ported result, suitable for ``git apply``.
"""

from __future__ import annotations

import difflib
import logging
from datetime import datetime, timezone
from pathlib import Path

from clm.voiceover.narrative_commits import _git_toplevel, get_file_at_rev

logger = logging.getLogger(__name__)


def plan_scratch_dir(slide_path: Path, *, base_dir: Path | None = None) -> Path:
    """Create and return a fresh ``.clm/voiceover-backfill/<topic>-<ts>/`` directory.

    ``base_dir`` defaults to the slide file's parent (so the directory
    sits next to the slides, inside whatever project rooted them).
    Timestamped so repeated runs do not clobber each other, which also
    makes ``--keep-scratch`` forensics readable.
    """
    anchor = base_dir if base_dir is not None else slide_path.parent
    topic = slide_path.stem
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    scratch = anchor / ".clm" / "voiceover-backfill" / f"{topic}-{stamp}"
    scratch.mkdir(parents=True, exist_ok=True)
    return scratch


def extract_slide_file_at_rev(slide_path: Path, rev: str, scratch_dir: Path) -> Path:
    """Export ``slide_path`` as it existed at ``rev`` into ``scratch_dir``.

    Uses ``git show`` rather than ``git checkout`` so the working tree is
    never touched. Raises :class:`FileNotFoundError` when the file did
    not exist at that revision — callers should surface this cleanly.
    """
    content = get_file_at_rev(rev, slide_path)
    if content is None:
        raise FileNotFoundError(f"{slide_path.name} does not exist at revision {rev[:10]}")
    # Use a short SHA suffix so multiple sync-at-rev invocations against
    # the same scratch dir stay distinguishable.
    out = scratch_dir / f"{slide_path.stem}-at-{rev[:10]}.py"
    out.write_text(content, encoding="utf-8")
    return out


def compute_port_patch(
    target_path: Path,
    updated_text: str,
    *,
    original_text: str | None = None,
) -> str:
    """Return a unified diff from ``target_path``'s current content to ``updated_text``.

    The result uses ``a/<name>`` / ``b/<name>`` paths so it applies
    cleanly via ``git apply`` when run from the file's directory.
    ``original_text`` can be supplied when the caller has already read
    the file (avoids re-reading on very large slide decks).
    """
    if original_text is None:
        original_text = target_path.read_text(encoding="utf-8")
    if original_text == updated_text:
        return ""
    diff_lines = difflib.unified_diff(
        original_text.splitlines(keepends=True),
        updated_text.splitlines(keepends=True),
        fromfile=f"a/{target_path.name}",
        tofile=f"b/{target_path.name}",
    )
    return "".join(diff_lines)


def resolve_rev(slide_path: Path, rev: str) -> str:
    """Resolve a rev-ish (tag, branch, short SHA) to a full SHA.

    Validates that the rev exists in the repo that owns ``slide_path``
    so we fail fast before the heavier sync step runs.
    """
    import subprocess

    repo_root = _git_toplevel(slide_path)
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo_root), "rev-parse", "--verify", f"{rev}^{{commit}}"],
            text=True,
            encoding="utf-8",
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"unknown revision {rev!r}: {exc.stderr.strip()}") from exc
    return out.strip()
