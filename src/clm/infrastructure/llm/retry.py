"""Bounded retry-with-backoff for the synchronous sync/translation LLM calls.

Issue (reported sync bug): a single transient failure on the edit judge (e.g. an
Ollama generation timing out, a momentary network blip, or an OpenRouter
rate-limit) **dropped that cell entirely** — the run proceeded and produced a
partial, incoherent result. One flaky call should not silently lose an edit.

This helper retries a callable a small number of times with exponential backoff,
re-raising the last error if every attempt fails (so a genuinely-down backend is
still surfaced as an error, never guessed). It is intentionally tiny and pure —
the ``sleep`` and ``exc`` types are injectable so it is unit-testable without a
real clock or network, and the real judge/translator clients (network adapters,
excluded from coverage) wrap their single call site with it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

__all__ = ["DEFAULT_ATTEMPTS", "call_with_retries"]

T = TypeVar("T")

# Three attempts (two retries) balances resilience against a flaky call with not
# stalling a run when the backend is genuinely unavailable.
DEFAULT_ATTEMPTS = 3


def call_with_retries(
    fn: Callable[[], T],
    *,
    exc: type[BaseException] | tuple[type[BaseException], ...],
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = 1.0,
    max_delay: float = 16.0,
    sleep: Callable[[float], None] = time.sleep,
    label: str = "LLM call",
) -> T:
    """Call ``fn`` with bounded retries on ``exc``, backing off between attempts.

    Returns ``fn()``'s value on the first success. On the ``attempts``-th
    failure the last exception is re-raised unchanged (callers keep their
    existing "record this as an error" handling). ``base_delay * 2**i`` (capped
    at ``max_delay``) is slept between attempts; ``sleep`` is injectable so tests
    run instantly. Only ``exc`` is retried — a deterministic failure type (e.g. a
    parse error) should be passed a narrower ``exc`` so it is not retried in
    vain.
    """
    if attempts < 1:
        raise ValueError(f"attempts must be >= 1, got {attempts}")
    last: BaseException | None = None
    for i in range(attempts):
        try:
            return fn()
        except exc as err:
            last = err
            if i == attempts - 1:
                break
            delay = min(max_delay, base_delay * (2**i))
            logger.info(
                "%s failed (attempt %d/%d: %s); retrying in %.1fs",
                label,
                i + 1,
                attempts,
                err,
                delay,
            )
            sleep(delay)
    assert last is not None  # loop ran at least once (attempts >= 1)
    raise last
