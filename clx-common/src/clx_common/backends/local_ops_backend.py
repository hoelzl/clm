import asyncio
import logging
import shutil
from abc import ABC

from attrs import define

from clx_common.backend import Backend
from clx_common.utils.copy_dir_group_data import CopyDirGroupData
from clx_common.utils.copy_file_data import CopyFileData
from clx_common.utils.path_utils import SKIP_DIRS_FOR_OUTPUT, SKIP_DIRS_PATTERNS

logger = logging.getLogger(__name__)


@define
class LocalOpsBackend(Backend, ABC):
    async def copy_file_to_output(self, copy_data: CopyFileData):
        logger.info(
            f"Copying {copy_data.relative_input_path} to {copy_data.output_path}"
        )
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._copy_file_to_output_sync, copy_data)
        except Exception as e:
            logger.exception(
                f"Error while copying file '{copy_data.relative_input_path}' "
                f"to {copy_data.output_path}: {e}"
            )
            raise

    @staticmethod
    def _copy_file_to_output_sync(copy_data: CopyFileData):
        if not copy_data.output_path.parent.exists():
            copy_data.output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(copy_data.input_path, copy_data.output_path)

    async def copy_dir_group_to_output(self, copy_data: "CopyDirGroupData"):
        logger.debug(f"Copying '{copy_data.name}' to output for {copy_data.lang}")
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, self._copy_dir_group_to_output_sync, copy_data
            )
        except Exception as e:
            logger.exception(
                f"Error while copying '{copy_data.name}' "
                f"to output for {copy_data.lang}: {e}"
            )
            raise

    @staticmethod
    def _copy_dir_group_to_output_sync(copy_data: "CopyDirGroupData"):
        for source_dir, relative_path in zip(
            copy_data.source_dirs, copy_data.relative_paths
        ):
            if not source_dir.exists():
                logger.error(f"Source directory does not exist: {source_dir}")
                continue
            output_dir = copy_data.output_dir / relative_path
            logger.debug(f"Copying '{source_dir}' to {output_dir}")
            output_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(
                source_dir,
                output_dir,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(
                    *SKIP_DIRS_FOR_OUTPUT, *SKIP_DIRS_PATTERNS
                ),
            )
