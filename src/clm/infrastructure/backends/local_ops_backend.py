import asyncio
import logging
import shutil
import sys
from abc import ABC
from pathlib import Path

# TaskGroup is available in Python 3.11+
if sys.version_info >= (3, 11):
    from asyncio import TaskGroup
else:
    from asyncio import gather as _gather

    class TaskGroup:
        """Minimal TaskGroup shim for Python 3.10 compatibility."""

        def __init__(self):
            self._tasks: list = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            if self._tasks:
                await _gather(*self._tasks)

        def create_task(self, coro):
            task = asyncio.create_task(coro)
            self._tasks.append(task)
            return task


from attrs import define

from clm.cli.build_data_classes import BuildWarning
from clm.core.course_file import CourseFile
from clm.core.output_write_registry import (
    WriteOutcome,
    is_image_path,
)
from clm.infrastructure.backend import Backend
from clm.infrastructure.utils.copy_dir_group_data import CopyDirGroupData
from clm.infrastructure.utils.copy_file_data import CopyFileData
from clm.infrastructure.utils.file import File
from clm.infrastructure.utils.path_utils import (
    SKIP_DIRS_FOR_OUTPUT,
    SKIP_DIRS_PATTERNS,
    SKIP_OUTPUT_FILE_GLOBS,
)

logger = logging.getLogger(__name__)


