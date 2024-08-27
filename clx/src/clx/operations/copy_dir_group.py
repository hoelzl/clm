import logging
from typing import Any

from attrs import frozen
from clx_common.backend import Backend
from clx_common.operation import Operation
from clx_common.utils.copy_dir_group_data import CopyDirGroupData

from clx.dir_group import DirGroup

logger = logging.getLogger(__name__)


@frozen
class CopyDirGroupOperation(Operation):
    dir_group: "DirGroup"
    lang: str
    is_speaker: bool

    async def execute(self, backend: Backend, *args, **kwargs) -> Any:
        data = CopyDirGroupData(
            name=self.dir_group.name[self.lang],
            source_dirs=self.dir_group.source_dirs,
            relative_paths=self.dir_group.relative_paths,
            output_dir=self.dir_group.output_path(
                is_speaker=self.is_speaker, lang=self.lang
            ),
            lang=self.lang,
        )
        await backend.copy_dir_group_to_output(data)
