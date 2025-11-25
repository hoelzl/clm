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

from clx.core.course_file import CourseFile
from clx.core.course_spec import CourseSpec
from clx.core.dir_group import DirGroup
from clx.core.section import Section
from clx.core.topic import Topic
from clx.core.utils.execution_utils import execution_stages
from clx.core.utils.notebook_mixin import NotebookMixin
from clx.core.utils.text_utils import Text
from clx.infrastructure.backend import Backend
from clx.infrastructure.operation import NoOperation
from clx.infrastructure.utils.file import File
from clx.infrastructure.utils.path_utils import (
    is_ignored_dir_for_course,
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
    output_root: Path
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

    @classmethod
    def from_spec(
        cls,
        spec: CourseSpec,
        course_root: Path,
        output_root: Path | None,
        output_languages: list[str] | None = None,
        output_kinds: list[str] | None = None,
        fallback_execute: bool = False,
    ) -> "Course":
        if output_root is None:
            output_root = course_root / "output"
        logger.debug(f"Creating course from spec {spec}: {course_root} -> {output_root}")
        course = cls(
            spec,
            course_root,
            output_root,
            output_languages=output_languages,
            output_kinds=output_kinds,
            fallback_execute=fallback_execute,
        )
        course._build_sections()
        course._build_dir_groups()
        course._add_source_output_files()
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
        logging.info(f"Processing changed file {path}")
        file = self.find_course_file(path)
        if not file:
            logger.warning(f"Cannot process file: not in course: {path}")
            return
        op = await file.get_processing_operation(self.output_root)
        await op.execute(backend)
        logger.debug(f"Processed file {path}")

    async def process_all(self, backend: Backend):
        logger.info(f"Processing all files for {self.course_root}")
        for stage in execution_stages():
            logger.debug(f"Processing stage {stage} for {self.course_root}")
            num_operations = await self.process_stage(stage, backend)
            logger.debug(f"Processed {num_operations} files for stage {stage}")
        await self.process_dir_group(backend)

    async def process_stage(self, stage, backend):
        num_operations = 0
        async with TaskGroup() as tg:
            for file in self.files:
                # Pass stage to get_processing_operation() so files can filter their operations
                op = await file.get_processing_operation(self.output_root, stage=stage)
                # NoOperation.execute() is a no-op, so we count actual operations
                if not isinstance(op, NoOperation):
                    logger.debug(f"Processing file {file.path} for stage {stage}")
                    tg.create_task(op.execute(backend))
                    num_operations += 1
        await backend.wait_for_completion()
        return num_operations

    async def process_dir_group(self, backend):
        async with TaskGroup() as tg:
            for dir_group in self.dir_groups:
                logger.debug(f"Processing dir group {dir_group.name}")
                op = await dir_group.get_processing_operation()
                tg.create_task(op.execute(backend))

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
        from clx.core.course_files.notebook_file import NotebookFile
        from clx.infrastructure.utils.path_utils import ext_for, output_specs

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
