from pathlib import Path
from typing import TYPE_CHECKING

from attrs import define

from clx.core.course_file import CourseFile
from clx.core.topic import Topic
from clx.core.utils.execution_utils import (
    FIRST_EXECUTION_STAGE,
    HTML_COMPLETED_STAGE,
    HTML_SPEAKER_STAGE,
    LAST_EXECUTION_STAGE,
)
from clx.core.utils.notebook_utils import find_notebook_titles
from clx.core.utils.text_utils import Text, sanitize_file_name
from clx.infrastructure.operation import Concurrently, NoOperation, Operation
from clx.infrastructure.utils.path_utils import ext_for, extension_to_prog_lang, output_specs

if TYPE_CHECKING:
    from clx.core.course import Course


def _get_operation_stage(format_: str, kind: str) -> int:
    """Determine which execution stage an operation belongs to.

    Staging:
    - Stage 1: Non-HTML operations (notebook, code formats) and code-along HTML
    - Stage 2 (HTML_SPEAKER_STAGE): Speaker HTML (executes and caches)
    - Stage 3 (HTML_COMPLETED_STAGE): Completed HTML (reuses cache)
    """
    if format_ != "html":
        return FIRST_EXECUTION_STAGE
    if kind == "speaker":
        return HTML_SPEAKER_STAGE
    if kind == "completed":
        return HTML_COMPLETED_STAGE
    # code-along HTML doesn't need execution, can run in first stage
    return FIRST_EXECUTION_STAGE


@define
class NotebookFile(CourseFile):
    title: Text = Text(de="", en="")
    number_in_section: int = 0
    skip_html: bool = False

    @classmethod
    def _from_path(cls, course: "Course", file: Path, topic: "Topic") -> "NotebookFile":
        text = file.read_text(encoding="utf-8")
        title = find_notebook_titles(text, default=file.stem)
        return cls(course=course, path=file, topic=topic, title=title, skip_html=topic.skip_html)

    @property
    def execution_stage(self) -> int:
        """NotebookFile spans multiple stages, return the last one it uses."""
        return LAST_EXECUTION_STAGE

    async def get_processing_operation(
        self, target_dir: Path, stage: int | None = None
    ) -> Operation:
        from clx.core.operations.process_notebook import ProcessNotebookOperation

        operations = [
            ProcessNotebookOperation(
                input_file=self,
                output_file=(
                    self.output_dir(output_dir, lang)
                    / self.file_name(lang, ext_for(format_, self.prog_lang))
                ),
                language=lang,
                format=format_,
                kind=mode,
                prog_lang=self.prog_lang,
                fallback_execute=self.course.fallback_execute,
            )
            for lang, format_, mode, output_dir in output_specs(
                self.course,
                target_dir,
                self.skip_html,
                languages=self.course.output_languages,
                kinds=self.course.output_kinds,
            )
        ]

        # If stage is specified, filter to only operations for that stage
        if stage is not None:
            operations = [
                op for op in operations if _get_operation_stage(op.format, op.kind) == stage
            ]

        if not operations:
            return NoOperation()

        return Concurrently(iter(operations))

    @property
    def prog_lang(self) -> str:
        return extension_to_prog_lang(self.path.suffix)

    def file_name(self, lang: str, ext: str) -> str:
        sanitized_title = sanitize_file_name(self.title[lang])
        return f"{self.number_in_section:02} {sanitized_title}{ext}"
