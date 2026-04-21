"""Narrative-commit heuristic spike.

Throwaway module for the ``clm voiceover debug voiceover-commits``
subcommand. Walks the git history of a slide file and flags commits whose
diff is dominated by changes to narrative cells (tagged ``voiceover`` or
``notes``). Consecutive narrative-heavy commits are collapsed into
"runs", and each run yields two candidate revisions (pre-run parent and
post-run tip) that identify-rev can score against the video fingerprint.

Promoted from spike to production if the heuristic proves useful on real
course history; see ``docs/proposals/VOICEOVER_BACKFILL.md`` §6.2.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from clm.notebooks.slide_parser import Cell, parse_cells


@dataclass(frozen=True)
class CommitInfo:
    sha: str
    parent_sha: str | None
    date: datetime
    subject: str


@dataclass(frozen=True)
class CommitMetrics:
    commit: CommitInfo
    narrative_delta: int
    content_delta: int
    ratio: float
    is_narrative_heavy: bool


@dataclass(frozen=True)
class NarrativeRun:
    run_id: int
    commit_metrics: list[CommitMetrics]
    pre_run_sha: str | None
    post_run_sha: str


def classify_cells(cells: list[Cell]) -> tuple[int, int]:
    """Return ``(narrative_chars, content_chars)`` summed across cells.

    Narrative = cells tagged ``voiceover`` or ``notes`` (both count, since
    pre-voiceover-era commits used ``notes``). Content = everything else.
    j2 directives are skipped (they rarely carry recording-relevant churn).
    """
    narrative = 0
    content = 0
    for cell in cells:
        if cell.metadata.is_j2:
            continue
        chars = len(cell.content)
        if cell.metadata.is_narrative:
            narrative += chars
        else:
            content += chars
    return narrative, content


def compute_ratio(narrative_delta: int, content_delta: int) -> float:
    """Narrative share of total churn, with a +1 denominator guard."""
    return narrative_delta / (narrative_delta + content_delta + 1)


def compute_commit_metrics(
    commit: CommitInfo,
    parent_text: str | None,
    commit_text: str | None,
    *,
    threshold: float = 0.7,
    floor: int = 50,
) -> CommitMetrics:
    """Compare parent-vs-commit cell character totals.

    ``parent_text`` may be None (root commit or file added this commit).
    ``commit_text`` may be None (file deleted this commit) — uncommon for
    slide files but we tolerate it.

    The delta is a signed-magnitude approximation of churn: we compare
    total characters in narrative vs content cells between the two
    versions. This undercounts reshuffling (cells moved without edits
    show zero delta), but for the spike that's acceptable — recording
    sessions produce substantive narrative additions, not reshuffles.
    """
    parent_cells = parse_cells(parent_text) if parent_text else []
    commit_cells = parse_cells(commit_text) if commit_text else []

    n_before, c_before = classify_cells(parent_cells)
    n_after, c_after = classify_cells(commit_cells)

    narrative_delta = abs(n_after - n_before)
    content_delta = abs(c_after - c_before)
    ratio = compute_ratio(narrative_delta, content_delta)
    heavy = ratio >= threshold and narrative_delta >= floor

    return CommitMetrics(
        commit=commit,
        narrative_delta=narrative_delta,
        content_delta=content_delta,
        ratio=ratio,
        is_narrative_heavy=heavy,
    )


def collapse_runs(metrics: list[CommitMetrics]) -> list[NarrativeRun]:
    """Collapse consecutive narrative-heavy commits into runs.

    ``metrics`` must be in chronological order (oldest → newest). Returns
    runs in the same order, each with its pre-run parent and post-run tip.
    """
    runs: list[NarrativeRun] = []
    current: list[CommitMetrics] = []
    next_id = 1

    def flush() -> None:
        nonlocal next_id
        if not current:
            return
        runs.append(
            NarrativeRun(
                run_id=next_id,
                commit_metrics=list(current),
                pre_run_sha=current[0].commit.parent_sha,
                post_run_sha=current[-1].commit.sha,
            )
        )
        next_id += 1
        current.clear()

    for m in metrics:
        if m.is_narrative_heavy:
            current.append(m)
        else:
            flush()
    flush()

    return runs


def _git_toplevel(path: Path) -> Path:
    target = path.resolve()
    cwd = target.parent if target.is_file() else target
    out = subprocess.check_output(
        ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
        text=True,
        encoding="utf-8",
    ).strip()
    return Path(out)


def walk_file_history(
    path: Path,
    *,
    since: str | None = None,
    limit: int | None = None,
) -> list[CommitInfo]:
    """Return commits touching ``path``, chronological (oldest → newest)."""
    repo_root = _git_toplevel(path)
    rel = path.resolve().relative_to(repo_root)

    cmd = [
        "git",
        "-C",
        str(repo_root),
        "log",
        "--follow",
        "--format=%H|%P|%aI|%s",
    ]
    if since:
        cmd.append(f"--since={since}")
    if limit:
        cmd.extend(["-n", str(limit)])
    cmd.append("--")
    cmd.append(rel.as_posix())

    out = subprocess.check_output(cmd, text=True, encoding="utf-8")
    commits: list[CommitInfo] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("|", 3)
        if len(parts) != 4:
            continue
        sha, parents, date_str, subject = parts
        parent_sha = parents.split()[0] if parents.strip() else None
        try:
            date = datetime.fromisoformat(date_str)
        except ValueError:
            continue
        commits.append(CommitInfo(sha=sha, parent_sha=parent_sha, date=date, subject=subject))

    return list(reversed(commits))


def get_file_at_rev(rev: str, path: Path) -> str | None:
    """Return the file's content at ``rev``, or None if it doesn't exist there."""
    repo_root = _git_toplevel(path)
    rel = path.resolve().relative_to(repo_root)
    result = subprocess.run(
        ["git", "-C", str(repo_root), "show", f"{rev}:{rel.as_posix()}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def scan_slide_file(
    path: Path,
    *,
    since: str | None = None,
    limit: int = 50,
    threshold: float = 0.7,
    floor: int = 50,
) -> tuple[list[CommitMetrics], list[NarrativeRun]]:
    """End-to-end scan: walk history, score each commit, collapse runs.

    Convenience entry point for the CLI and integration tests.
    """
    commits = walk_file_history(path, since=since, limit=limit)
    metrics: list[CommitMetrics] = []
    for c in commits:
        parent_text = get_file_at_rev(c.parent_sha, path) if c.parent_sha else None
        commit_text = get_file_at_rev(c.sha, path)
        metrics.append(
            compute_commit_metrics(
                c,
                parent_text,
                commit_text,
                threshold=threshold,
                floor=floor,
            )
        )
    runs = collapse_runs(metrics)
    return metrics, runs
