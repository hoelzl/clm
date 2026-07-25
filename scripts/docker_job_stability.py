"""Track whether the Docker CI job is stable enough to become a required check.

The "Docker Integration Tests" job is deliberately not a required status check:
its image builds reach four external hosts and flaked ~12% of runs on registry
timeouts and partial transfers. PR #678 added a BuildKit layer cache and retry
loops to attack that. Whether it worked is an empirical question that needs
~20 runs of evidence — which is exactly the kind of follow-up that gets
forgotten.

So it measures itself. This script reads the recent CI runs on the default
branch, tallies that job's outcomes, and keeps ONE tracking issue up to date:

* the issue body is **rewritten** every night — editing a body sends no
  notification, so a daily refresh is not spam, and the issue is a live
  dashboard rather than a stale note;
* a **comment** is posted only when the promotion criterion first becomes
  satisfied, because that is the one moment worth interrupting for.

Deliberately crude. A rolling success rate over the last N runs is not a
statistically defensible estimate of flake probability, and it does not
distinguish an infrastructure failure from a real regression — a real
regression *should* hold promotion back anyway, so conflating them is
conservative in the right direction. The point is that the number is in front
of you at all, not that it is precise.

Usage (from CI; needs ``GITHUB_TOKEN`` with ``issues: write``)::

    python scripts/docker_job_stability.py --repo hoelzl/clm

Add ``--dry-run`` to print the report without touching the issue.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

# The job whose stability decides promotion, matched by name.
JOB_NAME = "Docker Integration Tests"

# The workflow the job lives in.
WORKFLOW = "CI"

# How many recent default-branch runs to consider.
WINDOW = 30

# Promotion criterion: this many consecutive successes, ignoring runs where the
# job was skipped (docs-only changes). Twenty is the number that was named when
# the caching landed; it is a judgement call, not a derivation.
REQUIRED_STREAK = 20

ISSUE_TITLE = "Promote “Docker Integration Tests” to a required check?"
ISSUE_LABEL = "ci-health"
MARKER = "<!-- docker-job-stability -->"


@dataclass(frozen=True)
class RunOutcome:
    """One CI run's verdict for the tracked job."""

    run_id: int
    sha: str
    conclusion: str  # "success" | "failure" | "skipped" | "cancelled" | ""


def _gh(repo: str, *args: str) -> str:
    """Run ``gh`` and return stdout, failing loudly on a non-zero exit."""
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout


def collect_outcomes(repo: str, window: int = WINDOW) -> list[RunOutcome]:
    """Most-recent-first outcomes of *JOB_NAME* across the last *window* runs."""
    runs = json.loads(
        _gh(
            repo,
            "run",
            "list",
            "--repo",
            repo,
            "--workflow",
            WORKFLOW,
            "--branch",
            "master",
            "--limit",
            str(window),
            "--json",
            "databaseId,headSha,status",
        )
    )

    outcomes: list[RunOutcome] = []
    for run in runs:
        if run.get("status") != "completed":
            continue
        jobs = json.loads(
            _gh(repo, "run", "view", str(run["databaseId"]), "--repo", repo, "--json", "jobs")
        )["jobs"]
        for job in jobs:
            if job["name"] == JOB_NAME:
                outcomes.append(
                    RunOutcome(
                        run_id=run["databaseId"],
                        sha=run["headSha"][:8],
                        conclusion=job.get("conclusion") or "",
                    )
                )
                break
    return outcomes


def current_streak(outcomes: list[RunOutcome]) -> int:
    """Consecutive successes from the most recent run backwards.

    ``skipped`` runs are transparent: a docs-only change tells us nothing about
    stability either way, so it neither extends nor breaks the streak.
    """
    streak = 0
    for outcome in outcomes:
        if outcome.conclusion == "skipped":
            continue
        if outcome.conclusion == "success":
            streak += 1
            continue
        break
    return streak


