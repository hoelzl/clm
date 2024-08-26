import asyncio
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from attrs import frozen

from clx.course_spec import DirGroupSpec
from clx_common.operation import Operation
from clx.utils.path_utils import (
    SKIP_DIRS_FOR_OUTPUT,
    SKIP_DIRS_PATTERNS,
    output_path_for,
)
from clx.utils.text_utils import Text

if TYPE_CHECKING:
    from clx.course import Course

logger = logging.getLogger(__name__)


@frozen
class DirGroup:
    name: Text
    source_dirs: tuple[Path, ...]
    relative_paths: tuple[Path, ...]
    course: "Course"

    @classmethod
    def from_spec(cls, spec: DirGroupSpec, course: "Course") -> "DirGroup":
        source_path = course.course_root / spec.path
        source_dirs = (
            tuple(source_path / subdir for subdir in spec.subdirs)
            if spec.subdirs
            else (source_path,)
        )
        relative_paths = tuple(Path(path) for path in spec.subdirs or [""])
        return cls(
            name=spec.name,
            source_dirs=source_dirs,
            relative_paths=relative_paths,
            course=course,
        )

    @property
    def output_root(self) -> Path:
        return self.course.output_root

    def output_path(self, is_speaker, lang: str) -> Path:
        return (
            output_path_for(self.output_root, is_speaker, lang, self.course.name)
            / self.name[lang]
        )

    def output_dirs(self, is_speaker, lang: str) -> tuple[Path, ...]:
        return tuple(
            self.output_path(is_speaker, lang) / dir_ for dir_ in self.relative_paths
        )

    async def copy_to_output(self, is_speaker, lang: str):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.copy_to_output_sync, is_speaker, lang)

    def copy_to_output_sync(self, is_speaker, lang: str):
        logger.debug(f"Copying '{self.name[lang]}' to output for {lang}")
        for source_dir, relative_path in zip(self.source_dirs, self.relative_paths):
            if not source_dir.exists():
                logger.error(f"Source directory does not exist: {source_dir}")
                continue
            output_dir = self.output_path(is_speaker, lang) / relative_path
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
            # logger.debug(f"Listing output dir:")
            # dirs = "\n".join(str(path) for path in output_dir.glob("*"))
            # logger.debug(f"Output dir: {dirs}")

    async def get_processing_operation(self) -> "Operation":
        from clx_common.operation import Concurrently
        from clx.operations.copy_dict_group import CopyDictGroupOperation

        return Concurrently(
            (
                CopyDictGroupOperation(dict_group=self, lang="de"),
                CopyDictGroupOperation(dict_group=self, lang="en"),
            )
        )
