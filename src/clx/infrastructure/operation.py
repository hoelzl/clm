import asyncio
import os
from abc import ABC, abstractmethod
from collections.abc import Iterable

from attrs import field, frozen

from clx.infrastructure.backend import Backend

# Default concurrency limit for Concurrently operations
# This prevents resource exhaustion on Windows and other platforms
# Can be overridden via environment variable CLX_MAX_CONCURRENCY
DEFAULT_MAX_CONCURRENCY = int(os.getenv('CLX_MAX_CONCURRENCY', '50'))


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
    max_concurrency: int | None = field(default=DEFAULT_MAX_CONCURRENCY)

    async def execute(self, backend: Backend, *args, **kwargs) -> None:
        # If max_concurrency is explicitly set to None, use unbounded concurrency
        # Otherwise use the specified limit (default is DEFAULT_MAX_CONCURRENCY)
        if self.max_concurrency is None:
            await asyncio.gather(
                *[operation.execute(backend, *args, **kwargs) for operation in self.operations]
            )
        else:
            # Use a semaphore to limit concurrent operations
            semaphore = asyncio.Semaphore(self.max_concurrency)

            async def execute_with_limit(operation: Operation):
                async with semaphore:
                    await operation.execute(backend, *args, **kwargs)

            await asyncio.gather(
                *[execute_with_limit(op) for op in self.operations]
            )

    def __attrs_pre_init__(self):
        super().__init__()