def render_body(repo: str, outcomes: list[RunOutcome], streak: int) -> str:
    """The issue body: a dashboard, rewritten in place each night."""
    considered = [o for o in outcomes if o.conclusion not in ("", "skipped")]
    successes = sum(1 for o in considered if o.conclusion == "success")
    rate = (100 * successes / len(considered)) if considered else 0.0
    met = streak >= REQUIRED_STREAK

    lines = [
        MARKER,
        "",
        f"**Consecutive successes: {streak} / {REQUIRED_STREAK}** "
        f"{'✅ criterion met' if met else '⏳ not yet'}",
        "",
        f"Success rate over the last {len(considered)} non-skipped runs on `master`: "
        f"**{rate:.0f}%** ({successes}/{len(considered)}).",
        "",
        "### Why this issue exists",
        "",
        f"`{JOB_NAME}` is not a required status check, because its image builds",
        "reach four external hosts and used to flake ~12% of runs on registry",
        "timeouts and partial transfers. A green PR therefore proves nothing about",
        "it, and a regression can reach `master` — which has happened.",
        "",
        "PR #678 added a BuildKit layer cache and retry loops to attack that. This",
        "issue tracks whether it worked, so the decision to promote the job does not",
        "depend on anyone remembering to check.",
        "",
        "### To promote",
        "",
        "Add the context to the `Require CI green` ruleset:",
        "",
        "```bash",
        f"gh api repos/{repo}/rulesets/17358657   # read, then PUT with the context added",
        "```",
        "",
        f"The context name is exactly `{JOB_NAME}`.",
        "",
        "### Recent runs",
        "",
        "| run | commit | result |",
        "|---|---|---|",
    ]
    for outcome in outcomes[:15]:
        icon = {"success": "✅", "failure": "❌", "skipped": "⏭️", "cancelled": "⚪"}.get(
            outcome.conclusion, "❔"
        )
        lines.append(
            f"| [{outcome.run_id}](https://github.com/{repo}/actions/runs/{outcome.run_id}) "
            f"| `{outcome.sha}` | {icon} {outcome.conclusion or 'unknown'} |"
        )

    lines += [
        "",
        "---",
        "*Updated automatically by `scripts/docker_job_stability.py`, run from the",
        "nightly workflow. Editing an issue body sends no notification, so this",
        "refreshes daily without being noise; a comment is posted only when the",
        "criterion is first met.*",
    ]
    return "\n".join(lines)


def find_issue(repo: str) -> dict[str, Any] | None:
    """The single open tracking issue, if it exists."""
    issues: list[dict[str, Any]] = json.loads(
        _gh(
            repo,
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--label",
            ISSUE_LABEL,
            "--json",
            "number,title,body",
            "--limit",
            "50",
        )
    )
    for issue in issues:
        if MARKER in (issue.get("body") or ""):
            return issue
    return None


def ensure_label(repo: str) -> None:
    """Create the label if it is missing, so there is no manual setup step."""
    try:
        _gh(
            repo,
            "label",
            "create",
            ISSUE_LABEL,
            "--repo",
            repo,
            "--color",
            "0e8a16",
            "--description",
            "Health of the CI pipeline itself",
        )
    except RuntimeError:
        pass  # already exists


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "hoelzl/clm"))
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the report instead of creating or updating the issue",
    )
    args = parser.parse_args()

    outcomes = collect_outcomes(args.repo)
    if not outcomes:
        print(f"No completed runs found for {WORKFLOW!r}; nothing to report.")
        return 0

    streak = current_streak(outcomes)
    body = render_body(args.repo, outcomes, streak)
    met = streak >= REQUIRED_STREAK

    if args.dry_run:
        print(body)
        return 0

    ensure_label(args.repo)
    issue = find_issue(args.repo)

    if issue is None:
        out = _gh(
            args.repo,
            "issue",
            "create",
            "--repo",
            args.repo,
            "--title",
            ISSUE_TITLE,
            "--label",
            ISSUE_LABEL,
            "--body",
            body,
        )
        print(f"Created tracking issue: {out.strip()}")
        return 0

    number = str(issue["number"])
    was_met = "✅ criterion met" in (issue.get("body") or "")
    _gh(args.repo, "issue", "edit", number, "--repo", args.repo, "--body", body)
    print(f"Updated tracking issue #{number} (streak {streak}/{REQUIRED_STREAK})")

    # Comment only on the transition, so the one moment worth interrupting for
    # actually interrupts, and the other 364 days do not.
    if met and not was_met:
        _gh(
            args.repo,
            "issue",
            "comment",
            number,
            "--repo",
            args.repo,
            "--body",
            f"`{JOB_NAME}` has now passed {streak} consecutive non-skipped runs on "
            f"`master`. The criterion for promoting it to a required status check "
            f"is met — see the issue body for the command.",
        )
        print("Criterion newly met; comment posted.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
