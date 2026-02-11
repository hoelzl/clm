from pathlib import Path

from attrs import define

from clm.infrastructure.operation import Operation


@define
class File:
    path: Path

    async def get_processing_operation(
        self, target_dir: Path, stage: int | None = None
    ) -> "Operation":
        """Return operations to process this file.

        Args:
            target_dir: The target directory for output files.
            stage: If specified, return only operations for this execution stage.
                   If None, return all operations.

        Returns:
            An Operation (or Concurrently containing multiple operations).
        """
        from clm.infrastructure.operation import NoOperation

        return NoOperation()
