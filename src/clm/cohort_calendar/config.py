"""Per-cohort viewing-calendar config: model + TOML loader (issue #283).

A cohort calendar file (``release/<channel>.calendar.toml``) is the small,
hand-edited set of *deltas* a trainer maintains to map a course's schedule onto
one cohort's real dates: a ``start`` (and optional ``end``) date, an optional
weekly teaching ``pattern``, a list of ``holidays`` (single dates or inclusive
intervals), and an ordered list of ``adjustments`` (merge / split / insert /
pin). See ``docs/claude/design/cohort-viewing-calendar.md``.

TOML is the project's existing config idiom and parses with the stdlib
``tomllib`` (no new dependency), with native date literals so dates land as
:class:`datetime.date` directly. The file is only ever *read* programmatically,
so ``tomllib``'s read-only nature is no limitation.

This module is pure parsing + structural validation. It does **not** project
onto dates or check that buckets *fit* between pins / before ``end`` — those are
the projection engine's job (a later phase), because they need the course's
content sequence.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import tomllib
from attrs import frozen

from clm.core.course_spec import VALID_WEEKDAYS, WEEKDAY_ORDER


class CohortCalendarError(Exception):
    """A cohort calendar file is missing, malformed, or structurally invalid."""


@frozen
class Holiday:
    """A non-teaching span: a single date (``start == end``) or an inclusive interval."""

    start: dt.date
    end: dt.date
    label: str | None = None

    def covers(self, day: dt.date) -> bool:
        """True if *day* falls within this holiday (both ends inclusive)."""
        return self.start <= day <= self.end


@frozen
class Merge:
    """Collapse the next ``count`` buckets onto a single ``date`` (catch-up)."""

    date: dt.date
    count: int


@frozen
class Split:
    """Spread the bucket named by ``ref`` across ``dates`` (slow down)."""

    ref: str
    dates: tuple[dt.date, ...]


@frozen
class Insert:
    """A teaching ``date`` carrying no new video, labelled (review / exam / guest)."""

    date: dt.date
    label: str


@frozen
class Pin:
    """Anchor the bucket named by ``ref`` to exactly ``date`` (segments the timeline)."""

    ref: str
    date: dt.date


Adjustment = Merge | Split | Insert | Pin


@frozen
class CohortCalendarConfig:
    """The parsed, structurally-valid contents of a cohort calendar file.

    ``pattern`` may be empty, meaning "derive from the weekdays the spec
    actually uses" — resolve it with :func:`effective_pattern`. ``adjustments``
    preserve file order (the order in which they apply).
    ``google_calendar_id`` is the optional ``[google] calendar_id`` push target
    for ``clm calendar push``.
    """

    start: dt.date
    end: dt.date | None
    pattern: tuple[str, ...]
    holidays: tuple[Holiday, ...]
    adjustments: tuple[Adjustment, ...]
    google_calendar_id: str | None = None


# --- internal value coercion -------------------------------------------------


def _as_date(value: Any, ctx: str) -> dt.date:
    """Require a TOML date literal (not a datetime, not a string)."""
    # datetime is a subclass of date — a TOML datetime literal must be rejected
    # so dates stay unambiguous (calendars are whole-day granular).
    if isinstance(value, dt.datetime) or not isinstance(value, dt.date):
        raise CohortCalendarError(
            f"{ctx}: expected a date (YYYY-MM-DD), got {value!r}. "
            "Use a bare TOML date literal, e.g. 2026-03-02."
        )
    return value


def _as_int(value: Any, ctx: str) -> int:
    """Require a plain integer (TOML booleans are Python ints — reject them)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise CohortCalendarError(f"{ctx}: expected an integer, got {value!r}.")
    return value


def _as_str(value: Any, ctx: str) -> str:
    if not isinstance(value, str):
        raise CohortCalendarError(f"{ctx}: expected a string, got {value!r}.")
    return value


