"""Projection engine: map the content sequence onto a cohort's real dates.

Pure, I/O-free, date-deterministic. Given the ordered *buckets* (the content
sequence from :func:`clm.cli.commands.export.schedule.build_buckets`) and a
:class:`~clm.cohort_calendar.config.CohortCalendarConfig`, :func:`project`
produces the cohort *calendar*: a list of :class:`Assignment` rows plus
structural :class:`Diagnostic` s.

The model (see ``docs/claude/design/cohort-viewing-calendar.md`` §6):

* Teaching dates are generated from ``start`` + the effective weekday pattern,
  minus ``holidays`` — so a holiday simply removes a slot and everything after
  slides one teaching date later, automatically.
* By default bucket *i* takes the next ``span`` teaching dates (1 : 1 when
  span is 1). A holiday or earlier adjustment shifts later buckets for free.
* **Pins** anchor a bucket to an exact date and *segment* the timeline: each
  segment is fit independently, so an error in one segment can't cascade.
* **No magic** (§6.3.1): when a pin-bounded segment holds more bucket-dates
  than it has teaching dates, the engine does **not** redistribute — it emits an
  *error* naming the exact deficit ("merge ≥ N buckets"). Under-full segments
  emit a *warning* with the free-date count. Catch-up is always an explicit,
  diff-visible ``merge``.

Adjustments:

* ``insert`` — a teaching date carrying no video (review / exam); consumes a
  slot and pushes later buckets back.
* ``merge`` — collapse ``count`` consecutive buckets onto one date (catch-up).
  Each merged bucket is treated as a single date.
* ``split`` — slow a bucket down to occupy ``len(dates)`` teaching dates. Per
  §6.2 / §12 the per-date deck distribution is deferred: the bucket's decks ride
  the first date of its run; this phase honours only the *count* of dates.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from attrs import frozen

from clm.cohort_calendar.config import (
    CohortCalendarConfig,
    Insert,
    Merge,
    Pin,
    Split,
    effective_pattern,
)
from clm.core.course_spec import WEEKDAY_ORDER

if TYPE_CHECKING:
    from clm.cli.commands.export.schedule import Bucket, ScheduleDeck

# Generating teaching dates one calendar day at a time needs an upper bound so a
# misconfigured (e.g. empty) pattern can't loop forever. Six years comfortably
# covers any real course.
_SAFETY_DAYS = 366 * 6
_ONE_DAY = dt.timedelta(days=1)


@frozen
class Assignment:
    """One calendar row: what to watch (or do) across a contiguous date span."""

    start_date: dt.date
    end_date: dt.date  # == start_date for a single-date assignment
    decks: tuple[ScheduleDeck, ...]  # empty for an `insert`
    label: str | None  # set for inserts; else None
    kind: str  # "video" | "merged" | "insert"
    bucket_refs: tuple[str, ...]  # stable id seeds (deck-file stems) for .ics UIDs
    plan_label: str = ""  # plan-relative coordinate, e.g. "W4 Tuesday" (drift/status)


@frozen
class Diagnostic:
    """A structural finding from projection (surfaced by ``calendar check``)."""

    level: str  # "error" | "warning"
    message: str


@frozen
class Projection:
    """The projected calendar plus its diagnostics."""

    assignments: tuple[Assignment, ...]
    diagnostics: tuple[Diagnostic, ...]

    @property
    def errors(self) -> tuple[Diagnostic, ...]:
        return tuple(d for d in self.diagnostics if d.level == "error")

    @property
    def warnings(self) -> tuple[Diagnostic, ...]:
        return tuple(d for d in self.diagnostics if d.level == "warning")

    @property
    def ok(self) -> bool:
        return not self.errors


def _weekday_token(day: dt.date) -> str:
    return WEEKDAY_ORDER[day.weekday()]


class _TeachingDates:
    """On-demand, memoized stream of real teaching dates from ``start``.

    A teaching date is one whose weekday is in the pattern and which no holiday
    covers. ``at`` and ``index_on_or_after`` give the segment fitter its date
    math without materializing years of dates.
    """

    def __init__(self, start: dt.date, pattern: frozenset[str], holidays) -> None:
        self._start = start
        self._pattern = pattern
        self._holidays = holidays
        self._dates: list[dt.date] = []
        self._cursor = start
        self._exhausted = not pattern  # empty pattern => no teaching dates ever

    def _grow_to(self, index: int) -> None:
        cap = self._start + dt.timedelta(days=_SAFETY_DAYS)
        while len(self._dates) <= index and not self._exhausted:
            if self._cursor > cap:
                self._exhausted = True
                break
            day = self._cursor
            self._cursor += _ONE_DAY
            if _weekday_token(day) in self._pattern and not any(
                h.covers(day) for h in self._holidays
            ):
                self._dates.append(day)

    def at(self, index: int) -> dt.date | None:
        self._grow_to(index)
        return self._dates[index] if index < len(self._dates) else None

    def index_on_or_after(self, day: dt.date) -> int:
        """Smallest teaching-date index whose date is >= *day*."""
        i = 0
        while True:
            d = self.at(i)
            if d is None or d >= day:
                return i
            i += 1

    def is_teaching_date(self, day: dt.date) -> bool:
        return _weekday_token(day) in self._pattern and not any(
            h.covers(day) for h in self._holidays
        )


def _resolve_ref(ref: str, buckets) -> tuple[int | None, str | None]:
    """Resolve a pin/split bucket-ref to a unique bucket index."""
    matches = [i for i, b in enumerate(buckets) if ref in b.ref_ids]
    if not matches:
        return None, f"unknown bucket ref {ref!r} (no topic/deck id matches)."
    if len(matches) > 1:
        return None, f"ambiguous bucket ref {ref!r} (matches buckets {matches})."
    return matches[0], None


def _bucket_refs(decks: tuple[ScheduleDeck, ...]) -> tuple[str, ...]:
    return tuple(d.deck_file for d in decks)


def _plan_label(bucket) -> str:
    """The bucket's plan-relative coordinate, e.g. 'W4 Tuesday' (or just 'W4')."""
    return f"W{bucket.week} {bucket.weekday_label}".rstrip()


