from concurrent.futures import Executor, Future
from typing import ClassVar

from attr import define, field

from clm.core.notifier import Notifier


@define
class TestExecutor(Executor):
    # Make Pytest ignore this class when collecting tests.
    __test__: ClassVar = False
    _futures: list[Future] = field(factory=list)

    def submit(self, fn, *args, **kwargs):
        future = Future()
        self._futures.append(future)
        try:
            future.set_result(fn(*args, **kwargs))
        except Exception as err:
            future.set_exception(err)
        return future

    def shutdown(self, wait=True, *, cancel_futures=False):
        # For a more correct implementation we ought to set a flag
        # indicating that the executor is shutting down and then
        # check that flag in submit() and map() to raise an exception
        # if the executor is shutting down.
        pass


class TestNotifier(Notifier):
    # Make Pytest ignore this class when collecting tests.
    __test__: ClassVar = False

    def __init__(self):
        self.processed_data_source_count = 0
        self.wrote_to_target_count = 0

    def processed_data_source(self):
        self.processed_data_source_count += 1

    def wrote_to_target(self):
        self.wrote_to_target_count += 1