def _reject_unknown_keys(table: dict[str, Any], allowed: set[str], ctx: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise CohortCalendarError(f"{ctx}: unknown key(s) {unknown}. Allowed: {sorted(allowed)}.")


# --- section parsers ---------------------------------------------------------


def _parse_pattern(raw: Any) -> tuple[str, ...]:
    """Validate the weekday ``pattern`` list; canonicalize order, drop duplicates.

    An empty/absent pattern is allowed (caller derives it from the spec).
    """
    if not isinstance(raw, list):
        raise CohortCalendarError(f"pattern: expected a list of weekday tokens, got {raw!r}.")
    seen: set[str] = set()
    for entry in raw:
        token = _as_str(entry, "pattern entry").lower()
        if token not in VALID_WEEKDAYS:
            raise CohortCalendarError(
                f"pattern: unknown weekday {entry!r}. Expected tokens from {list(WEEKDAY_ORDER)}."
            )
        seen.add(token)
    # Canonical Mon..Sun order, regardless of how the file listed them.
    return tuple(wd for wd in WEEKDAY_ORDER if wd in seen)


def _parse_holiday(entry: Any, index: int) -> Holiday:
    ctx = f"holidays[{index}]"
    if isinstance(entry, dt.date) and not isinstance(entry, dt.datetime):
        return Holiday(start=entry, end=entry)
    if isinstance(entry, dict):
        _reject_unknown_keys(entry, {"from", "to", "label"}, ctx)
        if "from" not in entry or "to" not in entry:
            raise CohortCalendarError(f"{ctx}: an interval needs both 'from' and 'to' dates.")
        start = _as_date(entry["from"], f"{ctx}.from")
        end = _as_date(entry["to"], f"{ctx}.to")
        if end < start:
            raise CohortCalendarError(f"{ctx}: interval end {end} is before start {start}.")
        label = _as_str(entry["label"], f"{ctx}.label") if "label" in entry else None
        return Holiday(start=start, end=end, label=label)
    raise CohortCalendarError(
        f"{ctx}: expected a date or an interval table {{from, to, label?}}, got {entry!r}."
    )


# Discriminator key -> the full set of keys that adjustment kind allows.
_ADJUSTMENT_KEYS: dict[str, set[str]] = {
    "merge": {"merge", "count"},
    "split": {"split", "dates"},
    "insert": {"insert", "label"},
    "pin": {"pin", "date"},
}


def _parse_adjustment(table: Any, index: int) -> Adjustment:
    ctx = f"adjustments[{index}]"
    if not isinstance(table, dict):
        raise CohortCalendarError(f"{ctx}: expected a table, got {table!r}.")
    kinds = [k for k in _ADJUSTMENT_KEYS if k in table]
    if len(kinds) != 1:
        raise CohortCalendarError(
            f"{ctx}: a table must carry exactly one of "
            f"{sorted(_ADJUSTMENT_KEYS)} (found {sorted(kinds)})."
        )
    kind = kinds[0]
    _reject_unknown_keys(table, _ADJUSTMENT_KEYS[kind], ctx)

    if kind == "merge":
        count = _as_int(table["count"], f"{ctx}.count") if "count" in table else 0
        if count < 2:
            raise CohortCalendarError(
                f"{ctx}: merge needs count >= 2 (merging fewer than two buckets is a no-op)."
            )
        return Merge(date=_as_date(table["merge"], f"{ctx}.merge"), count=count)

    if kind == "split":
        ref = _as_str(table["split"], f"{ctx}.split")
        if "dates" not in table or not isinstance(table["dates"], list):
            raise CohortCalendarError(f"{ctx}: split needs a 'dates' list.")
        dates = tuple(_as_date(d, f"{ctx}.dates[{i}]") for i, d in enumerate(table["dates"]))
        if len(set(dates)) < 2:
            raise CohortCalendarError(f"{ctx}: split needs at least two distinct dates.")
        return Split(ref=ref, dates=tuple(sorted(dates)))

    if kind == "insert":
        if "label" not in table:
            raise CohortCalendarError(f"{ctx}: insert needs a 'label'.")
        return Insert(
            date=_as_date(table["insert"], f"{ctx}.insert"),
            label=_as_str(table["label"], f"{ctx}.label"),
        )

    # pin
    if "date" not in table:
        raise CohortCalendarError(f"{ctx}: pin needs a 'date'.")
    return Pin(ref=_as_str(table["pin"], f"{ctx}.pin"), date=_as_date(table["date"], f"{ctx}.date"))


# --- public API --------------------------------------------------------------

_TOP_LEVEL_KEYS = {"start", "end", "pattern", "holidays", "adjustments", "google"}


def _parse_google(raw: Any) -> str | None:
    """Parse the optional ``[google]`` table; returns the calendar id (or None)."""
    if not isinstance(raw, dict):
        raise CohortCalendarError(f"google: expected a table, got {raw!r}.")
    _reject_unknown_keys(raw, {"calendar_id"}, "google")
    if "calendar_id" not in raw:
        return None
    calendar_id = _as_str(raw["calendar_id"], "google.calendar_id").strip()
    if not calendar_id:
        raise CohortCalendarError("google.calendar_id: must be a non-empty string.")
    return calendar_id


def parse_calendar_config(text: str) -> CohortCalendarConfig:
    """Parse a cohort calendar from TOML *text*.

    Raises :class:`CohortCalendarError` with a located message on any malformed
    or invalid entry. Validation is structural only — it does not check that
    buckets fit between pins or before ``end`` (that needs the content sequence).
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise CohortCalendarError(f"invalid TOML: {exc}") from exc

    _reject_unknown_keys(data, _TOP_LEVEL_KEYS, "calendar file")

    if "start" not in data:
        raise CohortCalendarError("missing required 'start' date.")
    start = _as_date(data["start"], "start")

    end: dt.date | None = None
    if "end" in data:
        end = _as_date(data["end"], "end")
        if end < start:
            raise CohortCalendarError(f"end {end} is before start {start}.")

    pattern = _parse_pattern(data["pattern"]) if "pattern" in data else ()

    holidays_raw = data.get("holidays", [])
    if not isinstance(holidays_raw, list):
        raise CohortCalendarError(f"holidays: expected a list, got {holidays_raw!r}.")
    holidays = tuple(_parse_holiday(h, i) for i, h in enumerate(holidays_raw))

    adjustments_raw = data.get("adjustments", [])
    if not isinstance(adjustments_raw, list):
        raise CohortCalendarError(
            f"adjustments: expected an array of tables, got {adjustments_raw!r}."
        )
    adjustments = tuple(_parse_adjustment(a, i) for i, a in enumerate(adjustments_raw))

    google_calendar_id = _parse_google(data["google"]) if "google" in data else None

    return CohortCalendarConfig(
        start=start,
        end=end,
        pattern=pattern,
        holidays=holidays,
        adjustments=adjustments,
        google_calendar_id=google_calendar_id,
    )


def load_calendar_config(path: Path) -> CohortCalendarConfig:
    """Load and parse a cohort calendar file. A missing file is an error."""
    if not path.exists():
        raise CohortCalendarError(f"calendar file not found: {path}")
    return parse_calendar_config(path.read_text(encoding="utf-8"))


def effective_pattern(configured: tuple[str, ...], available: Iterable[str]) -> tuple[str, ...]:
    """Resolve the teaching-weekday pattern, in canonical Mon..Sun order.

    An explicit *configured* pattern wins. Otherwise the pattern defaults to the
    weekdays the course schedule actually uses (*available* — typically the union
    of the buckets' subsection weekdays), so a Mon/Tue/Wed course needs no
    explicit pattern.
    """
    if configured:
        return configured
    present = set(available)
    return tuple(wd for wd in WEEKDAY_ORDER if wd in present)
