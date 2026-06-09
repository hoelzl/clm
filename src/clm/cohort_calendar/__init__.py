"""Cohort viewing calendar (issue #283).

Projects a course's certification *schedule* (the course-relative plan of
``export schedule``) onto a cohort's real calendar dates. A *calendar* is the
zip of a shared content sequence (ordered day-buckets — see
:func:`clm.cli.commands.schedule.build_buckets`) with a per-cohort date
sequence generated from a small hand-edited ``release/<channel>.calendar.toml``
file.

This package holds the pure, I/O-light pieces: the config model + loader
(:mod:`clm.cohort_calendar.config`) and — later — the projection engine. CLI
wiring lives in :mod:`clm.cli.commands`.
"""

from __future__ import annotations

from clm.cohort_calendar.config import (
    Adjustment,
    CohortCalendarConfig,
    CohortCalendarError,
    Holiday,
    Insert,
    Merge,
    Pin,
    Split,
    effective_pattern,
    load_calendar_config,
    parse_calendar_config,
)

__all__ = [
    "Adjustment",
    "CohortCalendarConfig",
    "CohortCalendarError",
    "Holiday",
    "Insert",
    "Merge",
    "Pin",
    "Split",
    "effective_pattern",
    "load_calendar_config",
    "parse_calendar_config",
]
