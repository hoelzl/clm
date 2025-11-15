import asyncio
from time import time

from clx.infrastructure.backends.dummy_backend import DummyBackend
from clx.infrastructure.operation import Operation, Sequential, Concurrently

NUM_OPERATIONS = 100

# Increase this value to make the concurrent execution more noticeable
SLEEP_TIME = 0


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
