"""Narrative-commit heuristic.

Walks the git history of a slide file and flags commits whose diff is
dominated by changes to narrative cells (tagged ``voiceover`` or
``notes``). Consecutive narrative-heavy commits are collapsed into
"runs", and each run yields two candidate revisions (pre-run parent and
post-run tip) that ``identify-rev`` scores against the video fingerprint
as a multiplicative prior alongside sequence-based slide matching.

Classifier is hunk-based: diff lines are mapped to the owning cell via
``parse_cells`` line offsets, and added+removed lines are both counted
per class. This preserves churn signal on rewrites (where a block of
narrative is replaced by a different block of narrative) that a
net-char-delta approach loses. See
``docs/proposals/VOICEOVER_BACKFILL.md`` §6.2 for the empirical finding
that motivated the upgrade.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
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

    Retained as a helper for callers that want aggregate sizes; the
    commit-level scorer uses :func:`compute_hunk_deltas` instead.
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


def _line_to_cell(cells: list[Cell], num_lines: int) -> list[Cell | None]:
    """Build a 0-indexed array mapping each line to its owning cell.

    ``Cell.line_number`` is the 1-based line of the header. Cell *i*
    owns lines ``[cells[i].line_number - 1, cells[i+1].line_number - 1)``;
    the last cell owns through EOF. Lines before the first cell header
    (rare — shebangs or leading whitespace) are unowned (``None``).
    """
    mapping: list[Cell | None] = [None] * num_lines
    for i, cell in enumerate(cells):
        start = max(cell.line_number - 1, 0)
        end = cells[i + 1].line_number - 1 if i + 1 < len(cells) else num_lines
        for li in range(start, min(end, num_lines)):
            mapping[li] = cell
    return mapping


def _classify_line(cell: Cell | None) -> str | None:
    """Return ``"narrative"``, ``"content"``, or ``None`` for a line."""
    if cell is None or cell.metadata.is_j2:
        return None
    return "narrative" if cell.metadata.is_narrative else "content"


def compute_hunk_deltas(
    parent_text: str | None,
    commit_text: str | None,
) -> tuple[int, int]:
    """Count added+removed lines per cell class between two file versions.

    Hunk-based replacement for net-char-delta: each diff line is charged
    against the cell it belongs to in its respective file version, and
    both sides of a rewrite (deletes from parent + inserts in commit)
    are counted. This preserves churn that a net-size comparison hides.

    Returns ``(narrative_lines, content_lines)``.
    """
    parent_lines = (parent_text or "").splitlines()
    commit_lines = (commit_text or "").splitlines()

    parent_cells = parse_cells(parent_text) if parent_text else []
    commit_cells = parse_cells(commit_text) if commit_text else []

    parent_map = _line_to_cell(parent_cells, len(parent_lines))
    commit_map = _line_to_cell(commit_cells, len(commit_lines))

    narrative = 0
    content = 0

    sm = SequenceMatcher(a=parent_lines, b=commit_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        for li in range(i1, i2):
            cls = _classify_line(parent_map[li])
            if cls == "narrative":
                narrative += 1
            elif cls == "content":
                content += 1
        for lj in range(j1, j2):
            cls = _classify_line(commit_map[lj])
            if cls == "narrative":
                narrative += 1
            elif cls == "content":
                content += 1

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
    floor: int = 5,
) -> CommitMetrics:
    """Score a commit by hunk-based narrative vs content churn.

    ``parent_text`` may be None (root commit or file added this commit).
    ``commit_text`` may be None (file deleted this commit) — uncommon for
    slide files but we tolerate it.

    Deltas are counts of diff lines mapped to each class via the cell
    they belong to, with both sides of a replace counted. A commit is
    narrative-heavy if ``ratio >= threshold`` AND
    ``narrative_delta >= floor`` (the floor filters trivial whitespace
    commits while keeping small but intentional note additions).
    """
    narrative_delta, content_delta = compute_hunk_deltas(parent_text, commit_text)
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
    floor: int = 5,
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
