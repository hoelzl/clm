"""Score historical slide revisions against a video fingerprint.

Backs ``clm voiceover identify-rev``. Given a slide file path and an
OCR-derived video fingerprint (ordered list of keyframe texts), walks
git history and assigns each candidate revision a similarity score via
fuzzy longest-common-subsequence matching of slide labels against video
labels. Narrative-commit run endpoints (see ``narrative_commits``) get a
multiplicative prior on top of the base fingerprint score, per
``docs/proposals/VOICEOVER_BACKFILL.md`` §3.1.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from clm.notebooks.slide_parser import SlideGroup, group_slides, parse_cells
from clm.voiceover.narrative_commits import (
    CommitInfo,
    NarrativeRun,
    collapse_runs,
    compute_commit_metrics,
    get_file_at_rev,
    walk_file_history,
)

logger = logging.getLogger(__name__)

# Minimum fuzzy similarity for two labels to count as a match in LCS.
DEFAULT_LABEL_MATCH = 70.0

# Refuse to proceed below this score unless --force-rev is supplied.
DEFAULT_ACCEPT_THRESHOLD = 0.6

# Multiplicative boost applied to revisions that are narrative-run
# endpoints (pre-run parent or post-run tip). Tuned to break ties
# toward recording-session endpoints without overriding a strong
# fingerprint match elsewhere.
NARRATIVE_PRIOR = 1.25


@dataclass(frozen=True)
class RevisionScore:
    """Scoring result for one historical revision.

    ``score`` is the final, prior-multiplied score used for ranking.
    ``base_score`` is the raw fingerprint similarity before any priors.
    ``is_narrative_candidate`` flags revisions that sit at the boundary
    of a narrative run.
    """

    rev: str
    date: datetime | None
    subject: str | None
    base_score: float
    narrative_prior: float
    score: float
    is_narrative_candidate: bool
    run_id: int | None
    run_position: str | None  # "pre-run" or "post-run" when is_narrative_candidate


def slide_labels(text: str, lang: str) -> list[str]:
    """Extract an ordered fingerprint for a slide file version.

    Primary key is ``slide_id`` (stable across edits); falls back to
    the slide title, then to the first content line. Header (j2) slides
    are excluded because recordings rarely dwell on them and their
    synthetic labels are brittle. Untitled code-only slides map to an
    empty string which the scorer treats as a non-matching placeholder.
    """
    cells = parse_cells(text)
    groups = group_slides(cells, lang, include_header=False)
    labels: list[str] = []
    for group in groups:
        labels.append(_label_for_group(group))
    return labels


def _label_for_group(group: SlideGroup) -> str:
    slide_cell = group.cells[0] if group.cells else None
    slide_id = slide_cell.metadata.slide_id if slide_cell is not None else None
    if slide_id:
        return f"id:{slide_id}"
    if group.title:
        return f"title:{group.title}"
    text = group.text_content
    if text:
        first = text.split(".")[0].strip()
        if first:
            return f"text:{first[:80]}"
    return ""


def fuzzy_lcs_score(
    revision_labels: list[str],
    video_labels: list[str],
    *,
    match_threshold: float = DEFAULT_LABEL_MATCH,
) -> float:
    """Longest common subsequence with fuzzy string equality.

    Uses ``rapidfuzz.fuzz.token_set_ratio`` on lowercased labels. Two
    labels are considered equal when the ratio is at or above
    ``match_threshold``; matched pairs contribute ``ratio / 100`` to the
    total (so close matches weigh less than exact matches).

    The returned score is normalized by ``max(len(a), len(b))`` — adding
    slides between revisions drags the score down, but reshuffling
    within the matched set is essentially free.
    """
    if not revision_labels or not video_labels:
        return 0.0

    from rapidfuzz import fuzz

    m = len(revision_labels)
    n = len(video_labels)

    # Precompute pairwise similarity once.
    sims: list[list[float]] = [[0.0] * n for _ in range(m)]
    for i, rl in enumerate(revision_labels):
        rl_low = _clean_for_match(rl)
        if not rl_low:
            continue
        for j, vl in enumerate(video_labels):
            vl_low = _clean_for_match(vl)
            if not vl_low:
                continue
            sims[i][j] = float(fuzz.token_set_ratio(rl_low, vl_low))

    # DP table: dp[i][j] = best fuzzy-LCS score using first i rev labels
    # and first j video labels.
    dp: list[list[float]] = [[0.0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            best = max(dp[i - 1][j], dp[i][j - 1])
            s = sims[i - 1][j - 1]
            if s >= match_threshold:
                best = max(best, dp[i - 1][j - 1] + s / 100.0)
            dp[i][j] = best

    denom = max(m, n)
    if denom == 0:
        return 0.0
    return dp[m][n] / denom


def _clean_for_match(label: str) -> str:
    """Strip prefix, lowercase, collapse whitespace for fuzzy comparison.

    Hyphens and underscores are normalised to spaces so slug-style
    slide ids (``rest-vs-soap``) tokenise the same way as their OCR
    counterparts (``rest vs soap``) under ``token_set_ratio``.
    """
    if not label:
        return ""
    # Drop the "id:"/"title:"/"text:" prefix when comparing across types.
    stripped = label.split(":", 1)[1] if ":" in label else label
    normalized = stripped.lower().replace("-", " ").replace("_", " ")
    return " ".join(normalized.split())


def score_revisions(
    slide_path: Path,
    video_fingerprint: list[str],
    *,
    lang: str,
    limit: int = 50,
    since: str | None = None,
    match_threshold: float = DEFAULT_LABEL_MATCH,
    narrative_prior: float = NARRATIVE_PRIOR,
) -> list[RevisionScore]:
    """Score each recent revision of ``slide_path`` against the video.

    Returns a list sorted by final score descending. Narrative-run
    endpoints (pre-run parent SHA and post-run tip SHA) receive a
    multiplicative prior ``narrative_prior`` on top of the base LCS
    score. Scores are not clamped — a strong fingerprint match on a
    narrative endpoint can exceed 1.0, which is fine for ranking but
    callers should normalise for display.
    """
    commits = walk_file_history(slide_path, since=since, limit=limit)
    if not commits:
        return []

    metrics = [
        compute_commit_metrics(
            c,
            get_file_at_rev(c.parent_sha, slide_path) if c.parent_sha else None,
            get_file_at_rev(c.sha, slide_path),
        )
        for c in commits
    ]
    runs = collapse_runs(metrics)
    endpoint_info = _endpoint_lookup(runs)

    # Also score the pre-run parents (which aren't in the file-history
    # list, because the "touched" commit is the first heavy commit in
    # the run — its parent may not have touched the file).
    extras: dict[str, CommitInfo] = {}
    for run in runs:
        if run.pre_run_sha and run.pre_run_sha not in {c.sha for c in commits}:
            extras[run.pre_run_sha] = _synth_commit_info(run.pre_run_sha, slide_path)

    all_commits: list[CommitInfo] = list(commits) + list(extras.values())

    scored: list[RevisionScore] = []
    for commit in all_commits:
        text = get_file_at_rev(commit.sha, slide_path)
        if text is None:
            logger.debug("Skipping %s: file does not exist at this revision", commit.sha[:8])
            continue
        labels = slide_labels(text, lang)
        base = fuzzy_lcs_score(labels, video_fingerprint, match_threshold=match_threshold)

        endpoint = endpoint_info.get(commit.sha)
        prior = narrative_prior if endpoint is not None else 1.0
        final = base * prior

        scored.append(
            RevisionScore(
                rev=commit.sha,
                date=commit.date,
                subject=commit.subject,
                base_score=base,
                narrative_prior=prior,
                score=final,
                is_narrative_candidate=endpoint is not None,
                run_id=endpoint[0] if endpoint else None,
                run_position=endpoint[1] if endpoint else None,
            )
        )

    scored.sort(key=lambda r: (r.score, r.base_score), reverse=True)
    return scored


def _endpoint_lookup(runs: list[NarrativeRun]) -> dict[str, tuple[int, str]]:
    """Map endpoint SHA → (run_id, 'pre-run' | 'post-run').

    When a SHA is both an endpoint and appears elsewhere we keep the
    first position seen (pre-run wins over post-run because §3.1 breaks
    ties toward pre-run).
    """
    lookup: dict[str, tuple[int, str]] = {}
    for run in runs:
        if run.pre_run_sha:
            lookup.setdefault(run.pre_run_sha, (run.run_id, "pre-run"))
        lookup.setdefault(run.post_run_sha, (run.run_id, "post-run"))
    return lookup


def _synth_commit_info(sha: str, slide_path: Path) -> CommitInfo:
    """Build a ``CommitInfo`` for a SHA not returned by ``walk_file_history``.

    Pre-run parents may be commits that didn't touch the slide file. We
    still want to score them, so we fetch just their metadata.
    """
    import subprocess

    from clm.voiceover.narrative_commits import _git_toplevel

    repo_root = _git_toplevel(slide_path)
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo_root), "show", "-s", "--format=%P|%aI|%s", sha],
            text=True,
            encoding="utf-8",
        )
        parents, date_str, subject = out.strip().split("|", 2)
        parent_sha = parents.split()[0] if parents.strip() else None
        date = datetime.fromisoformat(date_str)
    except (subprocess.CalledProcessError, ValueError):
        parent_sha = None
        date = datetime.fromtimestamp(0)
        subject = ""
    return CommitInfo(sha=sha, parent_sha=parent_sha, date=date, subject=subject)
