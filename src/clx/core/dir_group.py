import logging
from pathlib import Path
from typing import TYPE_CHECKING

from attrs import frozen
from clx.infrastructure.operation import Operation

from clx.core.course_spec import DirGroupSpec
from clx.infrastructure.utils.path_utils import (output_path_for, )
from clx.core.utils.text_utils import Text

if TYPE_CHECKING:
    from clx.core.course import Course

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

    async def get_processing_operation(self) -> "Operation":
        from clx.infrastructure.operation import Concurrently
        from clx.core.operations.copy_dir_group import CopyDirGroupOperation

        return Concurrently(
            (
                CopyDirGroupOperation(dir_group=self, lang="de", is_speaker=False),
                CopyDirGroupOperation(dir_group=self, lang="en", is_speaker=False),
            )
        )
