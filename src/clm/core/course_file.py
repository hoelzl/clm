import logging
from pathlib import Path
from typing import TYPE_CHECKING

from attrs import define, field

from clm.core.utils.execution_utils import FIRST_EXECUTION_STAGE
from clm.infrastructure.operation import NoOperation, Operation
from clm.infrastructure.utils.file import File
from clm.infrastructure.utils.path_utils import (
    PLANTUML_EXTENSIONS,
    is_image_file,
    is_slides_file,
)

if TYPE_CHECKING:
    from clm.core.course import Course
    from clm.core.output_target import OutputTarget
    from clm.core.section import Section
    from clm.core.topic import Topic

logger = logging.getLogger(__name__)


@define
class CourseFile(File):
    course: "Course" = field(repr=False)
    topic: "Topic"
    generated_outputs: set[Path] = field(factory=set)
    # Optional canonical on-disk source for files contributed by an
    # ``<include>`` element. When set, ``self.path`` is the file's
    # *logical* location inside ``topic.path`` (the position the topic
    # claims it occupies, used for ``relative_path`` and output) and
    # ``source_origin`` is where to actually read the bytes from.
    # ``None`` for ordinary files physically present in the topic
    # directory; in that case ``source_path`` falls back to ``path`` and
    # behavior matches pre-include CLM exactly.
    source_origin: Path | None = field(default=None)

    @property
    def source_path(self) -> Path:
        """Filesystem path the file's content should be read from.

        For ordinary topic files this is just ``self.path``. For files
        spliced in via ``<include>``, ``self.path`` is virtual and
        ``source_origin`` is the canonical on-disk location.
        """
        return self.source_origin if self.source_origin is not None else self.path

    @staticmethod
    def from_path(course: "Course", file: Path, topic: "Topic") -> "CourseFile":
        cls: type[CourseFile] = _find_file_class(file, course.image_mode)
        return cls._from_path(course, file, topic)

    @classmethod
    def _from_path(cls, course: "Course", file: Path, topic: "Topic") -> "CourseFile":
        return cls(course=course, path=file, topic=topic)

    @classmethod
    def from_virtual(
        cls,
        course: "Course",
        *,
        virtual_path: Path,
        source_origin: Path,
        topic: "Topic",
    ) -> "CourseFile":
        """Build a CourseFile whose logical path is virtual.

        Used when a file is contributed by an ``<include>`` element. The
        concrete subclass is chosen by ``virtual_path``'s suffix (so
        included ``.py`` files become ``DataFile`` unless they look like
        slide files, ``.png`` images become ``DuplicatedImageFile``, etc.
        — matching what would happen if the file were physically present
        at ``virtual_path``).
        """
        concrete = _find_file_class(virtual_path, course.image_mode)
        return concrete(
            course=course,
            path=virtual_path,
            topic=topic,
            source_origin=source_origin,
        )

    @property
    def execution_stage(self) -> int:
        return FIRST_EXECUTION_STAGE

    @property
    def section(self) -> "Section":
        return self.topic.section

    @property
    def relative_path(self) -> Path:
        parent_path = self.topic.path
        if parent_path.is_file():
            logger.debug(f"Relative path: parent {parent_path}, {self.path}")
            parent_path = parent_path.parent
        topic_path = self.path.relative_to(parent_path)
        return topic_path

    def output_dir(self, target_dir: Path, lang: str) -> Path:
        from clm.core.utils.text_utils import sanitize_file_name

        return target_dir / sanitize_file_name(self.section.name[lang])

    # TODO: Maybe find a better naming convention
    # The generated_outputs are the outputs we have actually generated
    # The generated sources are source-files we *can* generate
    @property
    def source_outputs(self) -> frozenset[Path]:
        return frozenset()

    async def get_processing_operation(
        self,
        target_dir: Path,
        stage: int | None = None,
        target: "OutputTarget | None" = None,
        implicit_executions: set[tuple[str, str, str]] | None = None,
    ) -> Operation:
        """Get the processing operation for this file.

        Args:
            target_dir: Root output directory
            stage: Execution stage filter (None = all stages)
            target: OutputTarget for filtering outputs
            implicit_executions: Additional executions needed for cache population

        Returns:
            Operation to execute for this file
        """
        return NoOperation()


def _find_file_class(file: Path, image_mode: str = "duplicated") -> type[CourseFile]:
    """Determine the appropriate CourseFile subclass for a given file.

    Args:
        file: Path to the file
        image_mode: Image storage mode ("duplicated" or "shared")

    Returns:
        The appropriate CourseFile subclass for this file type
    """
    from clm.core.course_files.data_file import DataFile
    from clm.core.course_files.drawio_file import DrawIoFile
    from clm.core.course_files.duplicated_image_file import DuplicatedImageFile
    from clm.core.course_files.notebook_file import NotebookFile
    from clm.core.course_files.plantuml_file import PlantUmlFile
    from clm.core.course_files.shared_image_file import SharedImageFile

    if file.suffix in PLANTUML_EXTENSIONS:
        return PlantUmlFile
    if file.suffix == ".drawio":
        return DrawIoFile
    if is_slides_file(file):
        return NotebookFile
    if is_image_file(file):
        if image_mode == "shared":
            return SharedImageFile
        else:
            return DuplicatedImageFile
    return DataFile
