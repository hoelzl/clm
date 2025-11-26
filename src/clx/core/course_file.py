import logging
from pathlib import Path
from typing import TYPE_CHECKING

from attrs import define, field

from clx.core.utils.execution_utils import FIRST_EXECUTION_STAGE
from clx.infrastructure.operation import NoOperation, Operation
from clx.infrastructure.utils.file import File
from clx.infrastructure.utils.path_utils import (
    PLANTUML_EXTENSIONS,
    is_image_file,
    is_slides_file,
)

if TYPE_CHECKING:
    from clx.core.course import Course
    from clx.core.output_target import OutputTarget
    from clx.core.section import Section
    from clx.core.topic import Topic

logger = logging.getLogger(__name__)


@define
class CourseFile(File):
    course: "Course" = field(repr=False)
    topic: "Topic"
    generated_outputs: set[Path] = field(factory=set)

    @staticmethod
    def from_path(course: "Course", file: Path, topic: "Topic") -> "CourseFile":
        cls: type[CourseFile] = _find_file_class(file)
        return cls._from_path(course, file, topic)

    @classmethod
    def _from_path(cls, course: "Course", file: Path, topic: "Topic") -> "CourseFile":
        return cls(course=course, path=file, topic=topic)

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
        from clx.core.utils.text_utils import sanitize_file_name

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


def _find_file_class(file: Path) -> type[CourseFile]:
    from clx.core.course_files.data_file import DataFile
    from clx.core.course_files.drawio_file import DrawIoFile
    from clx.core.course_files.notebook_file import NotebookFile
    from clx.core.course_files.plantuml_file import PlantUmlFile
    from clx.core.course_files.shared_image_file import SharedImageFile

    if file.suffix in PLANTUML_EXTENSIONS:
        return PlantUmlFile
    if file.suffix == ".drawio":
        return DrawIoFile
    if is_slides_file(file):
        return NotebookFile
    if is_image_file(file):
        return SharedImageFile
    return DataFile
