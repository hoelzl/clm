import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from attrs import define, field

from clx.operation import Concurrently, NoOperation, Operation
from clx.utils.div_uils import FIRST_EXECUTION_STAGE, File, LAST_EXECUTION_STAGE
from clx.utils.notebook_utils import find_notebook_titles
from clx.utils.path_utils import (
    PLANTUML_EXTENSIONS,
    ext_for,
    extension_to_prog_lang,
    is_slides_file,
    output_specs,
)
from clx.utils.text_utils import Text

if TYPE_CHECKING:
    from clx.course import Course
    from clx.section import Section
    from clx.topic import Topic

logger = logging.getLogger(__name__)


@define
class CourseFile(File):
    course: "Course"
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
    def generated_sources(self) -> frozenset[Path]:
        return frozenset()

    async def get_processing_operation(self, target_dir: Path) -> Operation:
        return NoOperation()

    async def delete(self) -> None:
        course_actions = []
        for go in self.generated_outputs:
            course_actions.append(self.course.on_file_deleted(go))
            go.unlink(missing_ok=True)
        self.generated_outputs.clear()
        await asyncio.gather(*course_actions, return_exceptions=True)


@define
class PlantUmlFile(CourseFile):
    async def get_processing_operation(self, target_dir: Path) -> Operation:
        from clx.operations.convert_plantuml_file import ConvertPlantUmlFileOperation

        return ConvertPlantUmlFileOperation(
            input_file=self,
            output_file=self.img_path,
        )

    @property
    def img_path(self) -> Path:
        return (self.path.parents[1] / "img" / self.path.stem).with_suffix(".png")

    @property
    def generated_sources(self) -> frozenset[Path]:
        return frozenset({self.img_path})


@define
class DrawIoFile(CourseFile):
    async def get_processing_operation(self, target_dir: Path) -> Operation:
        from clx.operations.convert_drawio_file import ConvertDrawIoFileOperation

        return ConvertDrawIoFileOperation(
            input_file=self,
            output_file=self.img_path,
        )

    @property
    def img_path(self) -> Path:
        return (self.path.parents[1] / "img" / self.path.stem).with_suffix(".png")

    @property
    def generated_sources(self) -> frozenset[Path]:
        return frozenset({self.img_path})


@define
class DataFile(CourseFile):

    @property
    def execution_stage(self) -> int:
        return LAST_EXECUTION_STAGE

    async def get_processing_operation(self, target_dir: Path) -> Operation:
        from clx.operations.copy_file import CopyFileOperation

        return Concurrently(
            CopyFileOperation(
                input_file=self,
                output_file=self.output_dir(output_dir, lang) / self.relative_path,
            )
            for lang, _, _, output_dir in output_specs(self.course, target_dir)
        )


@define
class Notebook(CourseFile):
    title: Text = Text(de="", en="")
    number_in_section: int = 0

    @classmethod
    def _from_path(cls, course: "Course", file: Path, topic: "Topic") -> "Notebook":
        text = file.read_text()
        title = find_notebook_titles(text, default=file.stem)
        return cls(course=course, path=file, topic=topic, title=title)

    async def get_processing_operation(self, target_dir: Path) -> Operation:
        from clx.operations.process_notebook import ProcessNotebookOperation

        return Concurrently(
            ProcessNotebookOperation(
                input_file=self,
                output_file=(
                    self.output_dir(output_dir, lang)
                    / self.file_name(lang, ext_for(format_, self.prog_lang))
                ),
                lang=lang,
                format=format_,
                mode=mode,
                prog_lang=self.prog_lang,
            )
            for lang, format_, mode, output_dir in output_specs(self.course, target_dir)
        )

    @property
    def prog_lang(self) -> str:
        return extension_to_prog_lang(self.path.suffix)

    def file_name(self, lang: str, ext: str) -> str:
        return f"{self.number_in_section:02} {self.title[lang]}{ext}"


def _find_file_class(file: Path) -> type[CourseFile]:
    if file.suffix in PLANTUML_EXTENSIONS:
        return PlantUmlFile
    if file.suffix == ".drawio":
        return DrawIoFile
    if is_slides_file(file):
        return Notebook
    return DataFile
