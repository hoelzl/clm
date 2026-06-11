import hashlib
import logging
from base64 import b64encode
from functools import cache
from importlib.resources import files as package_files
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


@cache
def compute_template_fingerprint(prog_lang: str) -> str:
    """Digest of the bundled Jinja templates for *prog_lang* plus the CLM version.

    Computed HOST-side at payload construction and shipped to the worker via
    ``NotebookPayload.template_fingerprint``, where it is folded into the
    cache keys. Templates (``macros.j2`` etc.) live inside the clm package
    and are resolved worker-side, so they are otherwise invisible to the
    cache: without this fingerprint a template change shipped with a clm
    upgrade silently replays stale HTML from a warm cache (issue #321).

    The CLM version is folded in as well, as a coarse proxy for everything
    else the package ships that can affect output (worker code, bootstrap
    templates). The fingerprint must be computed on ONE side only — host and
    worker may run different clm versions (Docker images), and the cache key
    must be computed identically by every layer, so the worker reuses the
    host's value from the payload instead of recomputing.

    ``lru_cache`` makes this a one-time cost per prog_lang per process; the
    template directories are a handful of small files.
    """
    from clm import __version__

    hasher = hashlib.sha256()
    hasher.update(f"{__version__}:{prog_lang}".encode())
    template_dir = package_files("clm") / "workers" / "notebook" / f"templates_{prog_lang}"
    if template_dir.is_dir():
        entries = sorted((entry.name, entry) for entry in template_dir.iterdir() if entry.is_file())
        for name, entry in entries:
            hasher.update(f"\n{name}:".encode())
            hasher.update(entry.read_bytes())
    return hasher.hexdigest()


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


def report_voiceover_merge_issues(
    build_reporter: Any,
    *,
    slide_name: str,
    companion_name: str,
    file_path: str,
    unmatched: list[str],
) -> None:
    """Escalate unmatched companion voiceover at build time (#162 hardening).

    When ``merge_voiceover_text`` cannot match a companion cell's ``for_slide``
    to a ``slide_id`` in the slide, that narration is **dropped** from the built
    output. This used to be log-only (exit 0) — a silent loss of a slide's
    speaker notes, usually because a ``slide_id`` was renamed out from under the
    companion. Each drop is now reported as a ``BuildError`` (``category=
    "voiceover"``), so it is surfaced in the build summary and — under the
    build's ``--fail-on-error`` policy (default-on in CI / replay mode) — fails
    the build, exactly like a cell-execution error. A course-repo gate must not
    silently ship a slide whose narration vanished.

    ``build_reporter`` is duck-typed (anything exposing ``report_error``);
    ``None`` (no reporter — e.g. a direct ``payload()`` call in a test) is a
    no-op, as is an empty ``unmatched`` list.
    """
    if build_reporter is None or not unmatched:
        return
    from clm.cli.build_data_classes import BuildError

    for for_slide in unmatched:
        target = "(cell has no for_slide)" if for_slide == "<no for_slide>" else repr(for_slide)
        build_reporter.report_error(
            BuildError(
                error_type="user",
                category="voiceover",
                severity="error",
                file_path=file_path,
                message=(
                    f"companion voiceover {companion_name}: for_slide {target} has no "
                    f"matching slide_id in {slide_name}; the narration is dropped from output"
                ),
                actionable_guidance=(
                    "A slide_id was likely renamed out from under the companion. Re-align "
                    "the for_slide / slide_id (run `clm voiceover inline` then re-extract, or "
                    "`clm slides sync`), or pass --no-fail-on-error to tolerate the drop."
                ),
            )
        )


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
            payload = await self.payload(getattr(backend, "build_reporter", None))
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
            # Replay may fall back to the base (bilingual) cassette for a
            # split ``.de``/``.en`` deck (Issue #159). The fallback is scoped
            # to replay only: record modes below keep the strict,
            # language-specific name so a re-record never overwrites or bleeds
            # into the shared base.
            return self.input_file.replay_cassette_relative_name
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
            # Ship the same cassette the worker will look up. In ``replay``
            # mode that may be the base (bilingual) cassette via the language
            # fallback (Issue #159); record modes keep the strict,
            # language-specific cassette so the base is never seeded from or
            # written to on behalf of one language.
            if self.http_replay_mode == "replay":
                cassette = self.input_file.replay_cassette_path
                cassette_name = self.input_file.replay_cassette_relative_name
            else:
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

    def compute_cross_references(self, data: str) -> dict[str, str]:
        """Resolve every ``clm:`` cross-reference in *data* for this artifact.

        Returns a mapping consumed by ``rewrite_cross_references`` in the
        worker: raw reference -> resolved relative href (empty string means
        "drop the link, keep the text"; an omitted reference is left
        verbatim). Resolution happens here, at payload-construction time,
        because the full ``Course`` (all sections, assigned numbers, renamed
        filenames) is in scope — the worker processes one notebook in
        isolation and must not need other notebooks' output names.

        Build-time *reporting* of missing/ambiguous targets is handled
        separately by ``validate_cross_references`` (host-side, feeds the
        build summary); here we only produce the rewrite map, applying the
        same fail-on-missing policy so a failing build leaves dangling links
        verbatim rather than silently dropping them.
        """
        from clm.core.cross_references import (
            extract_cross_references,
            has_cross_references,
        )

        if not has_cross_references(data):
            return {}

        course = self.input_file.course
        resolver = course.cross_reference_resolver
        references = extract_cross_references(data)
        hrefs, _issues = resolver.build_href_map(
            references,
            from_notebook=self.input_file,
            language=self.language,
            kind=self.kind,
            format=self.format,
            fail_on_missing=course.fail_on_missing_xref,
        )
        return hrefs

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

    async def payload(self, build_reporter: Any = None) -> NotebookPayload:
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
            from clm.notebooks.slide_parser import comment_token_for_path
            from clm.slides.voiceover_tools import merge_voiceover_text

            companion_text = companion.read_text(encoding="utf-8")
            data, unmatched = merge_voiceover_text(
                data, companion_text, comment_token_for_path(companion)
            )
            for for_slide_id in unmatched:
                logger.warning(
                    f"Companion voiceover '{companion.name}': "
                    f"unmatched for_slide='{for_slide_id}' "
                    f"(no slide_id match in '{self.input_file.path.name}')"
                )
            # Escalate the drop from log-only to a surfaced BuildError so it
            # fails the build under --fail-on-error (the build consumer arm of
            # the #162 / split-voiceover hardening). No-op without a reporter.
            report_voiceover_merge_issues(
                build_reporter,
                slide_name=self.input_file.path.name,
                companion_name=companion.name,
                file_path=str(self.input_file.path),
                unmatched=unmatched,
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
            template_fingerprint=compute_template_fingerprint(self.prog_lang),
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
            cross_references=self.compute_cross_references(data),
        )
        await note_correlation_id_dependency(correlation_id, payload)
        return payload

    @property
    def service_name(self) -> str:
        return "notebook-processor"
