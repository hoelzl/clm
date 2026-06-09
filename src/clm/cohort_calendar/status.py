"""Calendar status: where a cohort is *today* vs the plan (issue #283, phase 5).

The only "now"-relative piece of the feature. :func:`compute_status` is pure —
it takes the reference date explicitly (the CLI passes the system date, or
``--as-of``) so it stays deterministic and testable.

Drift is measured against the *ideal* calendar — the same buckets, start, and
pattern but with **no holidays and no adjustments** — i.e. the as-planned
schedule. The reference assignment (the one active today, else the next one) is
matched to its ideal twin by its first bucket ref (a deck-file stem, stable even
across merges), and the gap in calendar days is the drift.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from attrs import frozen

from clm.cohort_calendar.config import CohortCalendarConfig
from clm.cohort_calendar.projection import Assignment, project

if TYPE_CHECKING:
    from clm.cli.commands.schedule import Bucket

_DEFAULT_LOOKAHEAD = 5


@frozen
class StatusReport:
    """A snapshot of a cohort calendar relative to *as_of*."""

    as_of: dt.date
    current: Assignment | None  # assignment whose span covers as_of, if any
    upcoming: tuple[Assignment, ...]  # the next assignments strictly after as_of
    finished: bool  # as_of is past the last assignment
    not_started: bool  # as_of is before the first assignment
    drift_days: int | None  # reference vs ideal: +behind / -ahead / 0 on plan
    reference: Assignment | None  # the assignment drift was measured against
    has_errors: bool  # the projection itself failed (see `calendar check`)


def _ideal(config: CohortCalendarConfig) -> CohortCalendarConfig:
    """The as-planned config: same start/pattern, no holidays, no adjustments."""
    return CohortCalendarConfig(
        start=config.start,
        end=None,
        pattern=config.pattern,
        holidays=(),
        adjustments=(),
    )


def _match_ideal(reference: Assignment, ideal: tuple[Assignment, ...]) -> Assignment | None:
    if not reference.bucket_refs:
        return None
    key = reference.bucket_refs[0]
    return next((a for a in ideal if a.bucket_refs and a.bucket_refs[0] == key), None)


def compute_status(
    buckets: list[Bucket],
    config: CohortCalendarConfig,
    as_of: dt.date,
    *,
    lookahead: int = _DEFAULT_LOOKAHEAD,
) -> StatusReport:
    """Build a :class:`StatusReport` for *as_of* against the projected calendar."""
    actual = project(buckets, config)
    assignments = actual.assignments

    current = next((a for a in assignments if a.start_date <= as_of <= a.end_date), None)
    upcoming = tuple(a for a in assignments if a.start_date > as_of)
    finished = bool(assignments) and as_of > assignments[-1].end_date
    not_started = bool(assignments) and as_of < assignments[0].start_date

    reference = current or (upcoming[0] if upcoming else None)
    drift_days: int | None = None
    if reference is not None:
        twin = _match_ideal(reference, project(buckets, _ideal(config)).assignments)
        if twin is not None:
            drift_days = (reference.start_date - twin.start_date).days

    return StatusReport(
        as_of=as_of,
        current=current,
        upcoming=upcoming[:lookahead],
        finished=finished,
        not_started=not_started,
        drift_days=drift_days,
        reference=reference,
        has_errors=not actual.ok,
    )