def project(buckets: list[Bucket], config: CohortCalendarConfig) -> Projection:
    """Project *buckets* onto real dates per *config*. Pure; never raises."""
    diagnostics: list[Diagnostic] = []
    available_weekdays = {wd for b in buckets for wd in b.weekdays}
    pattern = effective_pattern(config.pattern, available_weekdays)
    if not pattern:
        return Projection(
            (),
            (
                Diagnostic(
                    "error",
                    "no teaching weekdays: set `pattern` or use weekday subsections.",
                ),
            ),
        )
    dates = _TeachingDates(config.start, frozenset(pattern), config.holidays)

    # Index date-keyed adjustments and resolve ref-keyed ones to bucket indices.
    inserts: dict[dt.date, Insert] = {}
    merges: dict[dt.date, Merge] = {}
    splits: dict[int, Split] = {}
    pins: list[tuple[int, Pin]] = []
    for adj in config.adjustments:
        if isinstance(adj, Insert):
            inserts[adj.date] = adj
            if not dates.is_teaching_date(adj.date):
                diagnostics.append(
                    Diagnostic(
                        "warning",
                        f"insert date {adj.date} is not a teaching date "
                        "(wrong weekday or a holiday); it will not appear.",
                    )
                )
        elif isinstance(adj, Merge):
            merges[adj.date] = adj
        elif isinstance(adj, Split):
            idx, err = _resolve_ref(adj.ref, buckets)
            if err:
                diagnostics.append(Diagnostic("error", f"split: {err}"))
            else:
                splits[idx] = adj  # type: ignore[index]
        elif isinstance(adj, Pin):
            idx, err = _resolve_ref(adj.ref, buckets)
            if err:
                diagnostics.append(Diagnostic("error", f"pin: {err}"))
            else:
                pins.append((idx, adj))  # type: ignore[arg-type]

    pins.sort(key=lambda p: p[0])
    for (i0, p0), (i1, p1) in zip(pins, pins[1:], strict=False):
        if i0 == i1:
            diagnostics.append(
                Diagnostic("error", f"two pins target the same bucket (index {i0}).")
            )
        if p0.date >= p1.date:
            diagnostics.append(
                Diagnostic(
                    "error",
                    f"pins out of order: bucket {i0} pinned to {p0.date} but later "
                    f"bucket {i1} pinned to the earlier-or-equal {p1.date}.",
                )
            )

    # Build segment boundaries: bucket-index breakpoints at each pin (and 0/end).
    breakpoints = [0]
    for idx, _ in pins:
        if idx != breakpoints[-1]:
            breakpoints.append(idx)
    if breakpoints[-1] != len(buckets):
        breakpoints.append(len(buckets))
    pin_by_bucket = dict(pins)

    assignments: list[Assignment] = []
    # `start` may itself be a pin date; otherwise the first bucket lands on the
    # first teaching date on/after `start` (which is `start` if it is one).
    for s in range(len(breakpoints) - 1):
        lo_bucket = breakpoints[s]
        hi_bucket = breakpoints[s + 1]
        seg_start = pin_by_bucket[lo_bucket].date if lo_bucket in pin_by_bucket else config.start
        # The segment ends where the next pin fixes its bucket; the last segment
        # is open (optionally bounded by `end`).
        next_is_pin = hi_bucket in pin_by_bucket
        seg_end_excl = pin_by_bucket[hi_bucket].date if next_is_pin else None

        _project_segment(
            buckets=buckets,
            lo=lo_bucket,
            hi=hi_bucket,
            seg_start=seg_start,
            seg_end_excl=seg_end_excl,
            config=config,
            dates=dates,
            inserts=inserts,
            merges=merges,
            splits=splits,
            assignments=assignments,
            diagnostics=diagnostics,
            bounded=next_is_pin,
        )

    return Projection(tuple(assignments), tuple(diagnostics))


