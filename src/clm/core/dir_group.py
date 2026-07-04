import logging
from pathlib import Path
from typing import TYPE_CHECKING

from attrs import frozen

from clm.core.course_spec import DirGroupSpec
from clm.core.utils.text_utils import Text
from clm.infrastructure.operation import Operation
from clm.infrastructure.utils.path_utils import (
    output_path_for,
)

if TYPE_CHECKING:
    from clm.core.course import Course

logger = logging.getLogger(__name__)


@frozen
class DirGroup:
    name: Text
    source_dirs: tuple[Path, ...]
    relative_paths: tuple[Path, ...]
    course: "Course"
    base_path: Path | None = None
    recursive: bool = True
    # The spec this dir-group was built from, retained so the build/release
    # tooling can read its (section_id, topic_id) ownership (issue #208).
    # ``None`` only for dir-groups constructed without a spec.
    spec: DirGroupSpec | None = None

    @classmethod
    def from_spec(cls, spec: DirGroupSpec, course: "Course") -> "DirGroup":
        source_path = course.course_root / spec.path

        # Determine source_dirs based on subdirs and recursive settings
        if spec.subdirs:
            source_dirs = tuple(source_path / subdir for subdir in spec.subdirs)
            relative_paths = tuple(Path(path) for path in spec.subdirs)
        elif spec.recursive:
            # No subdirs, recursive=True: copy entire directory
            source_dirs = (source_path,)
            relative_paths = (Path(""),)
        else:
            # No subdirs, recursive=False: don't copy any directories
            source_dirs = ()
            relative_paths = ()

        # Set base_path when include_root_files is True
        base_path = source_path if spec.include_root_files else None

        return cls(
            name=spec.name,
            source_dirs=source_dirs,
            relative_paths=relative_paths,
            course=course,
            base_path=base_path,
            recursive=spec.recursive,
            spec=spec,
        )

    @property
    def output_root(self) -> Path:
        return self.course.output_root

    def output_path(
        self,
        is_speaker: bool,
        lang: str,
        output_root: Path | None = None,
        skip_toplevel: bool = False,
    ) -> Path:
        root = output_root if output_root is not None else self.output_root
        return (
            output_path_for(
                root,
                is_speaker,
                lang,
                self.course.output_dir_name[lang],
                skip_toplevel=skip_toplevel,
            )
            / self.name[lang]
        )

    def output_dirs(
        self,
        is_speaker: bool,
        lang: str,
        output_root: Path | None = None,
        skip_toplevel: bool = False,
    ) -> tuple[Path, ...]:
        return tuple(
            self.output_path(is_speaker, lang, output_root, skip_toplevel=skip_toplevel) / dir_
            for dir_ in self.relative_paths
        )

    def _copy_key(
        self,
        is_speaker: bool,
        lang: str,
        output_root: Path | None,
        skip_toplevel: bool,
    ) -> tuple:
        """Identity of a copy operation's effect on disk.

        Two operations with the same key copy the same sources to the same
        destination, so all but one are redundant — and running them
        concurrently makes ``shutil.copytree`` race against itself, which on
        Windows raises WinError 32 (sharing violation). The key deliberately
        includes the sources: operations writing *different* content to the
        same destination are a spec conflict, not a duplicate, and must stay
        visible to the output-write registry's conflict detection.
        """
        return (
            self.output_path(is_speaker, lang, output_root, skip_toplevel=skip_toplevel),
            self.source_dirs,
            self.relative_paths,
            self.base_path,
            self.recursive,
        )

    async def get_processing_operation(
        self,
        output_root: Path | None = None,
        languages: frozenset[str] | None = None,
        is_speaker_options: list[bool] | None = None,
        skip_toplevel: bool = False,
        seen_copy_keys: set | None = None,
    ) -> "Operation":
        """Get the operation to copy this directory group.

        Args:
            output_root: Optional override for output root directory.
                        If None, uses the course's output_root.
            languages: Optional set of languages to copy for.
                      If None, copies for all languages (de, en).
            is_speaker_options: List of is_speaker values to copy for.
                               If None, defaults to [False, True] for both public and speaker.
            skip_toplevel: If True, skip the "public"/"speaker" directory prefix.
                          Used for explicitly specified output targets.
            seen_copy_keys: Mutable set used to deduplicate copy operations
                           that would write the same sources to the same
                           destination. Pass one shared set across all
                           dir-groups of a build to also collapse duplicates
                           between dir-groups; if None, deduplication is
                           limited to this call. Deduplication matters for
                           explicit output targets, where the public/speaker
                           split collapses to a single path and both
                           is_speaker variants would otherwise copy the same
                           tree concurrently.
        """
        from clm.core.operations.copy_dir_group import CopyDirGroupOperation
        from clm.infrastructure.operation import Concurrently, NoOperation

        # Default to all languages if not specified
        langs_to_copy = languages if languages is not None else frozenset({"de", "en"})

        # Default to both public and speaker if not specified
        speaker_options = is_speaker_options if is_speaker_options is not None else [False, True]

        if seen_copy_keys is None:
            seen_copy_keys = set()

        operations = []
        for lang in sorted(langs_to_copy):
            for is_speaker in speaker_options:
                key = self._copy_key(is_speaker, lang, output_root, skip_toplevel)
                if key in seen_copy_keys:
                    logger.debug(
                        f"Skipping duplicate dir-group copy of '{self.name[lang]}' to {key[0]}"
                    )
                    continue
                seen_copy_keys.add(key)
                operations.append(
                    CopyDirGroupOperation(
                        dir_group=self,
                        lang=lang,
                        is_speaker=is_speaker,
                        output_root=output_root,
                        skip_toplevel=skip_toplevel,
                    )
                )

        if not operations:
            return NoOperation()

        return Concurrently(tuple(operations))
