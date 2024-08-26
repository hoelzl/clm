import logging
from pathlib import Path
from typing import TYPE_CHECKING

from attrs import define

if TYPE_CHECKING:
    from clx_common.operation import Operation

FIRST_EXECUTION_STAGE = 1
LAST_EXECUTION_STAGE = 2
NUM_EXECUTION_STAGES = LAST_EXECUTION_STAGE - FIRST_EXECUTION_STAGE + 1

logger = logging.getLogger(__name__)


def execution_stages() -> list[int]:
    return list(range(FIRST_EXECUTION_STAGE, LAST_EXECUTION_STAGE + 1))


@define
class File:
    path: Path

    async def get_processing_operation(self, target_dir: Path) -> "Operation":
        from clx_common.operation import NoOperation
        return NoOperation()

    async def delete(self) -> None:
        self.path.unlink()