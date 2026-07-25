"""Unit tests for the Docker-job promotion tracker's decision logic.

Only the pure parts are tested: the streak rule and the rendered report. The
GitHub calls are a thin ``gh`` shell-out and are exercised by actually running
the script (``--dry-run``) rather than mocked into a tautology.

The streak rule is the piece that can be quietly wrong, because its one
subtlety — ``skipped`` runs are transparent — is a judgement call rather than
an obvious consequence: a docs-only change tells us nothing about the Docker
job's stability, so counting it either way would distort the measurement.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[2] / "scripts" / "docker_job_stability.py"
_spec = importlib.util.spec_from_file_location("docker_job_stability", _SCRIPT)
assert _spec is not None and _spec.loader is not None
stability = importlib.util.module_from_spec(_spec)
# Register before executing: ``@dataclass`` resolves annotations through
# ``sys.modules[cls.__module__]``, which is None for a module that is being
# exec'd but not yet registered.
sys.modules[_spec.name] = stability
_spec.loader.exec_module(stability)


def _outcomes(*conclusions: str) -> list:
    """Build newest-first outcomes with throwaway ids."""
    return [
        stability.RunOutcome(run_id=1000 + i, sha=f"{i:08x}", conclusion=c)
        for i, c in enumerate(conclusions)
    ]


def test_streak_counts_consecutive_successes_from_the_newest_run() -> None:
    assert stability.current_streak(_outcomes("success", "success", "failure", "success")) == 2


def test_streak_is_zero_when_the_newest_run_failed() -> None:
    assert stability.current_streak(_outcomes("failure", "success", "success")) == 0


def test_skipped_runs_neither_extend_nor_break_the_streak() -> None:
    """A docs-only change says nothing about the Docker job either way."""
    assert stability.current_streak(_outcomes("success", "skipped", "success")) == 2
    assert stability.current_streak(_outcomes("skipped", "skipped")) == 0
    assert stability.current_streak(_outcomes("skipped", "failure")) == 0


def test_cancelled_runs_break_the_streak() -> None:
    """Unlike a skip, a cancellation is not evidence the job would have passed."""
    assert stability.current_streak(_outcomes("success", "cancelled", "success")) == 1


def test_empty_history_is_not_a_streak() -> None:
    assert stability.current_streak([]) == 0


@pytest.mark.parametrize(
    ("conclusions", "expected_marker"),
    [
        (["success"] * stability.REQUIRED_STREAK, "✅ criterion met"),
        (["success"] * (stability.REQUIRED_STREAK - 1), "⏳ not yet"),
    ],
)
def test_body_reports_whether_the_criterion_is_met(
    conclusions: list[str], expected_marker: str
) -> None:
    outcomes = _outcomes(*conclusions)
    body = stability.render_body("hoelzl/clm", outcomes, stability.current_streak(outcomes))
    assert expected_marker in body
    # The marker is what `find_issue` keys on, and what the "newly met"
    # transition check greps for — a rename would silently create a second
    # issue every night.
    assert stability.MARKER in body


def test_body_states_the_exact_context_name_needed_for_the_ruleset() -> None:
    """The promotion instructions must be copy-pasteable, not approximate."""
    outcomes = _outcomes("success")
    body = stability.render_body("hoelzl/clm", outcomes, 1)
    assert stability.JOB_NAME in body
    assert "rulesets/17358657" in body
