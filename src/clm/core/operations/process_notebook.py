import logging
from base64 import b64encode
from pathlib import Path
from typing import Any

from attrs import frozen

from clm.core.course_files.notebook_file import NotebookFile
from clm.infrastructure.messaging.correlation_ids import (
    new_correlation_id,
    note_correlation_id_dependency,
)
from clm.infrastructure.messaging.notebook_classes import NotebookPayload
from clm.infrastructure.operation import Operation
from clm.infrastructure.utils.path_utils import (
    is_ignored_file_for_output,
    is_image_file,
    is_image_source_file,
    output_path_for,
    relative_path_to_course_img,
)

logger = logging.getLogger(__name__)


def _resolve_trace_dir_for_payload() -> str:
    """Return the active HTTP-replay trace directory (string) or empty.

    Reads the host's pinned invocation dir set by ``clm build`` when
    ``CLM_HTTP_REPLAY_TRACE=1``. The returned string is the absolute path
    that the worker bootstrap template will receive via the
    ``http_replay_trace_dir`` payload field; the worker writes
    ``worker-<pid>.jsonl`` under it. Empty string means tracing is off.
    """
    from clm.workers.notebook.http_replay_trace import (
        get_invocation_dir,
        is_enabled,
    )

    if not is_enabled():
        return ""
    invocation_dir = get_invocation_dir()
    return str(invocation_dir) if invocation_dir is not None else ""


@frozen
class ProcessNotebookOperation(Operation):
    input_file: "NotebookFile"
    output_file: Path
    language: str
    format: str
    kind: str
    prog_lang: str
    fallback_execute: bool = False
    # If True, the notebook is rendered without spawning a kernel: cells
    # are converted to all configured output formats with empty outputs.
    # Set per-topic via the ``evaluate="no"`` spec attribute.
    skip_evaluation: bool = False
    skip_errors: bool = False
    # HTTP replay mode ("replay"/"once"/"refresh"/"disabled") or None.
    # Only set when the topic opted in via ``http-replay="yes"``; the
    # worker activates a ``vcrpy`` cassette when this is non-None.
    http_replay_mode: str | None = None
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

    def _resolve_cassette_name(self) -> str | None:
        """Resolve the cassette name to send to the worker.

        - ``replay`` mode requires the cassette to already exist; if it does
          not, return None and let the worker emit its strict-mode warning
          (or, in CI, fail).
        - ``once``, ``new-episodes``, and ``refresh`` modes are
          record-capable: return the *expected* cassette path even when the
          file does not yet exist, so the bootstrap can activate vcrpy with
          a write target.
        - Any other mode (incl. ``disabled``/None) returns None.
        """
        mode = self.http_replay_mode
        if not mode or mode == "disabled":
            return None
        if mode == "replay":
            return self.input_file.cassette_relative_name
        # once / new-episodes / refresh — record-capable.
        existing = self.input_file.cassette_relative_name
        if existing is not None:
            return existing
        return self.input_file.expected_cassette_relative_name

    def compute_other_files(self):
        companion = self.input_file.companion_voiceover_path

        def relative_path(file):
            return str(file.relative_path).replace("\\", "/")

        # ``is_ignored_file_for_output`` is a superset of
        # ``is_ignored_file_for_course`` plus the patterns in
        # ``SKIP_OUTPUT_FILE_PATTERNS`` (canonical cassettes, per-worker
        # ``.staging-*`` cassettes). The staging filter matters even though
        # this is a payload-side enumeration: a concurrent worker may
        # ``merge_staging_into_canonical`` and delete the staging file
        # between glob and read, producing a ``FileNotFoundError`` on the
        # ``read_bytes()`` below. Filtering them out here makes the
        # payload builder robust to that race. The canonical cassette is
        # re-added explicitly below when the topic opted in.
        other_files = {
            relative_path(file): b64encode(file.source_path.read_bytes())
            for file in self.input_file.topic.files
            if file != self.input_file
            and not is_image_file(file.path)
            and not is_image_source_file(file.path)
            and not is_ignored_file_for_output(file.path)
            and (companion is None or file.path != companion)
        }

        # Ensure the HTTP-replay cassette is available to the kernel when
        # the topic opted in. For FileTopic the cassette is not part of
        # ``topic.files`` automatically; for DirectoryTopic it may already
        # be present under a possibly-different key — overwrite with the
        # canonical kernel-cwd-relative key so bootstrap resolution is
        # deterministic.
        if self.http_replay_mode and self.http_replay_mode != "disabled":
            cassette = self.input_file.cassette_path
            cassette_name = self.input_file.cassette_relative_name
            if cassette is not None and cassette_name is not None:
                other_files[cassette_name] = b64encode(cassette.read_bytes())

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
        # The course directory is the parent that contains the course dir name folder
        # Structure: .../public|speaker/course-dir-name/...
        course_dir_name = course.output_dir_name[self.language]

        # Walk up from the output file to find the course directory
        # The course directory is the one named after the course
        output_path = self.output_file
        for parent in output_path.parents:
            if parent.name == course_dir_name:
                course_dir = parent
                break
        else:
            # Fallback to computing from output_root if pattern not found
            is_speaker = self.kind in ("trainer", "recording", "speaker")
            course_dir = output_path_for(
                course.output_root, is_speaker, self.language, course_dir_name
            )

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

    def compute_svg_available_stems(self) -> list[str]:
        """Collect image stems that have SVG equivalents.

        These are images generated from DrawIO/PlantUML sources, which can be
        produced as SVG. Raw .png files in the source tree are NOT included
        since they cannot be converted to SVG.

        Returns:
            List of image stems (filenames without extension) that have SVG versions
        """
        from clm.core.course_files.image_file import ImageFile

        stems = []
        for file in self.input_file.topic.files:
            if isinstance(file, ImageFile):
                stems.append(file.path.stem)
        return stems

    async def payload(self) -> NotebookPayload:
        course = self.input_file.course
        correlation_id = await new_correlation_id()

        # Resolve author: topic-level overrides course-level
        topic_author = self.input_file.topic.author
        author = topic_author if topic_author else course.spec.author

        # Resolve organization for the current output language
        organization = course.spec.organization[self.language]

        # Read slide file text, merging companion voiceover if present
        data = self.input_file.path.read_text(encoding="utf-8")
        companion = self.input_file.companion_voiceover_path
        if companion is not None:
            from clm.slides.voiceover_tools import merge_voiceover_text

            companion_text = companion.read_text(encoding="utf-8")
            data, unmatched = merge_voiceover_text(data, companion_text)
            for for_slide_id in unmatched:
                logger.warning(
                    f"Companion voiceover '{companion.name}': "
                    f"unmatched for_slide='{for_slide_id}' "
                    f"(no slide_id match in '{self.input_file.path.name}')"
                )

        payload = NotebookPayload(
            data=data,
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
            skip_evaluation=self.skip_evaluation,
            skip_errors=self.skip_errors,
            http_replay_mode=self.http_replay_mode,
            http_replay_cassette_name=self._resolve_cassette_name(),
            http_replay_trace_dir=_resolve_trace_dir_for_payload(),
            img_path_prefix=self.compute_img_path_prefix(),
            source_topic_dir=self.compute_source_topic_dir(),
            svg_available_stems=(
                self.compute_svg_available_stems() if course.image_format == "svg" else []
            ),
            inline_images=course.inline_images,
            author=author,
            organization=organization,
        )
        await note_correlation_id_dependency(correlation_id, payload)
        return payload

    @property
    def service_name(self) -> str:
        return "notebook-processor"
