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

### ~~Fix Spurious "Aborted!" Message After Successful Builds~~ (FIXED)

**Status**: ✅ FIXED (2025-11-26)

**Fix Applied**: Three-part fix for signal handling and late error reports:

1. **Moved signal handler registration from `main()` (async) to `build()` (sync)**:
   - Signal handlers are now registered in `build()` BEFORE `asyncio.run()` starts
   - This ensures handlers remain active during ALL of `asyncio.run()` including cleanup

2. **Install no-op signal handlers after successful build**:
   - After a successful build, install "ignore signal" handlers instead of restoring defaults
   - This prevents "Aborted!" from being printed when late signals arrive during:
     - Click's cleanup after `build()` returns
     - Python's atexit handlers during interpreter shutdown
     - Worker subprocess cleanup

3. **Suppress late error/warning reports after build finishes** (in BuildReporter):
   - Added `_build_finished` flag that is set when `finish_build()` is called
   - `report_error()` and `report_warning()` check this flag and return early if set
   - This prevents spurious errors from worker shutdown (due to process termination
     interrupting ongoing work) from appearing after the build summary

**Key Changes**:
- `src/clx/cli/main.py`: Signal handlers registered in `build()`, no-op handlers after success
- `src/clx/cli/build_reporter.py`: `_build_finished` flag to suppress late error reports

**Original Issue**: After a successful build completes, users sometimes saw "Aborted!" printed to the terminal along with spurious error messages. This was caused by timing interactions between signal handlers, asyncio cleanup, Click's exception handling, worker subprocess termination signals, and late-arriving error reports from interrupted workers.

---

## Future Enhancements

See `docs/developer-guide/architecture.md` for potential future enhancements.

---

**Last Updated**: 2025-11-26 (Fixed "Aborted!" message bug)
