"""Tests for the cohort calendar config loader (issue #283, phase 2)."""

import datetime as dt

import pytest

from clm.cohort_calendar.config import (
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

FULL = """
start = 2026-03-02
end   = 2026-06-30
pattern = ["wed", "mon", "tue"]

holidays = [
  2026-04-06,
  {from = 2026-07-20, to = 2026-08-02, label = "Summer break"},
]

[[adjustments]]
merge = 2026-03-18
count = 2

[[adjustments]]
split = "variables_intro"
dates = [2026-03-26, 2026-03-25]

[[adjustments]]
insert = 2026-03-30
label = "Review & Q&A"

[[adjustments]]
pin  = "control_flow"
date = 2026-04-09
"""


class TestParseValid:
    def test_minimal_start_only(self):
        cfg = parse_calendar_config("start = 2026-03-02")
        assert cfg.start == dt.date(2026, 3, 2)
        assert cfg.end is None
        assert cfg.pattern == ()
        assert cfg.holidays == ()
        assert cfg.adjustments == ()

    def test_full_scalars(self):
        cfg = parse_calendar_config(FULL)
        assert cfg.start == dt.date(2026, 3, 2)
        assert cfg.end == dt.date(2026, 6, 30)

    def test_pattern_canonicalized_to_mon_sun_order(self):
        cfg = parse_calendar_config(FULL)
        # File listed wed, mon, tue -> stored Mon, Tue, Wed.
        assert cfg.pattern == ("mon", "tue", "wed")

    def test_pattern_dedupes(self):
        cfg = parse_calendar_config('start = 2026-03-02\npattern = ["mon", "mon", "tue"]')
        assert cfg.pattern == ("mon", "tue")

    def test_holidays_single_and_interval(self):
        cfg = parse_calendar_config(FULL)
        assert cfg.holidays[0] == Holiday(dt.date(2026, 4, 6), dt.date(2026, 4, 6))
        assert cfg.holidays[1] == Holiday(dt.date(2026, 7, 20), dt.date(2026, 8, 2), "Summer break")

    def test_adjustments_parsed_in_file_order_with_types(self):
        cfg = parse_calendar_config(FULL)
        assert isinstance(cfg.adjustments[0], Merge)
        assert isinstance(cfg.adjustments[1], Split)
        assert isinstance(cfg.adjustments[2], Insert)
        assert isinstance(cfg.adjustments[3], Pin)

    def test_merge_fields(self):
        cfg = parse_calendar_config(FULL)
        assert cfg.adjustments[0] == Merge(date=dt.date(2026, 3, 18), count=2)

    def test_split_dates_sorted(self):
        cfg = parse_calendar_config(FULL)
        split = cfg.adjustments[1]
        assert split.ref == "variables_intro"
        # File listed 26th then 25th; stored chronologically.
        assert split.dates == (dt.date(2026, 3, 25), dt.date(2026, 3, 26))

    def test_pin_fields(self):
        cfg = parse_calendar_config(FULL)
        assert cfg.adjustments[3] == Pin(ref="control_flow", date=dt.date(2026, 4, 9))


class TestParseErrors:
    def test_missing_start(self):
        with pytest.raises(CohortCalendarError, match="start"):
            parse_calendar_config("end = 2026-06-30")

    def test_end_before_start(self):
        with pytest.raises(CohortCalendarError, match="before start"):
            parse_calendar_config("start = 2026-06-30\nend = 2026-03-02")

    def test_datetime_literal_rejected(self):
        with pytest.raises(CohortCalendarError, match="expected a date"):
            parse_calendar_config("start = 2026-03-02T10:00:00")

    def test_string_date_rejected(self):
        with pytest.raises(CohortCalendarError, match="expected a date"):
            parse_calendar_config('start = "2026-03-02"')

    def test_unknown_top_level_key(self):
        with pytest.raises(CohortCalendarError, match="unknown key"):
            parse_calendar_config("start = 2026-03-02\nbogus = 1")

    def test_invalid_weekday_token(self):
        with pytest.raises(CohortCalendarError, match="unknown weekday"):
            parse_calendar_config('start = 2026-03-02\npattern = ["funday"]')

    def test_invalid_toml(self):
        with pytest.raises(CohortCalendarError, match="invalid TOML"):
            parse_calendar_config("start = = 1")

    def test_holiday_interval_reversed(self):
        text = "start = 2026-03-02\nholidays = [{from = 2026-08-02, to = 2026-07-20}]"
        with pytest.raises(CohortCalendarError, match="before start"):
            parse_calendar_config(text)

    def test_holiday_interval_missing_to(self):
        text = "start = 2026-03-02\nholidays = [{from = 2026-07-20}]"
        with pytest.raises(CohortCalendarError, match="both 'from' and 'to'"):
            parse_calendar_config(text)

    def test_holiday_unknown_key(self):
        text = "start = 2026-03-02\nholidays = [{from = 2026-07-20, to = 2026-07-21, oops = 1}]"
        with pytest.raises(CohortCalendarError, match="unknown key"):
            parse_calendar_config(text)

    def test_adjustment_no_discriminator(self):
        text = "start = 2026-03-02\n[[adjustments]]\ncount = 2"
        with pytest.raises(CohortCalendarError, match="exactly one"):
            parse_calendar_config(text)

    def test_adjustment_two_discriminators(self):
        text = "start = 2026-03-02\n[[adjustments]]\nmerge = 2026-03-18\ncount = 2\npin = 'x'\ndate = 2026-03-18"
        with pytest.raises(CohortCalendarError, match="exactly one"):
            parse_calendar_config(text)

    def test_merge_count_too_small(self):
        text = "start = 2026-03-02\n[[adjustments]]\nmerge = 2026-03-18\ncount = 1"
        with pytest.raises(CohortCalendarError, match="count >= 2"):
            parse_calendar_config(text)

    def test_merge_missing_count(self):
        text = "start = 2026-03-02\n[[adjustments]]\nmerge = 2026-03-18"
        with pytest.raises(CohortCalendarError, match="count >= 2"):
            parse_calendar_config(text)

    def test_split_needs_two_distinct_dates(self):
        text = "start = 2026-03-02\n[[adjustments]]\nsplit = 'x'\ndates = [2026-03-25, 2026-03-25]"
        with pytest.raises(CohortCalendarError, match="two distinct dates"):
            parse_calendar_config(text)

    def test_insert_missing_label(self):
        text = "start = 2026-03-02\n[[adjustments]]\ninsert = 2026-03-30"
        with pytest.raises(CohortCalendarError, match="needs a 'label'"):
            parse_calendar_config(text)

    def test_pin_missing_date(self):
        text = "start = 2026-03-02\n[[adjustments]]\npin = 'x'"
        with pytest.raises(CohortCalendarError, match="needs a 'date'"):
            parse_calendar_config(text)

    def test_count_bool_rejected(self):
        text = "start = 2026-03-02\n[[adjustments]]\nmerge = 2026-03-18\ncount = true"
        with pytest.raises(CohortCalendarError, match="expected an integer"):
            parse_calendar_config(text)


class TestEffectivePattern:
    def test_configured_wins(self):
        assert effective_pattern(("mon", "wed"), ["tue", "thu"]) == ("mon", "wed")

    def test_derived_from_available_in_canonical_order(self):
        assert effective_pattern((), ["fri", "mon", "wed"]) == ("mon", "wed", "fri")

    def test_derived_empty_when_nothing_available(self):
        assert effective_pattern((), []) == ()


class TestHolidayCovers:
    def test_single_day(self):
        h = Holiday(dt.date(2026, 4, 6), dt.date(2026, 4, 6))
        assert h.covers(dt.date(2026, 4, 6))
        assert not h.covers(dt.date(2026, 4, 7))

    def test_interval_inclusive(self):
        h = Holiday(dt.date(2026, 7, 20), dt.date(2026, 8, 2))
        assert h.covers(dt.date(2026, 7, 20))
        assert h.covers(dt.date(2026, 8, 2))
        assert h.covers(dt.date(2026, 7, 25))
        assert not h.covers(dt.date(2026, 8, 3))


class TestLoadFromFile:
    def test_load_roundtrip(self, tmp_path):
        path = tmp_path / "jan.calendar.toml"
        path.write_text(FULL, encoding="utf-8")
        cfg = load_calendar_config(path)
        assert cfg.start == dt.date(2026, 3, 2)
        assert len(cfg.adjustments) == 4

    def test_missing_file_errors(self, tmp_path):
        with pytest.raises(CohortCalendarError, match="not found"):
            load_calendar_config(tmp_path / "nope.calendar.toml")


class TestGoogleTable:
    def test_absent_means_none(self):
        cfg = parse_calendar_config("start = 2026-03-02")
        assert cfg.google_calendar_id is None

    def test_calendar_id_parsed(self):
        cfg = parse_calendar_config(
            'start = 2026-03-02\n[google]\ncalendar_id = "abc123@group.calendar.google.com"\n'
        )
        assert cfg.google_calendar_id == "abc123@group.calendar.google.com"

    def test_empty_table_means_none(self):
        cfg = parse_calendar_config("start = 2026-03-02\n[google]\n")
        assert cfg.google_calendar_id is None

    def test_empty_calendar_id_rejected(self):
        with pytest.raises(CohortCalendarError, match="non-empty"):
            parse_calendar_config('start = 2026-03-02\n[google]\ncalendar_id = "  "\n')

    def test_unknown_key_rejected(self):
        with pytest.raises(CohortCalendarError, match="google: unknown key"):
            parse_calendar_config('start = 2026-03-02\n[google]\ncalender_id = "typo"\n')

    def test_non_string_calendar_id_rejected(self):
        with pytest.raises(CohortCalendarError, match="expected a string"):
            parse_calendar_config("start = 2026-03-02\n[google]\ncalendar_id = 42\n")
