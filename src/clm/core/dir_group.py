import logging
from pathlib import Path
from typing import TYPE_CHECKING

from attrs import evolve, frozen

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

    def _without_seen_copies(
        self,
        is_speaker: bool,
        lang: str,
        output_root: Path | None,
        skip_toplevel: bool,
        seen_copy_keys: set,
    ) -> "DirGroup | None":
        """Drop the copy targets of this variant already covered by an earlier one.

        Deduplication is per copied directory (and per root-file batch), not
        per dir-group: two dir-groups that merely *overlap* in one ``<subdir>``
        resolving to the same destination must not both copy that subdir —
        concurrent ``shutil.copytree`` calls on the same destination race,
        which on Windows aborts the build with WinError 32 sharing violations
        (issue #539). Each key deliberately includes the source: operations
        writing *different* content to the same destination are a spec
        conflict, not a duplicate, and must stay visible to the output-write
        registry's conflict detection (``clm validate`` warns about them as
        ``duplicate_dir_group_destination``).

        Returns this dir-group with only the not-yet-covered copy targets
        (``self`` when nothing was covered), or ``None`` when every target is
        already covered and no operation is needed.
        """
        output_path = self.output_path(is_speaker, lang, output_root, skip_toplevel=skip_toplevel)

        base_path = self.base_path
        if base_path is not None:
            root_key = ("root-files", output_path, base_path)
            if root_key in seen_copy_keys:
                base_path = None
            else:
                seen_copy_keys.add(root_key)

        kept_sources: list[Path] = []
        kept_relative: list[Path] = []
        for source_dir, relative_path in zip(self.source_dirs, self.relative_paths, strict=True):
            dir_key = ("dir", output_path / relative_path, source_dir, self.recursive)
            if dir_key in seen_copy_keys:
                continue
            seen_copy_keys.add(dir_key)
            kept_sources.append(source_dir)
            kept_relative.append(relative_path)

        if base_path is None and not kept_sources:
            return None
        if base_path == self.base_path and len(kept_sources) == len(self.source_dirs):
            return self
        return evolve(
            self,
            source_dirs=tuple(kept_sources),
            relative_paths=tuple(kept_relative),
            base_path=base_path,
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
                           that would write the same source to the same
                           destination. Pass one shared set across all
                           dir-groups of a build to also collapse duplicates
                           between dir-groups; if None, deduplication is
                           limited to this call. Deduplication is per copied
                           directory, so it matters both for explicit output
                           targets (the public/speaker split collapses to a
                           single path and both is_speaker variants would
                           otherwise copy the same tree concurrently) and for
                           dir-groups that overlap in a single <subdir>
                           (issue #539).
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
                remaining = self._without_seen_copies(
                    is_speaker, lang, output_root, skip_toplevel, seen_copy_keys
                )
                if remaining is None:
                    logger.debug(f"Skipping fully duplicate dir-group copy of '{self.name[lang]}'")
                    continue
                operations.append(
                    CopyDirGroupOperation(
                        dir_group=remaining,
                        lang=lang,
                        is_speaker=is_speaker,
                        output_root=output_root,
                        skip_toplevel=skip_toplevel,
                    )
                )

        if not operations:
            return NoOperation()

        return Concurrently(tuple(operations))
