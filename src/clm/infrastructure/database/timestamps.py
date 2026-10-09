"""Timezone-correct handling of timestamps stored in CLM's SQLite databases.

SQLite's ``CURRENT_TIMESTAMP`` (and ``datetime('now')``) write *naive UTC*
strings such as ``2026-09-26 08:15:02``. Parsing them with a plain
``datetime.fromisoformat`` yields a naive datetime that looks like local time,
and subtracting it from ``datetime.now()`` (naive *local* time) is off by the
host's UTC offset — a 7 s job logged as "completed in 7208s" on a CEST host
(#1021).

Every DB timestamp is therefore parsed with :func:`parse_db_timestamp`, which
returns an *aware* UTC datetime, and compared against :func:`utc_now`. Aware
datetimes make the mistake loud: mixing one with a naive ``datetime.now()``
raises ``TypeError`` instead of silently drifting by hours.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return the current time as an aware UTC datetime."""
    return datetime.now(timezone.utc)


def parse_db_timestamp(value: str) -> datetime:
    """Parse a timestamp read from a CLM SQLite database as aware UTC.

    Naive values (what ``CURRENT_TIMESTAMP`` writes) are interpreted as UTC.
    Values that already carry an offset are converted to UTC.
    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_optional_db_timestamp(value: str | None) -> datetime | None:
    """Like :func:`parse_db_timestamp`, but map ``None``/empty to ``None``."""
    if not value:
        return None
    return parse_db_timestamp(value)
