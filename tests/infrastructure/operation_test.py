import asyncio
from time import time

from clx.infrastructure.backends.dummy_backend import DummyBackend
from clx.infrastructure.operation import Concurrently, Operation, Sequential

NUM_OPERATIONS = 100

# Increase this value to make the concurrent execution more noticeable
SLEEP_TIME = 0

# Track maximum concurrent operations for testing
max_concurrent = 0
current_concurrent = 0


class PytestOperation(Operation):
    counter = 0

    async def execute(self, backend, *args, **kwargs):
        PytestOperation.counter += 1


class Stage1Operation(PytestOperation):
    async def execute(self, backend, *args, **kwargs):
        assert PytestOperation.counter < NUM_OPERATIONS
        await asyncio.sleep(SLEEP_TIME)
        await super().execute(backend)


class Stage2Operation(PytestOperation):
    async def execute(self, backend, *args, **kwargs):
        assert PytestOperation.counter >= NUM_OPERATIONS
        await asyncio.sleep(SLEEP_TIME)
        await super().execute(backend)


def test_operations():
    op1 = Concurrently([Stage1Operation() for _ in range(NUM_OPERATIONS)])
    op2 = Concurrently([Stage2Operation() for _ in range(NUM_OPERATIONS)])
    backend = DummyBackend()

    unit = Sequential([op1, op2])

    start_time = time()
    asyncio.run(unit.execute(backend))
    end_time = time()
    assert PytestOperation.counter == 2 * NUM_OPERATIONS
    # Check that tasks are actually executed concurrently
    # Time should be approximately 2 * SLEEP_TIME, but we add some slack for
    # the overhead of creating and running the tasks
    run_time = end_time - start_time
    assert 2 * SLEEP_TIME - 0.1 <= run_time
    assert run_time < 5 * SLEEP_TIME + 0.1


class ConcurrencyTrackingOperation(Operation):
    """Operation that tracks concurrent execution."""

    async def execute(self, backend, *args, **kwargs):
        global max_concurrent, current_concurrent

        current_concurrent += 1
        if current_concurrent > max_concurrent:
            max_concurrent = current_concurrent

        # Simulate some work
        await asyncio.sleep(0.01)

        current_concurrent -= 1


def test_concurrency_limiting():
    """Test that Concurrently respects max_concurrency limit."""
    global max_concurrent, current_concurrent

    # Reset tracking variables
    max_concurrent = 0
    current_concurrent = 0

    # Create 100 operations with max concurrency of 10
    operations = [ConcurrencyTrackingOperation() for _ in range(100)]
    concurrent_op = Concurrently(operations, max_concurrency=10)

    backend = DummyBackend()
    asyncio.run(concurrent_op.execute(backend))

    # Verify that we never exceeded the limit
    assert max_concurrent <= 10, f"Max concurrent operations was {max_concurrent}, expected <= 10"
    assert max_concurrent > 0, "No concurrent execution detected"


def test_concurrency_default_limit():
    """Test that Concurrently uses default limit from environment."""
    import os

    from clx.infrastructure.operation import DEFAULT_MAX_CONCURRENCY

    # Verify default is set
    assert DEFAULT_MAX_CONCURRENCY > 0

    # Create operations without specifying max_concurrency
    operations = [ConcurrencyTrackingOperation() for _ in range(10)]
    concurrent_op = Concurrently(operations)

    # Should use default limit
    assert concurrent_op.max_concurrency == DEFAULT_MAX_CONCURRENCY


def test_concurrency_unlimited():
    """Test that Concurrently can run with unlimited concurrency."""
    global max_concurrent, current_concurrent

    # Reset tracking variables
    max_concurrent = 0
    current_concurrent = 0

    # Create 20 operations with no limit (explicitly set to None)
    operations = [ConcurrencyTrackingOperation() for _ in range(20)]
    concurrent_op = Concurrently(operations, max_concurrency=None)

    backend = DummyBackend()
    asyncio.run(concurrent_op.execute(backend))

    # With unlimited concurrency and 0.01s sleep, we should see high concurrency
    # (All 20 should run at once given the timing)
    assert max_concurrent >= 15, f"Expected high concurrency, got {max_concurrent}"


def test_no_operation():
    """Test NoOperation execute does nothing."""
    from clx.infrastructure.operation import NoOperation

    backend = DummyBackend()
    no_op = NoOperation()

    # Execute should complete without error
    asyncio.run(no_op.execute(backend))


def test_operation_service_name_default():
    """Test that Operation.service_name returns None by default."""
    # Use a concrete subclass to test
    op = ConcurrencyTrackingOperation()

    # Default service_name should return None
    assert op.service_name is None


def test_no_operation_service_name():
    """Test that NoOperation.service_name returns None."""
    from clx.infrastructure.operation import NoOperation

    no_op = NoOperation()

    assert no_op.service_name is None
