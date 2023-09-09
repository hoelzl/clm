from concurrent.futures import Executor, Future

from attr import define, field


@define
class TestExecutor(Executor):
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
