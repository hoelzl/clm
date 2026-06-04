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
    - Stage 2 (HTML_SPEAKER_STAGE): Recording HTML (executes and caches; the
      stage constant retains its legacy ``SPEAKER`` name)
    - Stage 3 (HTML_COMPLETED_STAGE): Trainer, Completed, and Partial HTML —
      each reuses Recording's cached executed notebook
    """
    if format_ != "html":
        return FIRST_EXECUTION_STAGE
    # ``speaker`` is the deprecated alias for ``recording``; both populate
    # the cache and run in the producer stage.
    if kind in ("recording", "speaker"):
        return HTML_SPEAKER_STAGE
    if kind == "trainer":
        return HTML_COMPLETED_STAGE
    if kind == "completed":
        return HTML_COMPLETED_STAGE
    if kind == "partial":
        return HTML_COMPLETED_STAGE
    # code-along HTML doesn't need execution, can run in first stage
    return FIRST_EXECUTION_STAGE


# Trailing tokens on a slide-file stem that mark a language-split companion
# (``slides_010_foo.de.py`` / ``slides_010_foo.en.py``). See
# ``_base_cassette_stem`` and Issue #159.
_LANGUAGE_CASSETTE_TOKENS = ("de", "en")

# Topic-relative subdirectories that may hold HTTP-replay cassettes, in
# read-preference order. ``cassettes`` is the current name; ``_cassettes`` is
# the original underscore-prefixed form, still accepted as a legacy alias.
# Auto-detected by presence — no flag — mirroring the ``voiceover/`` companion
# sidecar (``slides.voiceover_tools.resolve_companion``).
_CASSETTE_SUBDIRS = ("cassettes", "_cassettes")


def _base_cassette_stem(stem: str) -> str | None:
    """Return the base (bilingual) stem for a split language deck, else ``None``.

    A split deck's source file (``slides_010_foo.de.py``) has stem
    ``slides_010_foo.de``. Stripping the single trailing ``.de``/``.en`` token
    yields ``slides_010_foo`` — the stem shared with the original bilingual
    deck, whose cassette holds both languages' recorded interactions.

    Only a single trailing ``.de``/``.en`` token is stripped, immediately
    before the (already-removed) ``.py`` suffix. Stems without such a token
    (normal decks, or dotted version-like stems such as ``slides_010_v1.2``)
    return ``None`` so they are unaffected.
    """
    base, dot, token = stem.rpartition(".")
    if dot and token in _LANGUAGE_CASSETTE_TOKENS:
        return base
    return None


@define
class NotebookFile(CourseFile):
    title: Text = Text(de="", en="")
    number_in_section: int = 0
    skip_html: bool = False
    skip_evaluation: bool = False
    skip_errors: bool = False
    http_replay: bool = False
    # Phase 6 split routing: when set to "de" or "en", this notebook
    # only produces output for the named language. Bilingual files leave
    # this ``None`` and the per-cell ``lang`` attribute drives filtering
    # downstream as before. Set by ``Topic.add_file`` when a slide unit
    # is detected as a ``.de.py`` / ``.en.py`` split companion.
    output_language_filter: str | None = None

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
            skip_evaluation=topic.skip_evaluation,
            skip_errors=topic.skip_errors,
            http_replay=topic.http_replay,
        )

    @property
    def companion_voiceover_path(self) -> Path | None:
        """Return the companion voiceover file path if it exists, else None.

        Resolves the relocated ``voiceover/`` subdirectory layout before the
        sibling layout (see ``voiceover_tools.resolve_companion``); the build
        merges the companion's narration host-side at payload time.
        """
        from clm.slides.voiceover_tools import resolve_companion

        return resolve_companion(self.path)

    def _resolve_cassette(self, cassette_name: str) -> Path | None:
        """Return the existing cassette ``cassette_name`` for this topic, else None.

        Prefers a sidecar subdirectory (``cassettes/`` then the legacy
        ``_cassettes/``); otherwise falls back to the sibling next to the source
        ``.py``. Encapsulates the read-precedence shared by ``cassette_path`` and
        ``replay_cassette_path`` so both honour the same layout detection.
        """
        topic_dir = self.path.parent
        for sub in _CASSETTE_SUBDIRS:
            candidate = topic_dir / sub / cassette_name
            if candidate.exists():
                return candidate
        sibling = topic_dir / cassette_name
        return sibling if sibling.exists() else None

    @property
    def cassette_path(self) -> Path | None:
        """Return the HTTP-replay cassette path if present, else None.

        Prefers ``<topic_dir>/cassettes/<stem>.http-cassette.yaml`` (or the
        legacy ``_cassettes/``) when that layout is in use; otherwise falls back
        to the sibling ``<topic_dir>/<stem>.http-cassette.yaml``.
        """
        return self._resolve_cassette(f"{self.path.stem}.http-cassette.yaml")

    @property
    def expected_cassette_path(self) -> Path:
        """Return the path where a (possibly not-yet-existing) cassette should live.

        Used by record-capable modes (``once``, ``refresh``) so the bootstrap
        can activate vcrpy with a target path even on first-run when no
        cassette has been recorded yet. Resolution rule:

        - If a sidecar subdirectory (``cassettes/``, then legacy ``_cassettes/``)
          exists, write inside it.
        - Otherwise write next to the source ``.py``.
        """
        cassette_name = f"{self.path.stem}.http-cassette.yaml"
        topic_dir = self.path.parent
        for sub in _CASSETTE_SUBDIRS:
            if (topic_dir / sub).is_dir():
                return topic_dir / sub / cassette_name
        return topic_dir / cassette_name

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
    def replay_cassette_path(self) -> Path | None:
        """Return the cassette to *replay* from, with a language fallback.

        Prefers the strict, language-specific cassette (``cassette_path``).
        When that is absent and this is a split ``.de``/``.en`` deck, falls
        back to the base (bilingual) cassette that already holds both
        languages' recorded interactions, searching the sidecar subdirectory
        (``cassettes/`` then legacy ``_cassettes/``) before the sibling layout
        — same precedence as ``cassette_path`` (Issue #159).

        This is used **only** on the replay path. Record/seed/sweep keep using
        the strict, language-specific ``cassette_path`` so a re-record can
        neither overwrite the shared base nor inherit the other language's
        entries.
        """
        strict = self.cassette_path
        if strict is not None:
            return strict
        base_stem = _base_cassette_stem(self.path.stem)
        if base_stem is None:
            return None
        return self._resolve_cassette(f"{base_stem}.http-cassette.yaml")

    @property
    def replay_cassette_relative_name(self) -> str | None:
        """``replay_cassette_path`` relative to the topic dir (posix), if any.

        The replay counterpart of ``cassette_relative_name``; used as the
        kernel-cwd-relative path on the replay path only.
        """
        cassette = self.replay_cassette_path
        if cassette is None:
            return None
        return cassette.relative_to(self.path.parent).as_posix()

    @property
    def expected_cassette_relative_name(self) -> str:
        """Return the expected cassette path relative to topic dir (posix-style).

        Always returns a name (existence not required). Caller decides whether
        to use it based on the active record mode.
        """
        return self.expected_cassette_path.relative_to(self.path.parent).as_posix()

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

        # Phase 6: a split source file (``.de.py`` / ``.en.py``) only
        # emits output for its tagged language. We narrow the course-level
        # language filter by intersection so that ``--output-languages``
        # and target filters still apply. An empty intersection produces
        # no operations and the file is silently skipped (covered by
        # ``NoOperation`` at the end of this method).
        course_languages = self.course.output_languages
        effective_languages: list[str] | None
        if self.output_language_filter is not None:
            allowed = [self.output_language_filter]
            effective_languages = (
                [lang for lang in course_languages if lang in allowed]
                if course_languages
                else allowed
            )
        else:
            effective_languages = course_languages

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
                skip_evaluation=self.skip_evaluation,
                skip_errors=self.skip_errors,
                http_replay_mode=(self.course.http_replay_mode if self.http_replay else None),
            )
            for lang, format_, mode, output_dir in output_specs(
                self.course,
                target_dir,
                self.skip_html,
                languages=effective_languages,
                kinds=self.course.output_kinds,
                target=target,
            )
            if self.output_language_filter is None or lang == self.output_language_filter
        ]

        # Add implicit executions for cache population
        # These are needed when completed/trainer/partial HTML is requested
        # but recording HTML (the cache producer) is not explicitly requested
        if implicit_executions and stage == HTML_SPEAKER_STAGE:
            # Create operations for implicit executions that aren't already included
            existing_keys = {(op.language, op.format, op.kind) for op in operations}
            for lang, format_, kind in implicit_executions:
                # Phase 6: a split source file only handles its own
                # language. Skipping the mismatched implicit-execution
                # avoids running a kernel against half a deck.
                if self.output_language_filter is not None and lang != self.output_language_filter:
                    continue
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
                            skip_evaluation=self.skip_evaluation,
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
