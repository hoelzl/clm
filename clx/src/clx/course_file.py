import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from attrs import define, field

from clx_common.operation import NoOperation, Operation
from clx.utils.div_uils import FIRST_EXECUTION_STAGE, File
from clx_common.utils.path_utils import (
    PLANTUML_EXTENSIONS, is_slides_file, )

if TYPE_CHECKING:
    from clx.course import Course
    from clx.section import Section
    from clx.topic import Topic

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
        return target_dir / self.section.name[lang]

    # TODO: Maybe find a better naming convention
    # The generated_outputs are the outputs we have actually generated
    # The generated sources are source-files we *can* generate
    @property
    def source_outputs(self) -> frozenset[Path]:
        return frozenset()

    async def get_processing_operation(self, target_dir: Path) -> Operation:
        return NoOperation()

    async def delete(self) -> None:
        course_actions = []
        for go in self.generated_outputs:
            course_actions.append(self.course.on_file_deleted(backend, go))
            go.unlink(missing_ok=True)
        self.generated_outputs.clear()
        await asyncio.gather(*course_actions, return_exceptions=True)


def _find_file_class(file: Path) -> type[CourseFile]:
    from clx.course_files.data_file import DataFile
    from clx.course_files.drawio_file import DrawIoFile
    from clx.course_files.notebook_file import NotebookFile
    from clx.course_files.plantuml_file import PlantUmlFile

    if file.suffix in PLANTUML_EXTENSIONS:
        return PlantUmlFile
    if file.suffix == ".drawio":
        return DrawIoFile
    if is_slides_file(file):
        return NotebookFile
    return DataFile
