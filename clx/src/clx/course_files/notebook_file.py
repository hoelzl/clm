from pathlib import Path
from typing import TYPE_CHECKING

from attrs import define

from clx.course_file import CourseFile
from clx.operation import Concurrently, Operation
from clx.topic import Topic
from clx.utils.notebook_utils import find_notebook_titles
from clx.utils.path_utils import ext_for, extension_to_prog_lang, output_specs
from clx.utils.text_utils import Text

if TYPE_CHECKING:
    from clx.course import Course


@define
class NotebookFile(CourseFile):
    title: Text = Text(de="", en="")
    number_in_section: int = 0

    @classmethod
    def _from_path(cls, course: "Course", file: Path, topic: "Topic") -> "NotebookFile":
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
