import logging
from pathlib import Path
from typing import TYPE_CHECKING

from attrs import frozen

from clx.core.course_spec import DirGroupSpec
from clx.core.utils.text_utils import Text
from clx.infrastructure.operation import Operation
from clx.infrastructure.utils.path_utils import (
    output_path_for,
)

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

    def output_path(
        self,
        is_speaker: bool,
        lang: str,
        output_root: Path | None = None,
        skip_toplevel: bool = False,
    ) -> Path:
        root = output_root if output_root is not None else self.output_root
        return (
            output_path_for(root, is_speaker, lang, self.course.name, skip_toplevel=skip_toplevel)
            / self.name[lang]
        )

    def output_dirs(
        self,
        is_speaker: bool,
        lang: str,
        output_root: Path | None = None,
        skip_toplevel: bool = False,
    ) -> tuple[Path, ...]:
        return tuple(
            self.output_path(is_speaker, lang, output_root, skip_toplevel=skip_toplevel) / dir_
            for dir_ in self.relative_paths
        )

    async def get_processing_operation(
        self,
        output_root: Path | None = None,
        languages: frozenset[str] | None = None,
        is_speaker_options: list[bool] | None = None,
        skip_toplevel: bool = False,
    ) -> "Operation":
        """Get the operation to copy this directory group.

        Args:
            output_root: Optional override for output root directory.
                        If None, uses the course's output_root.
            languages: Optional set of languages to copy for.
                      If None, copies for all languages (de, en).
            is_speaker_options: List of is_speaker values to copy for.
                               If None, defaults to [False, True] for both public and speaker.
            skip_toplevel: If True, skip the "public"/"speaker" directory prefix.
                          Used for explicitly specified output targets.
        """
        from clx.core.operations.copy_dir_group import CopyDirGroupOperation
        from clx.infrastructure.operation import Concurrently, NoOperation

        # Default to all languages if not specified
        langs_to_copy = languages if languages is not None else frozenset({"de", "en"})

        # Default to both public and speaker if not specified
        speaker_options = is_speaker_options if is_speaker_options is not None else [False, True]

        operations = tuple(
            CopyDirGroupOperation(
                dir_group=self,
                lang=lang,
                is_speaker=is_speaker,
                output_root=output_root,
                skip_toplevel=skip_toplevel,
            )
            for lang in langs_to_copy
            for is_speaker in speaker_options
        )

        if not operations:
            return NoOperation()

        return Concurrently(operations)
