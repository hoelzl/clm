"""Regression tests for #1021: DB timestamps are UTC, not local time.

SQLite's ``CURRENT_TIMESTAMP`` writes *naive UTC* strings. Code that parsed
them with plain ``datetime.fromisoformat`` and subtracted them from
``datetime.now()`` (naive *local* time) was off by the machine's UTC offset:
a 7 s job logged "completed in 7208s" on a CEST host, ``clm jobs list``
reported fresh jobs as "2.0h ago", and ``clm jobs cancel --older-than``
previewed brand-new jobs as matching.

The bug is invisible on a UTC machine (CI), so every test here runs under
``non_utc_local_clock``, which makes naive ``datetime.now()`` report a local
time 5 h 30 min ahead of UTC regardless of the host's real timezone. The
tests therefore fail before the fix on every machine, including CI.
"""

from __future__ import annotations

import datetime as dt_module
import logging
import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.main import cli
from clm.infrastructure.database import job_queue as job_queue_module
from clm.infrastructure.database.job_queue import JobQueue
from clm.infrastructure.database.schema import init_database

_RealDateTime = dt_module.datetime
_LOCAL_OFFSET = dt_module.timedelta(hours=5, minutes=30)


class _NonUtcLocalClock(_RealDateTime):
    """``datetime`` whose naive ``now()`` is local time at UTC+05:30.

    Aware calls (``now(tz)``) stay correct, so code that compares UTC with UTC
    is unaffected; only code that mixes naive local time with UTC drifts.
    """

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        utc = _RealDateTime.now(dt_module.timezone.utc)
        if tz is None:
            return (utc + _LOCAL_OFFSET).replace(tzinfo=None)
        return utc.astimezone(tz)


@pytest.fixture
def non_utc_local_clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pretend the host runs at UTC+05:30, independent of the real timezone.

    Patches the ``datetime`` class where the code under test looks it up:
    the ``datetime`` module attribute (for function-local
    ``from datetime import datetime`` imports, e.g. in ``clm jobs``) and the
    module-level names bound at import time.
    """
    import clm.infrastructure.database.timestamps as timestamps_module
    import clm.web.services.monitor_service as monitor_service_module

    monkeypatch.setattr(dt_module, "datetime", _NonUtcLocalClock)
    for module in (job_queue_module, timestamps_module, monitor_service_module):
        # raising=False: after the fix some modules no longer import datetime.
        monkeypatch.setattr(module, "datetime", _NonUtcLocalClock, raising=False)
    yield


@pytest.fixture
def jobs_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "jobs.db"
    init_database(db_path)
    return db_path


def _add_job(jq: JobQueue, name: str = "a") -> int:
    return jq.add_job(
        job_type="notebook",
        input_file=f"{name}.py",
        output_file=f"{name}.ipynb",
        content_hash=f"hash-{name}",
        payload={},
    )


def test_completed_job_duration_ignores_local_utc_offset(
    jobs_db: Path, non_utc_local_clock: None, caplog: pytest.LogCaptureFixture
) -> None:
    """Regression test for #1021: ``Job #N completed in 7208s`` for a 7 s job."""
    with JobQueue(jobs_db) as jq:
        job_id = _add_job(jq)
        jq._get_conn().execute(
            "UPDATE jobs SET status = 'processing', "
            "started_at = datetime('now', '-3 seconds') WHERE id = ?",
            (job_id,),
        )
        with caplog.at_level(logging.INFO, logger=job_queue_module.__name__):
            jq.update_job_status(job_id, "completed")

    match = re.search(rf"Job #{job_id} completed in ([0-9.]+)s", caplog.text)
    assert match, caplog.text
    duration = float(match.group(1))
    # ~3 s; the bug added the 19800 s UTC offset.
    assert 2.0 <= duration < 60.0, duration


def test_job_timestamps_are_utc_aware(jobs_db: Path) -> None:
    """Jobs read back from the DB carry their UTC-ness explicitly (#1021)."""
    with JobQueue(jobs_db) as jq:
        job_id = _add_job(jq)
        job = jq.get_job(job_id)
        listed = jq.get_jobs_by_status("pending")

    assert job is not None
    assert job.created_at.utcoffset() == dt_module.timedelta(0)
    assert listed[0].created_at.utcoffset() == dt_module.timedelta(0)
    age = (dt_module.datetime.now(dt_module.timezone.utc) - job.created_at).total_seconds()
    assert -5.0 < age < 60.0, age


