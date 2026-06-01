"""Unit tests for the bounded retry-with-backoff helper.

These exercise the retry policy directly (with an injected ``sleep`` so they run
instantly), demonstrating the fix for the reported sync bug where a single
transient judge/translator failure dropped a cell with no retry.
"""

from __future__ import annotations

import pytest

from clm.infrastructure.llm.retry import call_with_retries


class _Boom(Exception):
    pass


class _Other(Exception):
    pass


def test_returns_first_success_without_sleeping():
    slept: list[float] = []
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return "ok"

    out = call_with_retries(fn, exc=_Boom, sleep=slept.append)
    assert out == "ok"
    assert calls["n"] == 1
    assert slept == []  # no retry, no backoff


def test_retries_then_succeeds():
    slept: list[float] = []
    attempts = {"n": 0}

    def fn():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise _Boom("transient")
        return "recovered"

    out = call_with_retries(fn, exc=_Boom, base_delay=1.0, sleep=slept.append)
    assert out == "recovered"
    assert attempts["n"] == 3
    # Exponential backoff between the two failed attempts: 1.0s then 2.0s.
    assert slept == [1.0, 2.0]


def test_reraises_last_error_after_exhausting_attempts():
    slept: list[float] = []
    attempts = {"n": 0}

    def fn():
        attempts["n"] += 1
        raise _Boom(f"fail-{attempts['n']}")

    with pytest.raises(_Boom, match="fail-3"):
        call_with_retries(fn, exc=_Boom, attempts=3, sleep=slept.append)
    assert attempts["n"] == 3
    assert slept == [1.0, 2.0]  # slept between attempts 1->2 and 2->3, not after the last


def test_backoff_is_capped_at_max_delay():
    slept: list[float] = []
    attempts = {"n": 0}

    def fn():
        attempts["n"] += 1
        raise _Boom("always")

    with pytest.raises(_Boom):
        call_with_retries(
            fn, exc=_Boom, attempts=5, base_delay=1.0, max_delay=3.0, sleep=slept.append
        )
    # 1, 2, then capped at 3, 3 (four sleeps for five attempts).
    assert slept == [1.0, 2.0, 3.0, 3.0]


def test_does_not_retry_unlisted_exception():
    attempts = {"n": 0}

    def fn():
        attempts["n"] += 1
        raise _Other("not retryable")

    with pytest.raises(_Other):
        call_with_retries(fn, exc=_Boom, sleep=lambda _d: None)
    assert attempts["n"] == 1  # raised immediately, never retried


def test_retries_on_a_tuple_of_exception_types():
    attempts = {"n": 0}

    def fn():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise _Boom("first")
        if attempts["n"] == 2:
            raise _Other("second")
        return "ok"

    out = call_with_retries(fn, exc=(_Boom, _Other), sleep=lambda _d: None)
    assert out == "ok"
    assert attempts["n"] == 3


def test_attempts_must_be_at_least_one():
    with pytest.raises(ValueError, match="attempts"):
        call_with_retries(lambda: None, exc=_Boom, attempts=0)
