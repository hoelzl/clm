"""Unit tests for :mod:`clm.infrastructure.database.busy_retry` (issue #917)."""

from __future__ import annotations

import sqlite3

import pytest

from clm.infrastructure.database.busy_retry import is_transient_lock_error, retry_on_busy


def test_is_transient_lock_error_matches_only_lock_messages():
    assert is_transient_lock_error(sqlite3.OperationalError("database is locked"))
    assert is_transient_lock_error(sqlite3.OperationalError("database table is locked: jobs"))
    assert not is_transient_lock_error(sqlite3.OperationalError("no such table: jobs"))
    assert not is_transient_lock_error(sqlite3.IntegrityError("UNIQUE constraint failed"))
    assert not is_transient_lock_error(RuntimeError("database is locked"))


def test_retry_on_busy_retries_then_succeeds():
    calls = [0]
    slept: list[float] = []

    def flaky():
        calls[0] += 1
        if calls[0] < 3:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    assert retry_on_busy(flaky, label="t", delays=(0.1, 0.2, 0.3), sleep=slept.append) == "ok"
    assert calls[0] == 3
    assert slept == [0.1, 0.2]


def test_retry_on_busy_gives_up_after_schedule():
    calls = [0]

    def always_locked():
        calls[0] += 1
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError):
        retry_on_busy(always_locked, label="t", delays=(0.0, 0.0), sleep=lambda _s: None)
    assert calls[0] == 3  # len(delays) + 1 attempts


def test_retry_on_busy_does_not_retry_other_errors():
    calls = [0]

    def broken():
        calls[0] += 1
        raise sqlite3.OperationalError("no such table: processed_files")

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        retry_on_busy(broken, label="t", delays=(0.0,), sleep=lambda _s: None)
    assert calls[0] == 1


def test_retry_on_busy_calls_on_retry_before_each_backoff():
    calls = [0]
    hooks: list[int] = []

    def flaky():
        calls[0] += 1
        if calls[0] < 3:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    retry_on_busy(
        flaky,
        label="t",
        delays=(0.0, 0.0),
        sleep=lambda _s: None,
        on_retry=lambda: hooks.append(calls[0]),
    )
    assert hooks == [1, 2]


def test_retry_on_busy_ignores_on_retry_failures():
    calls = [0]

    def flaky():
        calls[0] += 1
        if calls[0] < 2:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    def bad_hook():
        raise RuntimeError("hook broke")

    assert (
        retry_on_busy(flaky, label="t", delays=(0.0,), sleep=lambda _s: None, on_retry=bad_hook)
        == "ok"
    )
