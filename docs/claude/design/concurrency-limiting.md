# Concurrency Limiting for Windows Compatibility

## Problem

When processing large courses with hundreds of notebooks, CLM was experiencing resource exhaustion on Windows due to unbounded concurrent operations:

### Symptoms
- **ZMQ Connection Reset**: `Assertion failed: Connection reset by peer [10054]` from Jupyter kernel's ZeroMQ connections
- **Windows AsyncIO Error**: `WinError 995: The I/O operation has been aborted` from ProactorEventLoop
- **Process Spawn Storm**: 200+ simultaneous worker processes overwhelming system resources

### Root Cause
The `Concurrently` operation class used `asyncio.gather()` with no concurrency limit. When processing a course with 448 jobs, this created hundreds of concurrent:
- Subprocess workers
- IPython kernel instances
- ZMQ connections
- AsyncIO I/O operations

This overwhelmed:
- Windows process creation system
- TCP port allocation for ZMQ
- AsyncIO's I/O completion port management

## Solution

Added semaphore-based concurrency limiting to the `Concurrently` operation class.

### Implementation

**File**: `src/clm/infrastructure/operation.py`

1. **Default Limit**: Set to 50 concurrent operations (configurable via `CLM_MAX_CONCURRENCY` environment variable)
2. **Semaphore Control**: Uses `asyncio.Semaphore` to limit concurrent execution
3. **Backward Compatibility**: Can be explicitly set to `None` for unlimited concurrency

### Key Changes

```python
# Default concurrency limit
DEFAULT_MAX_CONCURRENCY = int(os.getenv('CLM_MAX_CONCURRENCY', '50'))

@frozen
class Concurrently(Operation):
    operations: Iterable[Operation] = field(converter=make_list)
    max_concurrency: int | None = field(default=DEFAULT_MAX_CONCURRENCY)

    async def execute(self, backend: Backend, *args, **kwargs) -> None:
        if self.max_concurrency is None:
            # Unlimited concurrency (old behavior)
            await asyncio.gather(
                *[operation.execute(backend, *args, **kwargs) for operation in self.operations]
            )
        else:
            # Limited concurrency using semaphore
            semaphore = asyncio.Semaphore(self.max_concurrency)

            async def execute_with_limit(operation: Operation):
                async with semaphore:
                    await operation.execute(backend, *args, **kwargs)

            await asyncio.gather(
                *[execute_with_limit(op) for op in self.operations]
            )
```

## Configuration

### Environment Variable

```bash
# Set custom concurrency limit
export CLM_MAX_CONCURRENCY=25

# Windows PowerShell
$env:CLM_MAX_CONCURRENCY=25

# Windows CMD
set CLM_MAX_CONCURRENCY=25
```

### Programmatic Override

```python
from clm.infrastructure.operation import Concurrently

# Custom limit
op = Concurrently(operations, max_concurrency=10)

# Unlimited (not recommended on Windows)
op = Concurrently(operations, max_concurrency=None)
```

## Testing

Added comprehensive tests in `tests/infrastructure/operation_test.py`:

1. **test_concurrency_limiting**: Verifies semaphore respects limit
2. **test_concurrency_default_limit**: Ensures default is applied
3. **test_concurrency_unlimited**: Tests opt-out behavior

All tests pass on Windows and Linux.

## Performance Impact

- **Startup**: Slightly slower startup for large batches (operations queue behind semaphore)
- **Resource Usage**: Dramatically reduced memory and process count
- **Stability**: Eliminates ZMQ connection errors and AsyncIO crashes on Windows
- **Throughput**: Overall throughput similar due to better resource management

## Recommendations

### For Windows Users
- **Default (50)**: Good for most systems
- **High-spec machines**: Can increase to 75-100
- **Low-spec/VMs**: Reduce to 25

### For Linux/macOS Users
- **Default (50)**: Conservative, can increase
- **High-spec machines**: Can set to 100+ or None for unlimited
- **Docker environments**: Consider container limits

### For CI/CD
- **GitHub Actions**: Use 25 (limited resources)
- **Dedicated CI**: Can use 50-75
- **Docker-based workers**: Use default

## Future Improvements

1. **Auto-detection**: Detect platform and adjust default accordingly
2. **Per-worker-type limits**: Different limits for notebook vs diagram workers
3. **Dynamic adjustment**: Monitor system resources and adjust limit
4. **Configuration file**: Add to CLM config system

## Related Issues

This fix resolves:
- Windows AsyncIO ProactorEventLoop crashes
- ZMQ connection reset errors
- IPython kernel startup failures
- Resource exhaustion with large courses

---

**Date**: 2025-11-16
**Author**: Claude Code
**Status**: Implemented and tested
