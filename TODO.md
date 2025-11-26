# CLX TODO List

This file tracks known issues and planned improvements for the CLX project.

## Bugs / Technical Debt

### Fix Flaky Test: `test_worker_tracks_statistics`

**Location**: `tests/infrastructure/workers/test_worker_base.py:326`

**Issue**: The test `test_worker_tracks_statistics` is timing-sensitive and occasionally fails with:
```
assert avg_time > 0
E   assert 0.0 > 0
```

**Root Cause**: The mock worker processes jobs nearly instantaneously (no real work), so `avg_processing_time` can be 0.0 due to floating-point precision or the time measurement being too fast.

**Proposed Fix Options**:
1. Add a small artificial delay in the mock worker's `process_job()` method (e.g., `time.sleep(0.001)`)
2. Change the assertion to `assert avg_time >= 0` if zero is acceptable
3. Mock the time measurement to ensure non-zero processing time
4. Use `pytest.approx` with appropriate tolerance

**Priority**: Low (test infrastructure, not affecting production code)

**Related Files**:
- `tests/infrastructure/workers/test_worker_base.py`
- `src/clx/infrastructure/workers/worker_base.py`

---

### Fix Spurious "Aborted!" Message After Successful Builds

**Location**: `src/clx/cli/main.py` (lines ~715-780 and ~960-1010)

**Issue**: After a successful build completes, users sometimes see "Aborted!" printed to the terminal, even though the build finished successfully. This happens inconsistently.

**Symptom**:
```
Building course: Machine Learning (AZAV)
Total files: 191
... (successful build output) ...
Aborted!
```

**Root Cause Analysis**:

The issue involves complex timing interactions between signal handlers, asyncio, and Click:

1. **Signal Handler Flow**:
   - Custom signal handlers are registered at the start of `main()`
   - They raise `KeyboardInterrupt` to trigger graceful shutdown
   - Signal handlers are restored to defaults in a `finally` block before `main()` returns

2. **The Timing Problem**:
   - Build completes successfully, `completion_status[0] = True` is set
   - Signal handlers are restored to Python defaults in `finally` block
   - `main()` returns, but `asyncio.run()` still needs to do cleanup
   - `asyncio.run()` cleanup includes: cancelling pending tasks, closing the event loop
   - If a signal arrives during this cleanup window, Python's DEFAULT handler raises `KeyboardInterrupt`
   - Click catches this and prints "Aborted!"

3. **What We've Tried**:
   - Removed logging from signal handlers (fixed reentrant logging error)
   - Used mutable container `completion_status = [False]` to track completion
   - Set `completion_status[0] = True` before signal handlers are restored
   - Catch `KeyboardInterrupt` in `build()` and suppress if `completion_status[0]` is True

4. **Why It Still Happens**:
   The mutable container approach should work, but signals can still arrive:
   - After `main()` returns but during `asyncio.run()` internal cleanup
   - The `KeyboardInterrupt` is raised OUTSIDE our try/except in `build()`
   - Or there may be other code paths where signals aren't handled

**Investigation Needed**:

1. **Trace the exact signal timing**:
   - Add debug output (to stderr, not logging) to see exactly when signals arrive
   - Determine if it's during `asyncio.run()` cleanup or elsewhere

2. **Check if `asyncio.run()` swallows our completion status**:
   - The signal might be raised after `asyncio.run()` returns but before `build()` continues
   - This seems unlikely but worth verifying

3. **Alternative approaches to investigate**:

   a. **Don't restore signal handlers until after asyncio.run() returns**:
      ```python
      # In build(), not main():
      original_sigint = signal.signal(signal.SIGINT, shutdown_handler)
      try:
          asyncio.run(main(...))
      finally:
          signal.signal(signal.SIGINT, original_sigint)
      ```

   b. **Use `asyncio.Runner` for more control** (Python 3.11+):
      ```python
      with asyncio.Runner() as runner:
          runner.run(main(...))
      # Cleanup is done, now restore signals
      ```

   c. **Suppress KeyboardInterrupt at the Click level**:
      - Subclass Click's `Command` or use a custom exception handler
      - Check completion status before letting Click handle the exception

   d. **Use atexit to track completion**:
      ```python
      import atexit
      _build_completed = False
      def _check_completion():
          if _build_completed:
              # Suppress any pending exceptions somehow
      atexit.register(_check_completion)
      ```

   e. **Move signal handling entirely to the sync layer**:
      - Don't register signal handlers inside `main()` (async)
      - Handle everything in `build()` (sync) wrapper

4. **Test scenarios to reproduce**:
   - Run build with `--output-mode verbose` vs without (different timing)
   - Run with different numbers of workers
   - Run with `--notebook-workers 1` for more deterministic timing
   - Add artificial delays to find the timing window

**Related Files**:
- `src/clx/cli/main.py` - Signal handlers and build orchestration
- `docs/developer-guide/architecture.md` - Known Issues section

**Priority**: Medium (cosmetic issue but confusing for users)

**Branch**: `claude/fix-worker-orphan-processes` (PR #87) contains partial fixes

---

## Future Enhancements

See `docs/developer-guide/architecture.md` for potential future enhancements.

---

**Last Updated**: 2025-11-26
