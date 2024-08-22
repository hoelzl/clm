import asyncio
from time import time

from clx.operation import Operation, Sequential, Concurrently

NUM_OPERATIONS = 100

# Increase this value to make the concurrent execution more noticeable
SLEEP_TIME = 0.01


class TestOperation(Operation):
    counter = 0

    async def exec(self, *args, **kwargs):
        TestOperation.counter += 1


class Stage1Operation(TestOperation):
    async def exec(self, *args, **kwargs):
        assert TestOperation.counter < NUM_OPERATIONS
        await asyncio.sleep(SLEEP_TIME)
        await super().exec()


class Stage2Operation(TestOperation):
    async def exec(self, *args, **kwargs):
        assert TestOperation.counter >= NUM_OPERATIONS
        await asyncio.sleep(SLEEP_TIME)
        await super().exec()


def test_operations():
    op1 = Concurrently([Stage1Operation() for _ in range(NUM_OPERATIONS)])
    op2 = Concurrently([Stage2Operation() for _ in range(NUM_OPERATIONS)])
    unit = Sequential([op1, op2])

    start_time = time()
    asyncio.run(unit.exec())
    end_time = time()
    assert TestOperation.counter == 2 * NUM_OPERATIONS
    # Check that tasks are actually executed concurrently
    # Time should be approximately 2 * SLEEP_TIME, but we add some slack for
    # the overhead of creating and running the tasks
    run_time = end_time - start_time
    assert 2 * SLEEP_TIME - 0.1 <= run_time
    assert run_time < 5 * SLEEP_TIME + 0.1