def _project_segment(
    *,
    buckets,
    lo: int,
    hi: int,
    seg_start: dt.date,
    seg_end_excl: dt.date | None,
    config: CohortCalendarConfig,
    dates: _TeachingDates,
    inserts: dict[dt.date, Insert],
    merges: dict[dt.date, Merge],
    splits: dict[int, Split],
    assignments: list[Assignment],
    diagnostics: list[Diagnostic],
    bounded: bool,
) -> None:
    """Place buckets [lo, hi) from *seg_start*, emitting assignments + diagnostics."""
    start_i = dates.index_on_or_after(seg_start)

    # --- Fit accounting (for the no-magic diagnostics) -----------------------
    # demand = teaching dates the buckets + inserts need; merges reduce it.
    demand = 0
    for i in range(lo, hi):
        demand += len(splits[i].dates) if i in splits else buckets[i].span
    for m in merges.values():
        if _in_window(m.date, seg_start, seg_end_excl) and dates.is_teaching_date(m.date):
            demand -= m.count - 1
    for ins_date in inserts:
        if _in_window(ins_date, seg_start, seg_end_excl) and dates.is_teaching_date(ins_date):
            demand += 1

    if bounded and seg_end_excl is not None:
        available = dates.index_on_or_after(seg_end_excl) - start_i
        if demand > available:
            diagnostics.append(
                Diagnostic(
                    "error",
                    f"segment {seg_start}–{_prev_day(seg_end_excl)}: {hi - lo} buckets "
                    f"need {demand} teaching dates but only {available} are available — "
                    f"merge ≥ {demand - available} bucket(s) to fit.",
                )
            )
        elif demand < available:
            diagnostics.append(
                Diagnostic(
                    "warning",
                    f"segment {seg_start}–{_prev_day(seg_end_excl)}: "
                    f"{available - demand} free teaching date(s) before the next pin.",
                )
            )
    elif config.end is not None:
        end_excl = config.end + _ONE_DAY
        available = dates.index_on_or_after(end_excl) - start_i
        if demand > available:
            diagnostics.append(
                Diagnostic(
                    "error",
                    f"content does not fit before end {config.end}: needs {demand} "
                    f"teaching dates, {available} available — merge ≥ {demand - available} "
                    "bucket(s).",
                )
            )

    _emit_segment(
        buckets=buckets,
        lo=lo,
        hi=hi,
        start_i=start_i,
        dates=dates,
        inserts=inserts,
        merges=merges,
        splits=splits,
        assignments=assignments,
    )


def _in_window(day: dt.date, lo: dt.date, hi_excl: dt.date | None) -> bool:
    if day < lo:
        return False
    return hi_excl is None or day < hi_excl


def _prev_day(day: dt.date) -> dt.date:
    return day - _ONE_DAY


def _emit_segment(
    *,
    buckets,
    lo: int,
    hi: int,
    start_i: int,
    dates: _TeachingDates,
    inserts: dict[dt.date, Insert],
    merges: dict[dt.date, Merge],
    splits: dict[int, Split],
    assignments: list[Assignment],
) -> None:
    """Walk teaching dates from *start_i*, placing buckets [lo, hi)."""
    di = start_i
    bi = lo
    while bi < hi:
        d = dates.at(di)
        if d is None:
            break  # ran out of dates (over-full already reported)
        if d in inserts:
            ins = inserts[d]
            assignments.append(
                Assignment(d, d, (), ins.label, "insert", (f"insert:{d.isoformat()}",))
            )
            di += 1
            continue
        if d in merges:
            count = merges[d].count
            group = list(range(bi, min(bi + count, hi)))
            decks: tuple[ScheduleDeck, ...] = tuple(
                deck for gi in group for deck in buckets[gi].decks
            )
            assignments.append(
                Assignment(
                    d, d, decks, None, "merged", _bucket_refs(decks), _plan_label(buckets[group[0]])
                )
            )
            di += 1
            bi = group[-1] + 1
            continue
        bucket = buckets[bi]
        span = len(splits[bi].dates) if bi in splits else bucket.span
        last = dates.at(di + span - 1) or d
        decks = tuple(bucket.decks)
        assignments.append(
            Assignment(d, last, decks, None, "video", _bucket_refs(decks), _plan_label(bucket))
        )
        di += span
        bi += 1
