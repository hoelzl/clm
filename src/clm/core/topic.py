import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

from attr import Factory, frozen

from clm.core.course_file import CourseFile
from clm.core.utils.notebook_mixin import NotebookMixin
from clm.core.utils.notebook_utils import find_images, find_imports
from clm.infrastructure.utils.path_utils import (
    is_ignored_dir_for_course,
    is_ignored_file_for_course,
    is_in_dir,
    prog_lang_to_extension,
)

if TYPE_CHECKING:
    from clm.core.course import Course
    from clm.core.course_spec import TopicSpec
    from clm.core.section import Section

logger = logging.getLogger(__name__)


@frozen
class ResolvedInclude:
    """An ``<include>`` resolved against the course root.

    Carries the on-disk source path (already verified to exist when
    ``optional=False``) and the relative target inside the topic
    directory. Build code consumes this rather than the raw spec so
    course-root resolution is done once at section-build time.
    """

    source_root: Path
    """Absolute path to the file or directory the include points at."""

    as_path: str
    """Forward-slash-relative target inside ``topic.path``."""

    optional: bool = False


@frozen
class Topic(NotebookMixin, ABC):
    id: str
    section: "Section"
    path: Path
    skip_html: bool = False
    skip_evaluation: bool = False
    skip_errors: bool = False
    http_replay: bool = False
    author: str = ""
    prog_lang_override: str = ""
    _file_map: dict[Path, CourseFile] = Factory(dict)
    # Resolved ``<include>`` declarations for this topic. Populated by
    # :meth:`Topic.from_spec` from
    # :meth:`SectionSpec.includes_for(topic_spec)` after resolution
    # against the course root. Empty for topics without includes.
    includes: list[ResolvedInclude] = Factory(list)

    @staticmethod
    def from_spec(
        spec: "TopicSpec",
        section: "Section",
        path: Path,
        *,
        includes: "list[ResolvedInclude] | None" = None,
    ):  # noqa
        cls: type[Topic] = FileTopic if path.is_file() else DirectoryTopic
        return cls(
            id=spec.id,
            section=section,
            path=path,
            skip_html=spec.skip_html,
            skip_evaluation=spec.skip_evaluation,
            skip_errors=spec.skip_errors,
            http_replay=spec.http_replay,
            author=spec.author,
            prog_lang_override=spec.prog_lang,
            includes=list(includes) if includes else [],
        )  # noqa

    @property
    def course(self) -> "Course":
        return self.section.course

    @property
    def files(self) -> list[CourseFile]:
        return list(self._file_map.values())

    @property
    def prog_lang(self):
        return self.course.spec.prog_lang

    def file_for_path(self, path: Path) -> CourseFile | None:
        return self._file_map.get(path)

    def add_file(self, path: Path, ignore_dir: bool = False):
        # We can add files that don't exist yet (e.g. generated files), so don't check
        # if the path resolves to a file.
        if not self.matches_path(path, False):
            logger.debug(f"Path not within topic: {path}")
            return
        if self.file_for_path(path):
            logger.debug(f"Duplicate path when adding file: {path}")
            return
        if path.is_dir():
            if not ignore_dir:
                logger.warning(f"Trying to add a directory to topic {self.id!r}: {path}")
            return
        try:
            self._file_map[path] = CourseFile.from_path(self.course, path, self)
        except Exception as e:
            logger.error(f"Error adding file '{path.name}': {e}")
            logger.debug(f"Error traceback for '{path.name}'", exc_info=e)
            # Track for later reporting to user
            self.course.loading_errors.append(
                {
                    "category": "file_load_error",
                    "message": f"Failed to load file '{path.name}': {e}",
                    "details": {
                        "file_path": str(path),
                        "topic_id": self.id,
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                    },
                }
            )

    def add_virtual_file(self, *, virtual_path: Path, source_origin: Path) -> bool:
        """Splice a file in from outside the topic directory.

        Used by ``build_file_map`` to materialize ``<include>`` entries
        without copying them to disk. ``virtual_path`` is the file's
        logical position inside the topic (used for ``relative_path`` and
        output mapping); ``source_origin`` is where the bytes actually
        live. If a real file already occupies ``virtual_path``, the real
        file wins and a structured warning is recorded — this preserves
        the pre-include "drop a file, it ships" override path.

        Returns True when the virtual file was added; False when it was
        shadowed by a real file or otherwise rejected.
        """
        if self.file_for_path(virtual_path):
            self.course.loading_warnings.append(
                {
                    "category": "include_shadowed_by_local",
                    "message": (
                        f"Topic '{self.id}': a real file at "
                        f"'{virtual_path}' shadows the include from "
                        f"'{source_origin}'. The local file wins; the "
                        f"include is ignored for this path."
                    ),
                    "details": {
                        "topic_id": self.id,
                        "virtual_path": str(virtual_path),
                        "source_origin": str(source_origin),
                    },
                }
            )
            return False
        try:
            self._file_map[virtual_path] = CourseFile.from_virtual(
                self.course,
                virtual_path=virtual_path,
                source_origin=source_origin,
                topic=self,
            )
        except Exception as e:  # pragma: no cover — defensive
            logger.error(
                f"Error adding virtual include file '{virtual_path}' from '{source_origin}': {e}"
            )
            logger.debug(f"Virtual include traceback for '{virtual_path}'", exc_info=e)
            self.course.loading_errors.append(
                {
                    "category": "include_load_error",
                    "message": (
                        f"Failed to load include file '{virtual_path}' from '{source_origin}': {e}"
                    ),
                    "details": {
                        "topic_id": self.id,
                        "virtual_path": str(virtual_path),
                        "source_origin": str(source_origin),
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                    },
                }
            )
            return False
        return True

    def apply_includes(self):
        """Splice every resolved ``<include>`` into the file map.

        Called after the topic's own files have been added by the
        subclass-specific ``build_file_map`` so that real local files
        shadow virtual include entries (Feature 1 design, decision #1
        of the resolved-decisions list).

        Each include's source root is walked recursively (skipping
        directories filtered by ``SKIP_DIRS_FOR_COURSE``) and the
        discovered files are added under
        ``topic.path / include.as_path / <relative-to-source-root>``.
        Single-file includes register a single entry at
        ``topic.path / include.as_path``.
        """
        for include in self.includes:
            source = include.source_root
            if not source.exists():
                if include.optional:
                    logger.debug(
                        f"Topic '{self.id}': optional include "
                        f"'{include.as_path}' source '{source}' missing — "
                        f"skipping per optional=true."
                    )
                    continue
                # Existence is also enforced earlier in resolve. This
                # branch is a defensive fallback for the (unlikely) race
                # where the source disappears between resolve and
                # build_file_map.
                self.course.loading_errors.append(
                    {
                        "category": "include_source_missing",
                        "message": (
                            f"Topic '{self.id}': include source "
                            f"'{source}' (as '{include.as_path}') does "
                            f"not exist."
                        ),
                        "details": {
                            "topic_id": self.id,
                            "as_path": include.as_path,
                            "source": str(source),
                        },
                    }
                )
                continue
            target_root = self.path / include.as_path
            if source.is_file():
                self.add_virtual_file(virtual_path=target_root, source_origin=source)
                continue
            for sub in source.rglob("*"):
                if not sub.is_file():
                    continue
                if is_ignored_file_for_course(sub):
                    continue
                if any(
                    part in {".git", ".venv", "__pycache__", "node_modules"}
                    or is_ignored_dir_for_course(Path(part))
                    for part in sub.relative_to(source).parts[:-1]
                ):
                    continue
                rel = sub.relative_to(source)
                self.add_virtual_file(virtual_path=target_root / rel, source_origin=sub)

    @abstractmethod
    def matches_path(self, path: Path, check_is_file: bool = True) -> bool:
        """Returns True if the path is within the topic directory."""
        ...

    @abstractmethod
    def build_file_map(self): ...

    # Helper method for implementing build_file_map
    def add_files_in_dir(self, dir_path):
        for file in sorted(dir_path.iterdir()):
            if file.is_file():
                if is_ignored_file_for_course(file):
                    continue
                self.add_file(file)
            elif file.is_dir() and not is_ignored_dir_for_course(file):
                for sub_file in file.glob("**/*"):
                    if is_ignored_file_for_course(sub_file):
                        continue
                    self.add_file(sub_file)


@frozen
class DirectoryTopic(Topic):
    def matches_path(self, path: Path, check_is_file: bool = True) -> bool:
        return is_in_dir(path, self.path, check_is_file)

    def build_file_map(self):
        logger.debug(f"Building file map for file {self.path}")
        self.add_files_in_dir(self.path)
        # Includes splice in *after* real files so a real local file at
        # the same relative path always wins (recorded as a warning by
        # add_virtual_file).
        if self.includes:
            self.apply_includes()


@frozen
class FileTopic(Topic):
    def matches_path(self, path: Path, check_is_file: bool = True) -> bool:
        return is_in_dir(path, self.path.parent, check_is_file)

    def build_file_map(self):
        logger.debug(f"Building file map for directory {self.path}")
        self.add_file(self.path)
        with self.path.open(encoding="utf-8") as f:
            contents = f.read()
        if contents:
            included_images = find_images(contents)
            included_modules = find_imports(contents)
            ext = prog_lang_to_extension(self.prog_lang)
            included_module_files = {module + ext for module in included_modules}
            logger.debug(f"Found images: {included_images} and modules: {included_modules}")
            for file in included_images | included_module_files:
                self.add_file(self.path.parent / file)
        if self.includes:
            self.apply_includes()