@define
class LocalOpsBackend(Backend, ABC):
    async def copy_file_to_output(self, copy_data: CopyFileData):
        input_path = copy_data.relative_input_path
        output_path = copy_data.output_path
        logger.info(f"Copying {input_path} to {output_path}")

        # Register every write — including image-path sources — with
        # OutputWriteRegistry. ImageRegistry still records the output
        # path for the stray-file sweep and continues to catch source
        # rel-path collisions across topics in shared mode, but only
        # OutputWriteRegistry compares content hashes at the output
        # destination. Without that, two sources writing different bytes
        # to the same output (e.g. a static ``img/diagram.png`` and a
        # ``pu/diagram.pu`` rendering to ``img/diagram.png``) were
        # silently last-writer-wins.
        if copy_data.input_path.exists():
            abs_output = output_path if output_path.is_absolute() else output_path.resolve()
            if is_image_path(copy_data.input_path):
                self.image_registry.record_output_write(abs_output)
            write_result = self.output_write_registry.record_write(
                abs_output,
                content_source=copy_data.input_path,
                source=copy_data.input_path,
            )
            if write_result.outcome == WriteOutcome.DEDUP:
                logger.debug(
                    f"Output dedup: skipping copy to {abs_output} (identical "
                    f"content already written from "
                    f"{write_result.entry.first_writer_source})"
                )
                return
            if write_result.outcome == WriteOutcome.CONFLICT:
                logger.warning(
                    f"Output path conflict at {abs_output}: prior writer "
                    f"{write_result.entry.first_writer_source}, new writer "
                    f"{copy_data.input_path} (last writer wins)"
                )
            elif write_result.outcome == WriteOutcome.LARGE_FILE_COLLISION:
                logger.debug(
                    f"Large-file collision at {abs_output} from "
                    f"{copy_data.input_path} (over hash limit; counted as collision)"
                )

            # Hash-aware skip: if the destination already holds identical
            # content (typically from a prior build), avoid the copy so
            # mtime is preserved and git's stat-cache remains valid.
            if self.output_write_registry.is_destination_identical(
                abs_output, content_source=copy_data.input_path
            ):
                logger.debug(f"Hash-aware skip: {abs_output} already has identical content")
                return

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._copy_file_to_output_sync, copy_data)
        except Exception as e:
            logger.error(f"Error while copying file '{input_path}' to {output_path}: {e}")
            logger.debug("Error traceback:", exc_info=e)
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

    async def copy_dir_group_to_output(self, copy_data: "CopyDirGroupData") -> list[BuildWarning]:
        """Copy a directory group to the output directory.

        Args:
            copy_data: Data for the copy operation including source dirs and output path.

        Returns:
            List of BuildWarning objects for any issues encountered (e.g., missing directories).
        """
        logger.debug(f"Copying '{copy_data.name}' to output for {copy_data.lang}")
        try:
            loop = asyncio.get_running_loop()
            warnings = await loop.run_in_executor(
                None, self._copy_dir_group_to_output_sync, copy_data
            )
        except Exception as e:
            logger.error(
                f"Error while copying '{copy_data.name}' to output for {copy_data.lang}: {e}"
            )
            logger.debug(f"Error traceback for '{copy_data.name}':", exc_info=e)
            raise

        # Register each per-file write after the copy completes. Walking
        # the output tree post-hoc keeps registry access on the event
        # loop (the registry is not thread-safe) and reuses shutil's
        # ignore-pattern handling for free. The dedup-skip semantic
        # therefore doesn't apply to <dir-group> writes — but conflict
        # detection across overlapping dir-group writes still works,
        # matching the worker-readback hook's "warn-only" trade-off.
        self._register_dir_group_writes(copy_data)
        return warnings

    def _register_dir_group_writes(self, copy_data: "CopyDirGroupData") -> None:
        if copy_data.base_path is not None and copy_data.base_path.exists():
            for item in copy_data.base_path.iterdir():
                if item.is_file():
                    dest = copy_data.output_dir / item.name
                    self._record_dir_group_write(source=item, dest=dest)

        for source_dir, relative_path in zip(
            copy_data.source_dirs, copy_data.relative_paths, strict=False
        ):
            if not source_dir.exists():
                continue
            target_dir = copy_data.output_dir / relative_path
            if not target_dir.exists():
                continue
            if copy_data.recursive:
                for out_file in target_dir.rglob("*"):
                    if not out_file.is_file():
                        continue
                    rel = out_file.relative_to(target_dir)
                    source_file = source_dir / rel
                    self._record_dir_group_write(source=source_file, dest=out_file)
            else:
                for item in source_dir.iterdir():
                    if not item.is_file():
                        continue
                    dest = target_dir / item.name
                    if dest.exists():
                        self._record_dir_group_write(source=item, dest=dest)

    def _record_dir_group_write(self, *, source: Path, dest: Path) -> None:
        abs_dest = dest if dest.is_absolute() else dest.resolve()
        if is_image_path(source):
            self.image_registry.record_output_write(abs_dest)
        try:
            self.output_write_registry.record_write(
                abs_dest,
                content_source=dest,
                source=source,
            )
        except Exception as exc:
            logger.debug(f"Could not register dir-group write {abs_dest} from {source}: {exc}")

    @staticmethod
    def _copy_dir_group_to_output_sync(
        copy_data: "CopyDirGroupData",
    ) -> list[BuildWarning]:
        """Synchronously copy directory group to output.

        Args:
            copy_data: Data for the copy operation.

        Returns:
            List of BuildWarning objects for any missing source directories.
        """
        warnings: list[BuildWarning] = []

        # Copy files from base_path if specified (include-root-files attribute)
        if copy_data.base_path is not None:
            if copy_data.base_path.exists():
                copy_data.output_dir.mkdir(parents=True, exist_ok=True)
                for item in copy_data.base_path.iterdir():
                    if item.is_file():
                        dest = copy_data.output_dir / item.name
                        logger.debug(f"Copying root file '{item}' to {dest}")
                        shutil.copy2(item, dest)
            else:
                warning_msg = (
                    f"Base directory does not exist: {copy_data.base_path}\n"
                    f"The include-root-files path was not found. "
                    f"Please verify the path in your spec file."
                )
                logger.warning(warning_msg)
                warnings.append(
                    BuildWarning(
                        category="missing_directory",
                        message=warning_msg,
                        severity="high",
                        file_path=str(copy_data.base_path),
                    )
                )

        for source_dir, relative_path in zip(
            copy_data.source_dirs, copy_data.relative_paths, strict=False
        ):
            if not source_dir.exists():
                warning_msg = (
                    f"Source directory does not exist: {source_dir}\n"
                    f"The directory '{relative_path}' specified in the course spec was not found. "
                    f"Please verify the path in your spec file or create the directory."
                )
                logger.warning(warning_msg)
                warnings.append(
                    BuildWarning(
                        category="missing_directory",
                        message=warning_msg,
                        severity="high",
                        file_path=str(source_dir),
                    )
                )
                continue
            output_dir = copy_data.output_dir / relative_path
            logger.debug(f"Copying '{source_dir}' to {output_dir}")
            output_dir.mkdir(parents=True, exist_ok=True)

            if copy_data.recursive:
                # Existing behavior: copy entire tree
                shutil.copytree(
                    source_dir,
                    output_dir,
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(
                        *SKIP_DIRS_FOR_OUTPUT, *SKIP_DIRS_PATTERNS, *SKIP_OUTPUT_FILE_GLOBS
                    ),
                )
            else:
                # Non-recursive: copy only files from this directory
                for item in source_dir.iterdir():
                    if item.is_file():
                        dest = output_dir / item.name
                        logger.debug(f"Copying file '{item}' to {dest}")
                        shutil.copy2(item, dest)

        return warnings

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
                logger.error(f"Error while deleting dependencies for '{file.path.name}':{e}")
                logger.debug(f"Error traceback for '{file.path.name}':", exc_info=e)
                raise

    async def delete_file(self, path: Path) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._delete_file_sync, path)

    @staticmethod
    def _delete_file_sync(path: Path) -> None:
        logger.debug(f"Deleting '{path.name}'")
        path.unlink(missing_ok=True)
