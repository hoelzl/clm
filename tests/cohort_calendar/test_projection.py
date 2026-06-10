"""Tests for the cohort calendar projection engine (issue #283, phase 3)."""

import datetime as dt

from clm.cli.commands.export.schedule import Bucket, ScheduleDeck
from clm.cohort_calendar.config import CohortCalendarConfig, Holiday, Insert, Merge, Pin, Split
from clm.cohort_calendar.projection import project

# March 2026: 2nd=Mon, 3rd=Tue, 4th=Wed, 5th=Thu, 6th=Fri, 7-8 weekend, 9th=Mon.
MON = dt.date(2026, 3, 2)


def deck(name: str) -> ScheduleDeck:
    return ScheduleDeck(video_title=name, topic_id=name, deck_file=f"slides_{name}")


def bucket(*deck_names: str, weekdays=("mon",), week=1, label="day") -> Bucket:
    return Bucket(
        decks=[deck(n) for n in deck_names],
        span=max(1, len(weekdays)),
        week=week,
        weekday_label=label,
        weekdays=tuple(weekdays),
    )


def cfg(start=MON, **kw) -> CohortCalendarConfig:
    return CohortCalendarConfig(
        start=start,
        end=kw.get("end"),
        pattern=kw.get("pattern", ()),
        holidays=tuple(kw.get("holidays", ())),
        adjustments=tuple(kw.get("adjustments", ())),
    )


def dates_of(proj):
    return [(a.start_date, a.kind, tuple(d.topic_id for d in a.decks)) for a in proj.assignments]


class TestBasicProjection:
    def test_one_to_one_consecutive_days(self):
        buckets = [
            bucket("A", weekdays=("mon",)),
            bucket("B", weekdays=("tue",)),
            bucket("C", weekdays=("wed",)),
        ]
        proj = project(buckets, cfg())
        assert proj.ok
        assert [a.start_date for a in proj.assignments] == [
            dt.date(2026, 3, 2),
            dt.date(2026, 3, 3),
            dt.date(2026, 3, 4),
        ]

    def test_holiday_shifts_everything_after(self):
        # Tue 3 Mar is a holiday -> C slides from Wed 4th to Mon 9th.
        buckets = [
            bucket("A", weekdays=("mon",)),
            bucket("B", weekdays=("tue",)),
            bucket("C", weekdays=("wed",)),
        ]
        proj = project(buckets, cfg(holidays=[Holiday(dt.date(2026, 3, 3), dt.date(2026, 3, 3))]))
        assert [a.start_date for a in proj.assignments] == [
            dt.date(2026, 3, 2),  # Mon
            dt.date(2026, 3, 4),  # Wed (Tue removed)
            dt.date(2026, 3, 9),  # next Mon
        ]

    def test_interval_holiday_two_week_break(self):
        buckets = [bucket("A", weekdays=("mon",)), bucket("B", weekdays=("mon",))]
        # A on Mon 2 Mar; a two-week break covering the next two Mondays pushes B
        # to Mon 23 Mar.
        proj = project(
            buckets,
            cfg(
                pattern=("mon",),
                holidays=[Holiday(dt.date(2026, 3, 9), dt.date(2026, 3, 22), "Break")],
            ),
        )
        assert [a.start_date for a in proj.assignments] == [
            dt.date(2026, 3, 2),
            dt.date(2026, 3, 23),
        ]

    def test_span_bucket_occupies_two_dates(self):
        buckets = [bucket("A", "B", weekdays=("mon", "tue")), bucket("C", weekdays=("wed",))]
        proj = project(buckets, cfg())
        a0 = proj.assignments[0]
        assert a0.start_date == dt.date(2026, 3, 2)  # Mon
        assert a0.end_date == dt.date(2026, 3, 3)  # Tue (span 2)
        # C follows on Wed, not overlapping the span.
        assert proj.assignments[1].start_date == dt.date(2026, 3, 4)

    def test_start_on_non_teaching_day_lands_on_next(self):
        # Start Sunday 1 Mar with a Mon pattern -> first bucket on Mon 2 Mar.
        buckets = [bucket("A", weekdays=("mon",))]
        proj = project(buckets, cfg(start=dt.date(2026, 3, 1), pattern=("mon",)))
        assert proj.assignments[0].start_date == dt.date(2026, 3, 2)


class TestEnd:
    def test_end_overflow_errors_with_deficit(self):
        # Five Mon/Tue/Wed buckets but end leaves only the first week (3 dates).
        buckets = [
            bucket(c, weekdays=(w,)) for c, w in zip("ABCDE", ["mon", "tue", "wed", "mon", "tue"])
        ]
        proj = project(buckets, cfg(end=dt.date(2026, 3, 4)))  # Wed 4 Mar
        assert not proj.ok
        assert any("merge ≥ 2" in e.message for e in proj.errors)

    def test_fits_before_end(self):
        buckets = [bucket(c, weekdays=(w,)) for c, w in zip("ABC", ["mon", "tue", "wed"])]
        proj = project(buckets, cfg(end=dt.date(2026, 3, 6)))
        assert proj.ok


