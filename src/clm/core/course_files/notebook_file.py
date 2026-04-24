import logging
from pathlib import Path
from typing import TYPE_CHECKING

from attrs import define

from clm.core.course_file import CourseFile
from clm.core.topic import Topic
from clm.core.utils.execution_utils import (
    FIRST_EXECUTION_STAGE,
    HTML_COMPLETED_STAGE,
    HTML_SPEAKER_STAGE,
    LAST_EXECUTION_STAGE,
)
from clm.core.utils.notebook_utils import find_notebook_titles
from clm.core.utils.text_utils import Text, sanitize_file_name
from clm.infrastructure.operation import Concurrently, NoOperation, Operation
from clm.infrastructure.utils.path_utils import ext_for, extension_to_prog_lang, output_specs

if TYPE_CHECKING:
    from clm.core.course import Course
    from clm.core.output_target import OutputTarget

logger = logging.getLogger(__name__)


def _get_operation_stage(format_: str, kind: str) -> int:
    """Determine which execution stage an operation belongs to.

    Staging:
    - Stage 1: Non-HTML operations (notebook, code formats) and code-along HTML
    - Stage 2 (HTML_SPEAKER_STAGE): Speaker HTML (executes and caches)
    - Stage 3 (HTML_COMPLETED_STAGE): Completed HTML and Partial HTML (both
      reuse Speaker's cached executed notebook)
    """
    if format_ != "html":
        return FIRST_EXECUTION_STAGE
    if kind == "speaker":
        return HTML_SPEAKER_STAGE
    if kind == "completed":
        return HTML_COMPLETED_STAGE
    if kind == "partial":
        return HTML_COMPLETED_STAGE
    # code-along HTML doesn't need execution, can run in first stage
    return FIRST_EXECUTION_STAGE


@define
class NotebookFile(CourseFile):
    title: Text = Text(de="", en="")
    number_in_section: int = 0
    skip_html: bool = False
    skip_errors: bool = False
    http_replay: bool = False

    @classmethod
    def _from_path(cls, course: "Course", file: Path, topic: "Topic") -> "NotebookFile":
        text = file.read_text(encoding="utf-8")
        title = find_notebook_titles(text, default=file.stem)
        return cls(
            course=course,
            path=file,
            topic=topic,
            title=title,
            skip_html=topic.skip_html,
            skip_errors=topic.skip_errors,
            http_replay=topic.http_replay,
        )

    @property
    def companion_voiceover_path(self) -> Path | None:
        """Return the companion voiceover file path if it exists, else None."""
        from clm.slides.voiceover_tools import companion_path

        comp = companion_path(self.path)
        return comp if comp.exists() else None

    @property
    def cassette_path(self) -> Path | None:
        """Return the HTTP-replay cassette path if present, else None.

        Prefers ``<topic_dir>/_cassettes/<stem>.http-cassette.yaml`` when
        that layout is in use; otherwise falls back to the sibling
        ``<topic_dir>/<stem>.http-cassette.yaml``.
        """
        stem = self.path.stem
        cassette_name = f"{stem}.http-cassette.yaml"
        topic_dir = self.path.parent
        nested = topic_dir / "_cassettes" / cassette_name
        if nested.exists():
            return nested
        sibling = topic_dir / cassette_name
        if sibling.exists():
            return sibling
        return None

    @property
    def cassette_relative_name(self) -> str | None:
        """Return cassette path relative to topic dir (``posix``-style), if any.

        Used as the kernel-cwd-relative path in both direct and Docker modes.
        """
        cassette = self.cassette_path
        if cassette is None:
            return None
        return cassette.relative_to(self.path.parent).as_posix()

    @property
    def execution_stage(self) -> int:
        """NotebookFile spans multiple stages, return the last one it uses."""
        return LAST_EXECUTION_STAGE

    async def get_processing_operation(
        self,
        target_dir: Path,
        stage: int | None = None,
        target: "OutputTarget | None" = None,
        implicit_executions: set[tuple[str, str, str]] | None = None,
    ) -> Operation:
        """Get the processing operation for this notebook file.

        Args:
            target_dir: Root output directory
            stage: Execution stage filter (None = all stages)
            target: OutputTarget for filtering outputs
            implicit_executions: Additional executions needed for cache population
                These are executed but outputs are not written to disk unless
                they are also explicitly requested by the target.

        Returns:
            Operation to execute for this file
        """
        from clm.core.operations.process_notebook import ProcessNotebookOperation

        # Use target for filtering if provided, otherwise fall back to course-level filters
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
                skip_errors=self.skip_errors,
                http_replay_mode=(self.course.http_replay_mode if self.http_replay else None),
            )
            for lang, format_, mode, output_dir in output_specs(
                self.course,
                target_dir,
                self.skip_html,
                languages=self.course.output_languages,
                kinds=self.course.output_kinds,
                target=target,
            )
        ]

        # Add implicit executions for cache population
        # These are needed when completed HTML is requested but speaker HTML
        # (which populates the cache) is not explicitly requested
        if implicit_executions and stage == HTML_SPEAKER_STAGE:
            # Create operations for implicit executions that aren't already included
            existing_keys = {(op.language, op.format, op.kind) for op in operations}
            for lang, format_, kind in implicit_executions:
                if (lang, format_, kind) not in existing_keys:
                    # We need to generate an output spec for this implicit execution
                    # but we don't write it to disk (output will be written for
                    # explicit requests only, but cache will be populated)
                    logger.debug(
                        f"Adding implicit execution for ({lang}, {format_}, {kind}) "
                        f"to populate cache for notebook {self.path}"
                    )
                    # Import OutputSpec to generate path
                    from clm.infrastructure.utils.path_utils import OutputSpec

                    spec = OutputSpec(
                        course=self.course,
                        language=lang,
                        format=format_,
                        kind=kind,
                        root_dir=target_dir,
                    )
                    operations.append(
                        ProcessNotebookOperation(
                            input_file=self,
                            output_file=(
                                self.output_dir(spec.output_dir, lang)
                                / self.file_name(lang, ext_for(format_, self.prog_lang))
                            ),
                            language=lang,
                            format=format_,
                            kind=kind,
                            prog_lang=self.prog_lang,
                            fallback_execute=self.course.fallback_execute,
                            skip_errors=self.skip_errors,
                            http_replay_mode=(
                                self.course.http_replay_mode if self.http_replay else None
                            ),
                            # Mark as implicit - output may be discarded if not
                            # also explicitly requested
                            is_implicit_execution=True,
                        )
                    )

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
        # 1. Explicit topic-level override (from spec attribute)
        if self.topic.prog_lang_override:
            return self.topic.prog_lang_override
        # 2. For .md files: use course-level prog_lang, then default to "python"
        if self.path.suffix == ".md":
            if self.course.spec.prog_lang:
                return self.course.spec.prog_lang
            return "python"
        # 3. For other extensions: use extension-based mapping
        return extension_to_prog_lang(self.path.suffix)

    def file_name(self, lang: str, ext: str) -> str:
        sanitized_title = sanitize_file_name(self.title[lang])
        return f"{self.number_in_section:02} {sanitized_title}{ext}"
