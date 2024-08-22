import asyncio
from abc import ABC, abstractmethod
from typing import Any, Iterable

from attrs import field, frozen


@frozen
class Operation(ABC):
    @abstractmethod
    async def exec(self, *args, **kwargs) -> Any: ...


@frozen
class NoOperation(Operation):
    async def exec(self, *args, **kwargs) -> Any:
        pass

    def __attrs_pre_init__(self):
        super().__init__()


@frozen
class Sequential(Operation):
    operations: Iterable[Operation]

    async def exec(self, *args, **kwargs) -> Any:
        for operation in self.operations:
            await operation.exec(*args, **kwargs)

    def __attrs_pre_init__(self):
        super().__init__()


# To avoid problem reports from PyCharm
def make_list(it: Iterable[Operation]) -> Iterable[Operation]:
    return list(it)


@frozen
class Concurrently(Operation):
    operations: Iterable[Operation] = field(converter=make_list)

    async def exec(self, *args, **kwargs) -> Any:
        await asyncio.gather(
            *[operation.exec(*args, **kwargs) for operation in self.operations]
        )

    def __attrs_pre_init__(self):
        super().__init__()
