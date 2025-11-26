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

## Future Enhancements

See `docs/developer-guide/architecture.md` for potential future enhancements.

---

**Last Updated**: 2025-11-26
