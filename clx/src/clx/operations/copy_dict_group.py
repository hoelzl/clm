import asyncio
import logging
from typing import Any

from attrs import frozen

from clx.dict_group import DictGroup
from clx.operation import Operation

logger = logging.getLogger(__name__)


@frozen
class CopyDictGroupOperation(Operation):
    dict_group: "DictGroup"
    lang: str

    async def exec(self, *args, **kwargs) -> Any:
        logger.debug(
            f"Copying dict group '{self.dict_group.name[self.lang]}' "
            f"for {self.lang}"
        )
        loop = asyncio.get_running_loop()
        tasks = [
            loop.run_in_executor(
                None, self.dict_group.copy_to_output(is_speaker, self.lang)
            )
            for is_speaker in [False, True]
        ]
        await asyncio.gather(*tasks)