def test_jobs_list_age_ignores_local_utc_offset(jobs_db: Path, non_utc_local_clock: None) -> None:
    """Regression test for #1021: fresh jobs listed as "5.5h ago"."""
    with JobQueue(jobs_db) as jq:
        _add_job(jq)

    result = CliRunner().invoke(cli, ["--jobs-db-path", str(jobs_db), "jobs", "list"])

    assert result.exit_code == 0, result.output
    assert re.search(r"\b\d+s ago\b", result.output), result.output
    assert "h ago" not in result.output


def test_jobs_cancel_older_than_preview_ignores_local_utc_offset(
    jobs_db: Path, non_utc_local_clock: None
) -> None:
    """Regression test for #1021: fresh jobs previewed as older than N minutes."""
    with JobQueue(jobs_db) as jq:
        _add_job(jq)

    result = CliRunner().invoke(
        cli,
        ["--jobs-db-path", str(jobs_db), "jobs", "cancel", "--older-than", "10", "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert "No matching pending jobs found." in result.output


def test_monitor_service_worker_uptime_ignores_local_utc_offset(
    jobs_db: Path, non_utc_local_clock: None
) -> None:
    """Regression test for #1021: web monitor worker uptime off by the UTC offset."""
    from clm.web.services.monitor_service import MonitorService

    conn = sqlite3.connect(jobs_db)
    conn.execute(
        "INSERT INTO workers (container_id, worker_type, status, execution_mode) "
        "VALUES ('worker-1', 'notebook', 'idle', 'direct')"
    )
    conn.commit()
    conn.close()

    service = MonitorService(jobs_db)
    try:
        workers = service.get_workers().workers
    finally:
        if service.job_queue is not None:
            service.job_queue.close()

    assert len(workers) == 1
    assert 0 <= workers[0].uptime_seconds < 60, workers[0].uptime_seconds


class TestParseDbTimestamp:
    """The shared helper every DB-timestamp read goes through (#1021)."""

    def test_naive_value_is_interpreted_as_utc(self) -> None:
        from clm.infrastructure.database.timestamps import parse_db_timestamp

        parsed = parse_db_timestamp("2026-09-26 08:15:02")
        assert parsed == _RealDateTime(2026, 9, 26, 8, 15, 2, tzinfo=dt_module.timezone.utc)

    def test_fractional_seconds_are_kept(self) -> None:
        from clm.infrastructure.database.timestamps import parse_db_timestamp

        parsed = parse_db_timestamp("2026-09-26 08:15:02.250")
        assert parsed.microsecond == 250000
        assert parsed.utcoffset() == dt_module.timedelta(0)

    def test_offset_value_is_converted_to_utc(self) -> None:
        from clm.infrastructure.database.timestamps import parse_db_timestamp

        parsed = parse_db_timestamp("2026-09-26T10:15:02+02:00")
        assert parsed == _RealDateTime(2026, 9, 26, 8, 15, 2, tzinfo=dt_module.timezone.utc)
        assert parsed.utcoffset() == dt_module.timedelta(0)

    @pytest.mark.parametrize("value", [None, ""])
    def test_optional_missing_value_is_none(self, value: str | None) -> None:
        from clm.infrastructure.database.timestamps import parse_optional_db_timestamp

        assert parse_optional_db_timestamp(value) is None


def test_monitor_format_timestamp_shows_aware_utc_as_local_time() -> None:
    """The TUI activity panel shows DB (UTC) times in local wall-clock time."""
    from clm.cli.monitor.formatters import format_timestamp

    utc = _RealDateTime(2026, 9, 26, 8, 15, 2, tzinfo=dt_module.timezone.utc)
    assert format_timestamp(utc) == utc.astimezone().strftime("%H:%M:%S")
    assert format_timestamp(utc.replace(tzinfo=None)) == "08:15:02"


def test_monitor_format_timestamp_relative_aware_is_exact() -> None:
    """Relative age of an aware UTC timestamp is computed in UTC (no offset/DST drift)."""
    from clm.cli.monitor.formatters import format_timestamp

    ten_minutes_ago = _RealDateTime.now(dt_module.timezone.utc) - dt_module.timedelta(minutes=10)
    assert format_timestamp(ten_minutes_ago, relative=True) == "10m ago"
