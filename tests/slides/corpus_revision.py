"""#682 half 1, the cheap pin: make a real-corpus gate failure interpretable.

The two real-corpus modules (``test_doc_lens_corpus`` / ``test_sync_diff_corpus``)
measure their ceilings against the maintainer's private, continuously edited
PythonCourses checkout. A bare ceiling breach is therefore uninterpretable —
did CLM regress, or did a deck change? Recording the revision the ceilings
were last measured against, and reporting the revision the failing run
actually used, answers that at zero infrastructure cost. The real fix — a
derived, CI-runnable corpus with a curated public test-course repo — is
tracked on #682; until it lands, every real-corpus assertion message carries
:func:`revision_context`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

#: The corpus revision the current ceilings/floors/refusal-code sets were last
#: measured against (PythonCourses). Update DELIBERATELY whenever a ceiling is
#: re-measured — this constant is what turns "the number moved" into "the
#: number moved relative to a named corpus state".
CEILINGS_MEASURED_AT = "36f66ba970313635d592926b2f658a8a073466cd"  # 2026-08-04


def corpus_revision(corpus_dir: Path) -> str:
    """The corpus checkout's revision: ``<sha>`` (+ ``-dirty``), or ``unknown``.

    Best-effort by design — a corpus that is not a git checkout (a plain
    directory passed via ``CLM_SYNC_CORPUS_DIR``) reports ``unknown`` rather
    than failing the very assertion message that is trying to explain a
    failure.
    """

    def _git(*args: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", "-C", str(corpus_dir), *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except (FileNotFoundError, OSError):
            return None
        return completed.stdout.strip() if completed.returncode == 0 else None

    sha = _git("rev-parse", "HEAD")
    if not sha:
        return "unknown (not a git checkout)"
    dirty = _git("status", "--porcelain", "--untracked-files=no")
    return f"{sha}-dirty" if dirty else sha


def revision_context(corpus_dir: Path) -> str:
    """The one line every real-corpus assertion message carries (#682)."""
    current = corpus_revision(corpus_dir)
    hint = (
        "the corpus moved since the ceilings were measured — decide whether "
        "the corpus or CLM changed before touching any ceiling"
        if current.split("-")[0] != CEILINGS_MEASURED_AT
        else "the corpus is at the measured revision — this is a CLM change"
    )
    return (
        f"\n[corpus revision: running against {current}; ceilings measured at "
        f"{CEILINGS_MEASURED_AT} — {hint}]"
    )
