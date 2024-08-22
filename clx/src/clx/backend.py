import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from attrs import define

if TYPE_CHECKING:
    from clx.operation import Operation

logger = logging.getLogger(__name__)


@define
class Backend(ABC):
    @abstractmethod
    def execute_operation(self, operation: "Operation", *args, **kwargs) -> None:
        ...


@define
class DummyBackend(Backend):
    def execute_operation(self, operation: "Operation", *args, **kwargs) -> None:
        logger.info(f"DummyBackend:Skipping operation:{operation!r}")