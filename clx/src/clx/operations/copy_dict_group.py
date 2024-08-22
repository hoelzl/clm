import asyncio
import logging
from typing import Any

from attrs import frozen

from clx.backend import Backend
from clx.dict_group import DictGroup
from clx.operation import Operation

logger = logging.getLogger(__name__)


@frozen
class CopyDictGroupOperation(Operation):
    dict_group: "DictGroup"
    lang: str

    async def execute(self, backend: Backend, *args, **kwargs) -> Any:
        logger.debug(
            f"Copying dict group '{self.dict_group.name[self.lang]}' "
            f"for {self.lang}"
        )
        # TODO: This should probably be moved to the backend
        # (including the DictGroup.copy_to_output operation)
        try:
            await asyncio.gather(
                self.dict_group.copy_to_output(False, self.lang),
                self.dict_group.copy_to_output(True, self.lang),
            )
        except Exception as e:
            logger.exception(f"Error while copying {self.dict_group}: {e}")
            raise
