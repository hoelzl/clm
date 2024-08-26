import logging
from abc import abstractmethod
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING

from attrs import define

from clx_common.base_classes import Payload

if TYPE_CHECKING:
    from clx_common.operation import Operation

logger = logging.getLogger(__name__)


@define
class Backend(AbstractAsyncContextManager):
    @abstractmethod
    async def execute_operation(
        self, operation: "Operation", payload: Payload
    ) -> None: ...

    @abstractmethod
    async def wait_for_completion(self) -> None: ...


@define
class DummyBackend(Backend):
    async def __aexit__(self, __exc_type, __exc_value, __traceback):
        return None

    async def execute_operation(self, operation: "Operation", payload: Payload) -> None:
        logger.info(f"DummyBackend:Skipping operation:{operation!r}")

    async def wait_for_completion(self) -> None:
        pass
