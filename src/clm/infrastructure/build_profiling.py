"""Opt-in build performance profiling (``CLM_PROFILE_BUILD=1``).

This is a lightweight, zero-cost-when-disabled diagnostic for the build's
*progress-reporting* health — specifically the symptom where the ``clm build``
progress bar appears frozen mid-stage while ``clm monitor`` shows hundreds of
jobs finishing, then jumps far ahead.

The bar only advances from inside ``SqliteBackend.wait_for_completion``'s poll
loop (it calls ``progress_tracker.job_completed`` per retired job). If that
coroutine is starved of the event loop — because job *submission* runs a burst
of awaitless, CPU/IO-heavy work (payload build, cache unpickle, disk writes,
SQLite inserts) on the same loop — the bar freezes even though the worker
subprocesses keep finishing. This module measures exactly that:

* the inter-iteration **gap** of the poll loop (healthy ≈ ``poll_interval``;
  a multi-second gap is the freeze), and the number of completions retired when
  the loop finally runs again (the size of the "jump");
* the per-call wall time of ``execute_operation`` (the awaitless submission
  body), split by cache hit/miss, which is the work that starves the loop.

Enable with ``CLM_PROFILE_BUILD=1``. When disabled, the call sites skip all
timing and this module's methods early-return, so there is no measurable
overhead on a normal build. A summary is logged at backend shutdown.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from time import perf_counter

logger = logging.getLogger(__name__)


def _env_enabled() -> bool:
    return os.environ.get("CLM_PROFILE_BUILD", "").strip().lower() in ("1", "true", "yes")


def _configure_stderr_handler() -> None:
    """Route this module's records straight to stderr, INFO+, regardless of the
    app's log config.

    The build configures logging to send INFO records to a file (the console
    only carries the Rich build summary), so profiling lines would otherwise be
    invisible. Because profiling is explicitly opt-in (``CLM_PROFILE_BUILD``),
    attaching a dedicated stderr handler with ``propagate=False`` makes the
    measurements reliably visible/capturable without touching global logging.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


class BuildProfiler:
    """Process-wide accumulator for build profiling counters.

    Thread-safe: ``record_execute_op`` may be called from worker threads once
    the submission work is offloaded off the event loop, while
    ``record_poll_cycle`` is called from the event-loop thread.
    """

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self._lock = threading.Lock()
        if enabled:
            _configure_stderr_handler()

        # Poll-loop health.
        self.poll_cycles = 0
        self.poll_gap_max = 0.0
        self.poll_gap_sum = 0.0
        self.poll_starved_cycles = 0  # gap > 2x poll_interval
        self.poll_completed_total = 0
        # The single worst stall, with how many jobs it hid (the "jump" size).
        self.worst_gap = 0.0
        self.worst_gap_completed = 0

        # Submission body (execute_operation) timing. ``op_time_sum`` is the
        # total wall time of the call; ``offload_time_sum`` is the part spent
        # awaiting the off-loop submission executor (which does not block the
        # event loop). On-loop blocking time = op_time_sum - offload_time_sum.
        self.op_count = 0
        self.op_time_sum = 0.0
        self.op_time_max = 0.0
        self.op_hits = 0
        self.op_misses = 0
        self.offload_count = 0
        self.offload_time_sum = 0.0

        # Payload build (ProcessNotebookOperation.payload) timing — the slide
        # read + base64 of every topic sibling + content hashing, also on-loop.
        self.payload_count = 0
        self.payload_time_sum = 0.0
        self.payload_time_max = 0.0

    def record_poll_cycle(
        self, gap_s: float, completed_count: int, active_count: int, poll_interval: float
    ) -> None:
        """Record one poll-loop iteration's gap since the previous iteration."""
        if not self.enabled:
            return
        with self._lock:
            self.poll_cycles += 1
            self.poll_gap_sum += gap_s
            self.poll_completed_total += completed_count
            if gap_s > self.poll_gap_max:
                self.poll_gap_max = gap_s
            if gap_s > 2.0 * poll_interval:
                self.poll_starved_cycles += 1
            if gap_s > self.worst_gap:
                self.worst_gap = gap_s
                self.worst_gap_completed = completed_count
        # Surface the bad gaps live so a long run is not silent. A gap beyond
        # ~3x the poll interval means the loop did not get scheduled on time —
        # i.e. the bar was frozen for that long.
        if gap_s > max(2.0, 3.0 * poll_interval):
            logger.info(
                "[build-profile] poll-loop stalled %.2fs (then retired %d job(s); %d still active)",
                gap_s,
                completed_count,
                active_count,
            )

    def record_execute_op(self, elapsed_s: float, cache_hit: bool) -> None:
        """Record one ``execute_operation`` (submission body) call."""
        if not self.enabled:
            return
        with self._lock:
            self.op_count += 1
            self.op_time_sum += elapsed_s
            if elapsed_s > self.op_time_max:
                self.op_time_max = elapsed_s
            if cache_hit:
                self.op_hits += 1
            else:
                self.op_misses += 1

    def record_submit_offload(self, elapsed_s: float) -> None:
        """Record time spent awaiting the off-loop submission executor.

        This is wall time that does NOT block the event loop (the loop is free
        to poll while it runs), so it is subtracted from the execute_operation
        total to report the true on-loop blocking cost.
        """
        if not self.enabled:
            return
        with self._lock:
            self.offload_count += 1
            self.offload_time_sum += elapsed_s

    def record_payload_build(self, elapsed_s: float) -> None:
        """Record one ``payload()`` build (slide read + sibling base64 + hash)."""
        if not self.enabled:
            return
        with self._lock:
            self.payload_count += 1
            self.payload_time_sum += elapsed_s
            if elapsed_s > self.payload_time_max:
                self.payload_time_max = elapsed_s

    def summary_lines(self) -> list[str]:
        with self._lock:
            avg_gap = self.poll_gap_sum / self.poll_cycles if self.poll_cycles else 0.0
            avg_payload_ms = (
                (self.payload_time_sum / self.payload_count * 1000.0) if self.payload_count else 0.0
            )
            return [
                "[build-profile] ===== build profiling summary =====",
                f"[build-profile] poll loop: {self.poll_cycles} cycle(s), "
                f"avg gap {avg_gap * 1000:.0f}ms, max gap {self.poll_gap_max:.2f}s, "
                f"{self.poll_starved_cycles} starved cycle(s) (gap > 2x interval)",
                f"[build-profile] worst stall: {self.worst_gap:.2f}s, which hid "
                f"{self.worst_gap_completed} completion(s) (the 'jump' size)",
                f"[build-profile] submission: {self.op_count} execute_operation call(s) "
                f"({self.op_hits} hit / {self.op_misses} miss), "
                f"{self.op_time_sum - self.offload_time_sum:.2f}s ON-LOOP "
                f"(+ {self.offload_time_sum:.2f}s offloaded to the submit thread, "
                f"not blocking the loop), max call {self.op_time_max * 1000:.0f}ms",
                f"[build-profile] payload build: {self.payload_count} call(s), "
                f"total {self.payload_time_sum:.2f}s on-loop, avg {avg_payload_ms:.1f}ms, "
                f"max {self.payload_time_max * 1000:.0f}ms",
                "[build-profile] ===================================",
            ]

    def dump_summary(self) -> None:
        if not self.enabled:
            return
        for line in self.summary_lines():
            logger.info(line)


# Process-wide singleton. ``enabled`` is read once from the environment, which
# is set before the build subprocess starts.
profiler = BuildProfiler(enabled=_env_enabled())


def now() -> float:
    """Monotonic timer for measuring intervals (perf_counter)."""
    return perf_counter()
