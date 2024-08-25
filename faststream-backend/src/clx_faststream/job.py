from asyncio import Future, Task
from datetime import datetime
from typing import Any

from attrs import define

from clx.operation import Operation


@define
class Job:
    """
    Wrapper around an asyncio Task that performs an operation, possibly over a network

    Jobs may also represent the result of an operation that was already completed.
    """
    correlation_id: str
    creation_time: datetime
    operation: Operation
    task: Task | None
    _result: Any = None
    _exception: Any = None

    def cancel(self):
        if self.task:
            self.task.cancel()

    def cancelled(self):
        if self.task:
            return self.task.cancelled()
        return False

    def done(self):
        if self.task:
            return self.task.done()
        return True

    def result(self):
        if self.task:
            return self.task.result()
        return self._result

    def exception(self):
        if self.task:
            return self.task.exception()
        return self._exception