import asyncio
from abc import ABC, abstractmethod
from typing import Iterable

from attrs import field, frozen

from clx_common.backend import Backend


@frozen
class Operation(ABC):
    @abstractmethod
    async def execute(self, backend: Backend, *args, **kwargs) -> None:
        """Execute the operation on the given service."""
        ...

    @property
    def service_name(self) -> str | None:
        """
        The name of the backend service this operation requests.

        Returns None, if the operation can be performed without invoking a service.

        Otherwise, returns a string that the backend can use to determine which service
        it should invoke.

        Each backend service has a set of properties that it expects from the operation
        provided to the service. The operation must provide all these properties.
        """
        return None


@frozen
class NoOperation(Operation):
    async def execute(self, backend: Backend, *args, **kwargs) -> None:
        pass

    def __attrs_pre_init__(self):
        super().__init__()


@frozen
class Sequential(Operation):
    operations: Iterable[Operation]

    async def execute(self, backend: Backend, *args, **kwargs) -> None:
        for operation in self.operations:
            await operation.execute(backend, *args, **kwargs)

    def __attrs_pre_init__(self):
        super().__init__()


# To avoid problem reports from PyCharm
def make_list(it: Iterable[Operation]) -> Iterable[Operation]:
    return list(it)


@frozen
class Concurrently(Operation):
    operations: Iterable[Operation] = field(converter=make_list)

    async def execute(self, backend: Backend, *args, **kwargs) -> None:
        await asyncio.gather(
            *[operation.execute(backend, *args, **kwargs) for operation in self.operations]
        )

    def __attrs_pre_init__(self):
        super().__init__()
