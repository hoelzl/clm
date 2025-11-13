import asyncio
import logging
import shutil
from abc import ABC
from asyncio import TaskGroup
from pathlib import Path

from attrs import define
from clx.course_file import CourseFile
from clx_common.utils.file import File

from clx_common.backend import Backend
from clx_common.utils.copy_dir_group_data import CopyDirGroupData
from clx_common.utils.copy_file_data import CopyFileData
from clx_common.utils.path_utils import SKIP_DIRS_FOR_OUTPUT, SKIP_DIRS_PATTERNS

logger = logging.getLogger(__name__)


@define
class LocalOpsBackend(Backend, ABC):
    async def copy_file_to_output(self, copy_data: CopyFileData):
        input_path = copy_data.relative_input_path
        output_path = copy_data.output_path
        logger.info(f"Copying {input_path} to {output_path}")
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._copy_file_to_output_sync, copy_data)
        except Exception as e:
            logger.error(
                f"Error while copying file '{input_path}' to {output_path}: {e}"
            )
            logger.debug(f"Error traceback:", exc_info=e)
            raise

    @staticmethod
    def _copy_file_to_output_sync(copy_data: CopyFileData):
        if not copy_data.output_path.parent.exists():
            copy_data.output_path.parent.mkdir(parents=True, exist_ok=True)

        # Check if source file exists before attempting to copy
        if not copy_data.input_path.exists():
            error_msg = (
                f"Source file does not exist: {copy_data.input_path}\n"
                f"This may indicate that a previous conversion step failed. "
                f"Check logs for errors from PlantUML, Draw.io, or other converters."
            )
            logger.error(error_msg)
            raise FileNotFoundError(error_msg)

        shutil.copyfile(copy_data.input_path, copy_data.output_path)

    async def copy_dir_group_to_output(self, copy_data: "CopyDirGroupData"):
        logger.debug(f"Copying '{copy_data.name}' to output for {copy_data.lang}")
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, self._copy_dir_group_to_output_sync, copy_data
            )
        except Exception as e:
            logger.error(
                f"Error while copying '{copy_data.name}' "
                f"to output for {copy_data.lang}: {e}"
            )
            logger.debug(f"Error traceback for '{copy_data.name}':", exc_info=e)
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

    async def delete_dependencies(self, file: File) -> None:
        logger.debug(f"Deleting '{file.path.name}'")
        if isinstance(file, CourseFile):
            try:
                async with TaskGroup() as tg:
                    for go in file.generated_outputs:
                        logger.debug(f"Deleting generated output '{go.name}'")
                        tg.create_task(self.delete_file(go))
                file.generated_outputs.clear()
            except Exception as e:
                logger.error(
                    f"Error while deleting dependencies for '{file.path.name}':{e}"
                )
                logger.debug(f"Error traceback for '{file.path.name}':", exc_info=e)
                raise

    async def delete_file(self, path: Path) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._delete_file_sync, path)

    @staticmethod
    def _delete_file_sync(path: Path) -> None:
        logger.debug(f"Deleting '{path.name}'")
        path.unlink(missing_ok=True)