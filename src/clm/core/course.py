import logging
import sys
from collections import defaultdict

# TaskGroup is available in Python 3.11+
if sys.version_info >= (3, 11):
    from asyncio import TaskGroup
else:
    from asyncio import gather as _gather

    class TaskGroup:
        """Minimal TaskGroup shim for Python 3.10 compatibility."""

        def __init__(self):
            self._tasks: list = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            if self._tasks:
                await _gather(*self._tasks)

        def create_task(self, coro):
            import asyncio

            task = asyncio.create_task(coro)
            self._tasks.append(task)
            return task


from pathlib import Path
from typing import TYPE_CHECKING

from attrs import Factory, define

from clm.core.course_file import CourseFile
from clm.core.course_spec import CourseSpec
from clm.core.dir_group import DirGroup
from clm.core.execution_dependencies import ExecutionDependencyResolver
from clm.core.image_registry import ImageRegistry
from clm.core.output_target import OutputTarget
from clm.core.section import Section
from clm.core.topic import Topic
from clm.core.utils.execution_utils import (
    HTML_SPEAKER_STAGE,
    execution_stages,
)
from clm.core.utils.notebook_mixin import NotebookMixin
from clm.core.utils.text_utils import Text
from clm.infrastructure.backend import Backend
from clm.infrastructure.operation import NoOperation
from clm.infrastructure.utils.file import File
from clm.infrastructure.utils.path_utils import (
    is_ignored_dir_for_course,
    is_image_file,
    is_in_dir,
    simplify_ordered_name,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@define
class Course(NotebookMixin):
    spec: CourseSpec
    course_root: Path
    output_root: Path  # Primary output root (for backward compatibility)
    code_dir: str = "Python"
    sections: list[Section] = Factory(list)
    dir_groups: list[DirGroup] = Factory(list)
    _topic_path_map: dict[str, Path] = Factory(dict)
    output_languages: list[str] | None = None
    output_kinds: list[str] | None = None
    fallback_execute: bool = False
    # Track issues encountered during course loading for later reporting
    loading_warnings: list[dict] = Factory(list)
    loading_errors: list[dict] = Factory(list)
    # Multiple output targets support
    output_targets: list[OutputTarget] = Factory(list)
    # Implicit executions needed for cache population
    implicit_executions: set[tuple[str, str, str]] = Factory(set)
    # Image registry for collision detection
    image_registry: ImageRegistry = Factory(ImageRegistry)
    # Image storage mode: "duplicated" (default) or "shared"
    image_mode: str = "duplicated"
    # Image output format: "png" (default) or "svg"
    image_format: str = "png"
    # Whether to inline images as data URLs in notebooks
    inline_images: bool = False

    @classmethod
    def from_spec(
        cls,
        spec: CourseSpec,
        course_root: Path,
        output_root: Path | None,
        output_languages: list[str] | None = None,
        output_kinds: list[str] | None = None,
        fallback_execute: bool = False,
        selected_targets: list[str] | None = None,
        image_mode: str = "duplicated",
        image_format: str = "png",
        inline_images: bool = False,
    ) -> "Course":
        """Create a Course from a CourseSpec.

        Args:
            spec: The parsed course specification
            course_root: Root directory of the course source
            output_root: Override output directory (None = use spec targets)
            output_languages: Filter languages (applies to all targets)
            output_kinds: Filter kinds (applies to all targets)
            fallback_execute: Whether to fall back to execution on cache miss
            selected_targets: List of target names to build (None = all)
            image_mode: Image storage mode ("duplicated" or "shared")
            image_format: Image output format ("png" or "svg")
            inline_images: Whether to inline images as data URLs in notebooks

        Returns:
            Configured Course instance
        """
        # Determine output targets
        if output_root is not None:
            # CLI override: use single output directory with all outputs
            targets = [OutputTarget.default_target(output_root)]
            effective_output_root = output_root
        elif spec.output_targets:
            # Use targets from spec file
            targets = [OutputTarget.from_spec(t, course_root) for t in spec.output_targets]
            # Filter by selected targets if specified
            if selected_targets:
                targets = [t for t in targets if t.name in selected_targets]
                if not targets:
                    available = [t.name for t in spec.output_targets]
                    raise ValueError(
                        f"No matching targets found. "
                        f"Requested: {selected_targets}, "
                        f"Available: {available}"
                    )
            # Use first target's root as the "primary" for legacy compatibility
            effective_output_root = targets[0].output_root if targets else course_root / "output"
        else:
            # No targets in spec, no CLI override: use default
            effective_output_root = course_root / "output"
            targets = [OutputTarget.default_target(effective_output_root)]

        # Apply CLI-level language/kind filters to all targets
        if output_languages or output_kinds:
            targets = [t.with_cli_filters(output_languages, output_kinds) for t in targets]

        # Resolve implicit execution dependencies
        resolver = ExecutionDependencyResolver()
        explicit, implicit = resolver.get_all_required_executions(targets)
        if implicit:
            logger.info(f"Implicit executions required for cache population: {implicit}")

        logger.debug(f"Creating course from spec {spec}: {course_root} -> {effective_output_root}")
        logger.info(f"Output targets: {[t.name for t in targets]}")

        course = cls(
            spec,
            course_root,
            effective_output_root,
            output_languages=output_languages,
            output_kinds=output_kinds,
            fallback_execute=fallback_execute,
            output_targets=targets,
            implicit_executions=implicit,
            image_mode=image_mode,
            image_format=image_format,
            inline_images=inline_images,
        )
        course._build_sections()
        course._build_dir_groups()
        course._add_source_output_files()
        course._collect_images()
        return course

    @property
    def name(self) -> Text:
        return self.spec.name

    @property
    def prog_lang(self) -> str:
        return self.spec.prog_lang

    @property
    def topics(self) -> list[Topic]:
        return [topic for section in self.sections for topic in section.topics]

    @property
    def files(self) -> list[CourseFile]:
        return [file for section in self.sections for file in section.files]

    def find_file(self, path: Path) -> File | None:
        """Return a File, if path exists in the course, None otherwise."""
        abspath = path.resolve()
        for dir_group in self.dir_groups:
            for source_dir in dir_group.source_dirs:
                if is_in_dir(abspath, source_dir):
                    return File(path=abspath)
        return self.find_course_file(abspath)

    def find_course_file(self, path: Path) -> CourseFile | None:
        """Return a File, if path is in the course but not in a directory group"""
        abspath = path.resolve()
        for file in self.files:
            if file.path.resolve() == abspath:
                return file
        return None

    def add_file(self, path: Path, warn_if_no_topic: bool = True) -> Topic | None:
        for topic in self.topics:
            if topic.matches_path(path, False):
                topic.add_file(path)
                return topic
        if warn_if_no_topic:
            logger.warning(f"File not in course structure: {path}")
        else:
            logger.debug(f"File not in course structure: {path}")
        return None

    # TODO: Perhaps all the processing logic should be moved out of this class?
    async def process_file(self, backend: Backend, path: Path):
        """Process a single changed file for all output targets."""
        logging.info(f"Processing changed file {path}")
        file = self.find_course_file(path)
        if not file:
            logger.warning(f"Cannot process file: not in course: {path}")
            return

        # Process file for each output target
        for target in self.output_targets:
            op = await file.get_processing_operation(target.output_root, target=target)
            await op.execute(backend)
            logger.debug(f"Processed file {path} for target '{target.name}'")

    async def process_all(self, backend: Backend):
        """Process all files for all output targets."""
        logger.info(f"Processing all files for {self.course_root}")
        logger.info(f"Output targets: {[t.name for t in self.output_targets]}")

        for stage in execution_stages():
            logger.debug(f"Processing stage {stage}")

            # For HTML_SPEAKER_STAGE, we may need implicit executions
            # to populate the cache for outputs that REUSE_CACHE
            implicit_for_stage = self.implicit_executions if stage == HTML_SPEAKER_STAGE else set()

            for target in self.output_targets:
                logger.debug(f"Processing target '{target.name}' at {target.output_root}")
                num_operations = await self.process_stage_for_target(
                    stage, backend, target, implicit_for_stage
                )
                logger.debug(
                    f"Processed {num_operations} operations for "
                    f"stage {stage}, target '{target.name}'"
                )

        await self.process_dir_group_for_targets(backend)

    async def process_stage_for_target(
        self,
        stage: int,
        backend: Backend,
        target: OutputTarget,
        implicit_executions: set[tuple[str, str, str]] | None = None,
    ) -> int:
        """Process a single stage for a single target.

        Args:
            stage: Execution stage number
            backend: Backend for executing operations
            target: Output target to process
            implicit_executions: Additional executions needed for cache population

        Returns:
            Number of operations processed
        """
        num_operations = 0
        async with TaskGroup() as tg:
            for file in self.files:
                op = await file.get_processing_operation(
                    target.output_root,
                    stage=stage,
                    target=target,
                    implicit_executions=implicit_executions,
                )
                if not isinstance(op, NoOperation):
                    logger.debug(f"Processing file {file.path} for target '{target.name}'")
                    tg.create_task(op.execute(backend))
                    num_operations += 1
        await backend.wait_for_completion()
        return num_operations

    async def process_stage(self, stage: int, backend: Backend) -> int:
        """Process a single stage for all targets (backward compatibility).

        This method is kept for backward compatibility with existing code.
        """
        total_operations = 0
        implicit_for_stage = self.implicit_executions if stage == HTML_SPEAKER_STAGE else set()
        for target in self.output_targets:
            total_operations += await self.process_stage_for_target(
                stage, backend, target, implicit_for_stage
            )
        return total_operations

    async def count_stage_operations(self, stage: int) -> int:
        """Count the number of worker jobs that will be submitted for a stage.

        This method counts operations that will be submitted to workers,
        useful for progress reporting. It only counts operations with a
        service_name (i.e., operations that submit jobs to workers), not
        local operations like file copies.

        Args:
            stage: Execution stage number

        Returns:
            Number of worker jobs that will be submitted for this stage
        """
        from clm.infrastructure.operation import Concurrently, NoOperation

        def count_worker_ops(op):
            """Count operations that will submit jobs to workers."""
            if isinstance(op, NoOperation):
                return 0
            elif isinstance(op, Concurrently):
                return sum(count_worker_ops(inner) for inner in op.operations)
            elif op.service_name is not None:
                # This operation will submit a job to a worker
                return 1
            else:
                # Local operation (no worker job)
                return 0

        total_count = 0
        implicit_for_stage = self.implicit_executions if stage == HTML_SPEAKER_STAGE else set()

        for target in self.output_targets:
            for file in self.files:
                op = await file.get_processing_operation(
                    target.output_root,
                    stage=stage,
                    target=target,
                    implicit_executions=implicit_for_stage,
                )
                total_count += count_worker_ops(op)

        return total_count

    async def process_dir_group_for_targets(self, backend: Backend):
        """Process directory groups for all targets.

        Dir groups contain supplementary materials (README, examples, etc.)
        that should be available in all output directories, including both
        public and speaker directories as appropriate for each target.
        """
        async with TaskGroup() as tg:
            for dir_group in self.dir_groups:
                for target in self.output_targets:
                    # Determine which output types to generate based on target kinds
                    has_public = bool(target.kinds & {"code-along", "completed"})
                    has_speaker = "speaker" in target.kinds
                    is_speaker_options: list[bool] = []
                    if has_public:
                        is_speaker_options.append(False)
                    if has_speaker:
                        is_speaker_options.append(True)

                    if not is_speaker_options:
                        # Target has no valid kinds (after filtering), skip
                        continue

                    logger.debug(
                        f"Processing dir group {dir_group.name} for target '{target.name}'"
                    )
                    op = await dir_group.get_processing_operation(
                        output_root=target.output_root,
                        languages=target.languages,
                        is_speaker_options=is_speaker_options,
                        skip_toplevel=target.is_explicit,
                    )
                    tg.create_task(op.execute(backend))

    async def process_dir_group(self, backend: Backend):
        """Process directory groups (backward compatibility).

        This method is kept for backward compatibility.
        """
        await self.process_dir_group_for_targets(backend)

    def _build_sections(self):
        logger.debug(f"Building sections for {self.course_root}")
        self._build_topic_map()
        for section_spec in self.spec.sections:
            section = Section(name=section_spec.name, course=self)
            self._build_topics(section, section_spec)
            section.add_notebook_numbers()
            self.sections.append(section)

    def _build_topics(self, section, section_spec):
        for topic_spec in section_spec.topics:
            topic_path = self._topic_path_map.get(topic_spec.id)
            if not topic_path:
                logger.error(f"Topic not found: {topic_spec.id}")
                # Track for later reporting to user
                self.loading_errors.append(
                    {
                        "category": "topic_not_found",
                        "message": f"Topic '{topic_spec.id}' not found in filesystem",
                        "details": {
                            "topic_id": topic_spec.id,
                            "section": section_spec.name.en,
                            "available_topics": list(self._topic_path_map.keys())[:10],
                        },
                    }
                )
                continue
            topic = Topic.from_spec(spec=topic_spec, section=section, path=topic_path)
            topic.build_file_map()
            section.topics.append(topic)

    def _build_topic_map(self, rebuild: bool = False):
        """Build map from topic IDs to topic paths.

        Scans the slides directory for topics and creates a mapping from
        simplified topic IDs to their filesystem paths.

        Args:
            rebuild: If True, force rebuild even if map already exists
        """
        logger.debug(f"Building topic map for {self.course_root}")

        # Skip rebuild if map already populated
        if self._topic_path_map and not rebuild:
            return

        self._topic_path_map.clear()

        # Populate map from all valid topic paths
        for topic_id, topic_path in self._iterate_topic_paths():
            if existing_topic_path := self._topic_path_map.get(topic_id):
                logger.warning(
                    f"Duplicate topic id: {topic_id}: {topic_path} and {existing_topic_path}"
                )
                # Track for later reporting to user
                self.loading_warnings.append(
                    {
                        "category": "duplicate_topic_id",
                        "message": f"Duplicate topic ID '{topic_id}' - using first occurrence",
                        "details": {
                            "topic_id": topic_id,
                            "first_path": str(existing_topic_path),
                            "duplicate_path": str(topic_path),
                        },
                    }
                )
                continue

            self._topic_path_map[topic_id] = topic_path

        logger.debug(f"Built topic map with {len(self._topic_path_map)} topics")

    def _iterate_topic_paths(self):
        """Generate (topic_id, topic_path) pairs for all valid topics.

        Yields:
            Tuples of (topic_id, topic_path) for each valid topic found
            in the slides directory structure.
        """
        slides_dir = self.course_root / "slides"

        for module in slides_dir.iterdir():
            # Skip ignored or non-directory entries
            if is_ignored_dir_for_course(module):
                logger.debug(f"Skipping ignored dir while building topic map: {module}")
                continue

            if not module.is_dir():
                logger.debug(f"Skipping non-directory module while building topic map: {module}")
                continue

            # Process all topic directories within this module
            for topic_path in module.iterdir():
                topic_id = simplify_ordered_name(topic_path.name)

                if not topic_id:
                    logger.debug(f"Skipping topic with no id: {topic_path}")
                    continue

                yield topic_id, topic_path

    def _build_dir_groups(self):
        for dictionary_spec in self.spec.dictionaries:
            self.dir_groups.append(DirGroup.from_spec(dictionary_spec, self))

    def _add_source_output_files(self):
        logger.debug("Adding source output files.")
        for topic in self.topics:
            for file in topic.files:
                for new_file in file.source_outputs:
                    topic.add_file(new_file)
                    logger.debug(f"Added source output file: {new_file}")

    def _collect_images(self):
        """Collect all images from course files into the image registry.

        This populates the image_registry with all image files in the course,
        detecting filename collisions between images with different content.

        Note: Image collection is only performed in "shared" mode where collision
        detection is needed. In "duplicated" mode, each output variant has its
        own images, so collisions are handled naturally.
        """
        # Skip image collection in duplicated mode - not needed for collision detection
        if self.image_mode == "duplicated":
            logger.debug("Skipping image collection (duplicated mode)")
            return

        logger.debug("Collecting images for collision detection.")
        for file in self.files:
            if is_image_file(file.path):
                self.image_registry.register(file.path)
        logger.debug(
            f"Collected {len(self.image_registry.images)} images, "
            f"found {len(self.image_registry.collisions)} collision(s)"
        )

    def collect_output_directories(self) -> set[Path]:
        """Collect all output directories needed for course processing.

        This method identifies all directories that will be created during
        course processing. This is used for pre-creation of directories
        before Docker workers start, to avoid bind mount visibility issues.

        Returns:
            Set of Path objects for all output directories
        """
        from clm.core.utils.text_utils import sanitize_file_name
        from clm.infrastructure.utils.path_utils import output_specs

        directories: set[Path] = set()

        for target in self.output_targets:
            for output_spec in output_specs(
                self,
                target.output_root,
                skip_html=False,
                target=target,
            ):
                lang = output_spec.language
                output_dir = output_spec.output_dir

                # Add base output directory
                directories.add(output_dir)

                # Add section directories
                for section in self.sections:
                    section_dir = output_dir / sanitize_file_name(section.name[lang])
                    directories.add(section_dir)

        return directories

    def precreate_output_directories(self) -> int:
        """Pre-create all output directories needed for course processing.

        This should be called before Docker workers start processing to ensure
        all directories exist and are visible through bind mounts.

        Returns:
            Number of directories created
        """
        directories = self.collect_output_directories()
        created_count = 0

        for directory in sorted(directories):
            if not directory.exists():
                directory.mkdir(parents=True, exist_ok=True)
                created_count += 1
                logger.debug(f"Pre-created directory: {directory}")
            else:
                logger.debug(f"Directory already exists: {directory}")

        if created_count > 0:
            logger.info(f"Pre-created {created_count} output directories")

        return created_count

    def detect_duplicate_output_files(self) -> list[dict]:
        """Detect notebook files that would produce duplicate output file names.

        This method checks for notebooks that have the same output file name
        (based on number and title) within the same output directory. This
        causes unpredictable compilation results because files overwrite each
        other.

        Returns:
            List of duplicate info dicts, each containing:
            - output_name: The duplicate output file name
            - output_dir: The output directory where duplicates occur
            - files: List of source file paths that produce this output
        """
        from clm.core.course_files.notebook_file import NotebookFile
        from clm.infrastructure.utils.path_utils import ext_for, output_specs

        duplicates = []

        # Group notebooks by their output paths
        # Key: (output_dir, lang, format, kind, file_name) -> list of source files
        output_map: dict[tuple, list[Path]] = defaultdict(list)

        for file in self.files:
            if not isinstance(file, NotebookFile):
                continue

            # Get all output specs for this file
            for lang, format_, kind, output_dir in output_specs(
                self, self.output_root, file.skip_html
            ):
                ext = ext_for(format_, file.prog_lang)
                file_name = file.file_name(lang, ext)
                actual_output_dir = file.output_dir(output_dir, lang)

                key = (str(actual_output_dir), lang, format_, kind, file_name)
                output_map[key].append(file.path)

        # Find duplicates
        for key, source_files in output_map.items():
            if len(source_files) > 1:
                output_dir, lang, format_, kind, file_name = key
                duplicates.append(
                    {
                        "output_name": file_name,
                        "output_dir": output_dir,
                        "language": lang,
                        "format": format_,
                        "kind": kind,
                        "files": source_files,
                    }
                )

        return duplicates