class TestPins:
    def _week(self):
        return [
            bucket("M1", "M2", weekdays=("mon",)),
            bucket("T1", weekdays=("tue",)),
            bucket("W1", "W2", "W3", weekdays=("wed",)),
            bucket("H1", weekdays=("thu",)),
            bucket("F1", "F2", weekdays=("fri",)),
        ]

    def test_overfull_segment_errors_with_deficit(self):
        # The §6.4 scenario WITHOUT the merge: 5 buckets pinned into 4 dates.
        adj = [Pin("M1", dt.date(2026, 3, 3)), Pin("F2", dt.date(2026, 3, 6))]
        proj = project(self._week(), cfg(adjustments=adj))
        assert not proj.ok
        assert any("merge ≥ 1" in e.message for e in proj.errors)

    def test_worked_example_with_merge_is_exact(self):
        # §6.4 resolved: pin start to Tue, end to Fri, merge Tue+Wed on Wed.
        adj = [
            Pin("M1", dt.date(2026, 3, 3)),
            Pin("F2", dt.date(2026, 3, 6)),
            Merge(dt.date(2026, 3, 4), count=2),
        ]
        proj = project(self._week(), cfg(adjustments=adj))
        assert proj.ok, [d.message for d in proj.diagnostics]
        assert dates_of(proj) == [
            (dt.date(2026, 3, 3), "video", ("M1", "M2")),
            (dt.date(2026, 3, 4), "merged", ("T1", "W1", "W2", "W3")),
            (dt.date(2026, 3, 5), "video", ("H1",)),
            (dt.date(2026, 3, 6), "video", ("F1", "F2")),
        ]

    def test_underfull_segment_warns_with_free_dates(self):
        buckets = [bucket("A", weekdays=("mon",)), bucket("Z", weekdays=("fri",))]
        adj = [Pin("A", dt.date(2026, 3, 2)), Pin("Z", dt.date(2026, 3, 6))]
        proj = project(buckets, cfg(pattern=("mon", "tue", "wed", "thu", "fri"), adjustments=adj))
        assert proj.ok
        assert any("free teaching date" in w.message for w in proj.warnings)

    def test_pins_out_of_order_error(self):
        buckets = [bucket("A", weekdays=("mon",)), bucket("B", weekdays=("tue",))]
        adj = [Pin("A", dt.date(2026, 3, 5)), Pin("B", dt.date(2026, 3, 3))]
        proj = project(buckets, cfg(pattern=("mon", "tue", "wed", "thu", "fri"), adjustments=adj))
        assert any("out of order" in e.message for e in proj.errors)

    def test_unknown_pin_ref_error(self):
        proj = project([bucket("A", weekdays=("mon",))], cfg(adjustments=[Pin("nope", MON)]))
        assert any("unknown bucket ref" in e.message for e in proj.errors)

    def test_ambiguous_pin_ref_error(self):
        # Two buckets both contain a deck named "dup".
        buckets = [bucket("dup", weekdays=("mon",)), bucket("dup", weekdays=("tue",))]
        proj = project(buckets, cfg(adjustments=[Pin("dup", dt.date(2026, 3, 3))]))
        assert any("ambiguous" in e.message for e in proj.errors)


class TestInsertAndMerge:
    def test_insert_emits_label_and_shifts(self):
        buckets = [bucket("A", weekdays=("mon",)), bucket("B", weekdays=("tue",))]
        adj = [Insert(dt.date(2026, 3, 3), "Review")]  # Tue 3 Mar is a review day
        proj = project(buckets, cfg(pattern=("mon", "tue", "wed"), adjustments=adj))
        kinds = dates_of(proj)
        assert (dt.date(2026, 3, 2), "video", ("A",)) in kinds
        assert (dt.date(2026, 3, 3), "insert", ()) in kinds
        # B is pushed past the review day to Wed 4 Mar.
        assert (dt.date(2026, 3, 4), "video", ("B",)) in kinds

    def test_insert_on_non_teaching_date_warns(self):
        buckets = [bucket("A", weekdays=("mon",))]
        adj = [Insert(dt.date(2026, 3, 7), "Sat")]  # Saturday — not in pattern
        proj = project(buckets, cfg(pattern=("mon",), adjustments=adj))
        assert any("not a teaching date" in w.message for w in proj.warnings)

    def test_merge_collapses_buckets_onto_one_date(self):
        buckets = [
            bucket("A", weekdays=("mon",)),
            bucket("B", weekdays=("tue",)),
            bucket("C", weekdays=("wed",)),
        ]
        adj = [Merge(dt.date(2026, 3, 3), count=2)]  # B+C share Tue 3 Mar
        proj = project(buckets, cfg(adjustments=adj))
        assert dates_of(proj) == [
            (dt.date(2026, 3, 2), "video", ("A",)),
            (dt.date(2026, 3, 3), "merged", ("B", "C")),
        ]


class TestSplit:
    def test_split_slows_bucket_to_two_dates(self):
        buckets = [bucket("A", weekdays=("mon",)), bucket("B", weekdays=("tue",))]
        adj = [Split("A", (dt.date(2026, 3, 2), dt.date(2026, 3, 3)))]
        proj = project(buckets, cfg(pattern=("mon", "tue", "wed"), adjustments=adj))
        a0 = proj.assignments[0]
        assert a0.start_date == dt.date(2026, 3, 2)
        assert a0.end_date == dt.date(2026, 3, 3)  # occupies two dates
        # B is pushed to Wed.
        assert proj.assignments[1].start_date == dt.date(2026, 3, 4)


class TestDegenerate:
    def test_no_teaching_weekdays_errors(self):
        # Bucket has no weekday tokens and no pattern configured.
        b = Bucket(decks=[deck("A")], span=1, week=1, weekday_label="x", weekdays=())
        proj = project([b], cfg())
        assert not proj.ok
        assert any("no teaching weekdays" in e.message for e in proj.errors)

    def test_empty_buckets_yields_empty_calendar(self):
        proj = project([], cfg(pattern=("mon",)))
        assert proj.assignments == ()
        assert proj.ok
