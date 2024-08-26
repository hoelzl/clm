import asyncio
import logging
from typing import Any

from attrs import frozen

from clx_common.backend import Backend
from clx.dir_group import DirGroup
from clx_common.operation import Operation

logger = logging.getLogger(__name__)


@frozen
class CopyDictGroupOperation(Operation):
    dict_group: "DirGroup"
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
