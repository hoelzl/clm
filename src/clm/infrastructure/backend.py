import logging
from abc import abstractmethod
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import TYPE_CHECKING

from attrs import define

if TYPE_CHECKING:
    from clm.cli.build_data_classes import BuildWarning
    from clm.infrastructure.messaging.base_classes import Payload
    from clm.infrastructure.operation import Operation
    from clm.infrastructure.utils.copy_dir_group_data import CopyDirGroupData
    from clm.infrastructure.utils.copy_file_data import CopyFileData
    from clm.infrastructure.utils.file import File

logger = logging.getLogger(__name__)


@define
class Backend(AbstractAsyncContextManager):
    @abstractmethod
    async def execute_operation(self, operation: "Operation", payload: "Payload") -> None: ...

    async def __aenter__(self) -> "Backend":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        return None

    @abstractmethod
    async def wait_for_completion(self) -> bool: ...

    @abstractmethod
    async def copy_file_to_output(self, copy_data: "CopyFileData"): ...

    @abstractmethod
    async def copy_dir_group_to_output(self, copy_data: "CopyDirGroupData") -> list["BuildWarning"]:
        """Copy a directory group to the output directory.

        Args:
            copy_data: Data for the copy operation including source dirs and output path.

        Returns:
            List of BuildWarning objects for any issues encountered (e.g., missing directories).
        """
        ...

    @abstractmethod
    async def delete_dependencies(self, file: "File") -> None: ...

    @abstractmethod
    async def delete_file(self, path: Path) -> None: ...

    async def cancel_jobs_for_file(self, file_path: Path) -> int:
        """Cancel all pending jobs for a given input file.

        This is used in watch mode when a file is modified to cancel any
        pending jobs before submitting new ones with updated content.

        Args:
            file_path: Path to the input file

        Returns:
            Number of jobs cancelled
        """
        # Default implementation does nothing (for backends that don't support job cancellation)
        return 0
