from pathlib import Path

from attrs import define

from clx_common.operation import Operation


@define
class File:
    path: Path

    async def get_processing_operation(self, target_dir: Path) -> "Operation":
        from clx_common.operation import NoOperation
        return NoOperation()
