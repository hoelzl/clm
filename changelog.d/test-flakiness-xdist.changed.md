- **Hardened the test suite against pytest-xdist contention flakes.** Removed the
  remaining oversubscription-sensitive synchronization that forced manual test
  re-runs under high worker counts: the worker-status test now gates the
  transient `"busy"` state on an event instead of polling a 1s window
  (empirically reproduced 1-in-3 at 64 workers); the watch-mode debounce tests
  await the scheduled task instead of a fixed `asyncio.sleep` margin; the
  `http_replay_mitm_manager` subprocess-spawning module is now `serial`; the
  fake-uvicorn lifecycle tests dropped a `_free_port()` TOCTOU bind; the
  heartbeat slow-write threshold is relaxed once session-wide via
  `CLM_HEARTBEAT_SLOW_WRITE_THRESHOLD_SECONDS` (production default unchanged at
  50ms). A scoped `@pytest.mark.flaky` safety net (pytest-rerunfailures, no
  global `--reruns`, `only_rerun`-filtered, `-rR`-loud) retries only the known
  thread-starvation families without masking real regressions. See
  `docs/claude/test-flakiness-investigation.md` and the "Four levers" section of
  `docs/developer-guide/testing.md`.
