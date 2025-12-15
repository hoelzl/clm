import logging
from pathlib import Path
from typing import Any

from attrs import frozen

from clx.core.dir_group import DirGroup
from clx.infrastructure.backend import Backend
from clx.infrastructure.operation import Operation
from clx.infrastructure.utils.copy_dir_group_data import CopyDirGroupData

logger = logging.getLogger(__name__)


@frozen
class CopyDirGroupOperation(Operation):
    dir_group: "DirGroup"
    lang: str
    is_speaker: bool
    output_root: Path | None = None
    skip_toplevel: bool = False

    async def execute(self, backend: Backend, *args: Any, **kwargs: Any) -> Any:
        data = CopyDirGroupData(
            name=self.dir_group.name[self.lang],
            source_dirs=self.dir_group.source_dirs,
            relative_paths=self.dir_group.relative_paths,
            output_dir=self.dir_group.output_path(
                is_speaker=self.is_speaker,
                lang=self.lang,
                output_root=self.output_root,
                skip_toplevel=self.skip_toplevel,
            ),
            lang=self.lang,
            base_path=self.dir_group.base_path,
            recursive=self.dir_group.recursive,
        )
        await backend.copy_dir_group_to_output(data)
