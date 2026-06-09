"""Tests for the cohort calendar status engine (issue #283, phase 5)."""

import datetime as dt

from clm.cli.commands.schedule import Bucket, ScheduleDeck
from clm.cohort_calendar.config import CohortCalendarConfig, Holiday
from clm.cohort_calendar.status import compute_status


def deck(name):
    return ScheduleDeck(video_title=name, topic_id=name, deck_file=f"slides_{name}")


def bucket(name, weekday, week=1):
    return Bucket(
        decks=[deck(name)],
        span=1,
        week=week,
        weekday_label=weekday.capitalize(),
        weekdays=(weekday,),
    )


def cfg(start=dt.date(2026, 3, 2), holidays=()):
    return CohortCalendarConfig(
        start=start, end=None, pattern=(), holidays=tuple(holidays), adjustments=()
    )


WEEK = [bucket("A", "mon"), bucket("B", "tue"), bucket("C", "wed")]


class TestComputeStatus:
    def test_today_is_current_and_on_schedule(self):
        r = compute_status(WEEK, cfg(), dt.date(2026, 3, 2))
        assert r.current is not None
        assert r.current.decks[0].topic_id == "A"
        assert r.drift_days == 0

    def test_no_class_today_points_at_next(self):
        # Thu 5 Mar is not a teaching day (mon/tue/wed pattern).
        r = compute_status(WEEK, cfg(), dt.date(2026, 3, 5))
        assert r.current is None
        assert r.finished  # all of A/B/C (2,3,4 Mar) are in the past
        assert not r.upcoming

    def test_midweek_gap_before_finish(self):
        # With a Tue holiday, C slides to Mon 9 Mar; as-of Thu 5 Mar sees it ahead.
        r = compute_status(
            WEEK,
            cfg(holidays=[Holiday(dt.date(2026, 3, 3), dt.date(2026, 3, 3))]),
            dt.date(2026, 3, 5),
        )
        assert r.current is None
        assert not r.finished
        assert r.reference is not None and r.reference.decks[0].topic_id == "C"

    def test_drift_behind_after_holiday(self):
        # Tue holiday pushes C from ideal Wed 4 Mar to actual Mon 9 Mar = +5 days.
        r = compute_status(
            WEEK,
            cfg(holidays=[Holiday(dt.date(2026, 3, 3), dt.date(2026, 3, 3))]),
            dt.date(2026, 3, 9),
        )
        assert r.current is not None and r.current.decks[0].topic_id == "C"
        assert r.drift_days == 5

    def test_not_started(self):
        r = compute_status(WEEK, cfg(start=dt.date(2026, 3, 9)), dt.date(2026, 3, 2))
        assert r.not_started
        assert r.current is None
        assert r.reference is not None and r.reference.decks[0].topic_id == "A"

    def test_finished(self):
        r = compute_status(WEEK, cfg(), dt.date(2026, 3, 20))
        assert r.finished
        assert not r.upcoming

    def test_upcoming_lookahead_limited(self):
        many = [bucket(c, w, week=1) for c, w in zip("ABCDE", ["mon", "tue", "wed", "mon", "tue"])]
        r = compute_status(many, cfg(), dt.date(2026, 3, 1), lookahead=2)
        assert len(r.upcoming) == 2

    def test_projection_errors_flagged(self):
        bad = [Bucket(decks=[deck("A")], span=1, week=1, weekday_label="x", weekdays=())]
        r = compute_status(bad, cfg(), dt.date(2026, 3, 2))
        assert r.has_errors
