import logging
from base64 import b64encode
from pathlib import Path
from typing import Any

from attrs import frozen

from clx.core.course_files.notebook_file import NotebookFile
from clx.infrastructure.messaging.correlation_ids import (
    new_correlation_id,
    note_correlation_id_dependency,
)
from clx.infrastructure.messaging.notebook_classes import NotebookPayload
from clx.infrastructure.operation import Operation
from clx.infrastructure.utils.path_utils import (
    is_ignored_file_for_course,
    is_image_file,
    is_image_source_file,
    output_path_for,
    relative_path_to_course_img,
)

logger = logging.getLogger(__name__)


@frozen
class ProcessNotebookOperation(Operation):
    input_file: "NotebookFile"
    output_file: Path
    language: str
    format: str
    kind: str
    prog_lang: str
    fallback_execute: bool = False
    # If True, this operation is for implicit cache population only.
    # The output is still generated (to populate the cache), but this
    # flag can be used for logging/debugging purposes.
    is_implicit_execution: bool = False

    async def execute(self, backend, *args, **kwargs) -> Any:
        file_path = self.input_file.relative_path
        try:
            logger.info(f"Processing notebook '{file_path}' to '{self.output_file}'")
            payload = await self.payload()
            await backend.execute_operation(self, payload)
            self.input_file.generated_outputs.add(self.output_file)
        except Exception as e:
            op = "'ProcessNotebookOperation'"
            logger.error(f"Error while executing {op} for '{file_path}': {e}")
            logger.debug(f"Error traceback for '{file_path}'", exc_info=e)
            raise

    def compute_other_files(self):
        def relative_path(file):
            return str(file.relative_path).replace("\\", "/")

        other_files = {
            relative_path(file): b64encode(file.path.read_bytes())
            for file in self.input_file.topic.files
            if file != self.input_file
            and not is_image_file(file.path)
            and not is_image_source_file(file.path)
            and not is_ignored_file_for_course(file.path)
        }
        return other_files

    def compute_img_path_prefix(self) -> str:
        """Compute the relative path from output file to the img/ folder.

        In duplicated mode, images are in img/ relative to the notebook output,
        so no path rewriting is needed (returns "img/").

        In shared mode, images are in a course-level img/ folder, so we need to
        compute the relative path to that folder (e.g., "../../../../img/").

        Returns:
            Relative path prefix for use in HTML/notebook output
        """
        course = self.input_file.course

        # In duplicated mode, images are local to each output variant
        # Return "img/" so no path rewriting occurs
        if course.image_mode == "duplicated":
            return "img/"

        # In shared mode, compute relative path to course-level img/ folder
        # Find the course directory by looking at the output file path
        # The course directory is the parent that contains the course name folder
        # Structure: .../public|speaker/Lang/CourseName/...
        course_name = course.name[self.language]

        # Walk up from the output file to find the course directory
        # The course directory is the one named after the course
        output_path = self.output_file
        for parent in output_path.parents:
            if parent.name == course_name:
                course_dir = parent
                break
        else:
            # Fallback to computing from output_root if pattern not found
            is_speaker = self.kind == "speaker"
            course_dir = output_path_for(course.output_root, is_speaker, self.language, course.name)

        # Calculate relative path from output file to course's img/ folder
        return relative_path_to_course_img(self.output_file, course_dir)

    def compute_source_topic_dir(self) -> str:
        """Compute the absolute path to the topic directory.

        This is used by Docker workers with source mount to read supporting files
        directly from /source/{relative_path} instead of from base64-encoded
        other_files in the payload.

        Returns:
            Absolute path to the topic directory on the host filesystem.
        """
        topic_dir = self.input_file.topic.path
        if topic_dir.is_file():
            topic_dir = topic_dir.parent
        return str(topic_dir)

    async def payload(self) -> NotebookPayload:
        correlation_id = await new_correlation_id()
        payload = NotebookPayload(
            data=self.input_file.path.read_text(encoding="utf-8"),
            correlation_id=correlation_id,
            input_file=str(self.input_file.path),
            input_file_name=self.input_file.path.name,
            output_file=str(self.output_file),
            kind=self.kind,
            prog_lang=self.prog_lang,
            language=self.language,
            format=self.format,
            other_files=self.compute_other_files(),
            fallback_execute=self.fallback_execute,
            img_path_prefix=self.compute_img_path_prefix(),
            source_topic_dir=self.compute_source_topic_dir(),
        )
        await note_correlation_id_dependency(correlation_id, payload)
        return payload

    @property
    def service_name(self) -> str:
        return "notebook-processor"
