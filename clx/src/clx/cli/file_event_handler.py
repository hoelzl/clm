import logging
import re
from pathlib import Path

from watchdog.events import PatternMatchingEventHandler

from clx.core.course import Course
from clx.infrastructure.backend import Backend
from clx.infrastructure.utils.path_utils import is_ignored_dir_for_course

logger = logging.getLogger(__name__)

IGNORED_FILE_REGEX = re.compile(r"\.~.*")

def is_ignored_file(path: Path) -> bool:
    return re.match(IGNORED_FILE_REGEX, path.name) is not None


class FileEventHandler(PatternMatchingEventHandler):
    def __init__(self, backend, course, data_dir, loop, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.course = course
        self.backend = backend
        self.data_dir = data_dir
        self.loop = loop

    def on_created(self, event):
        src_path = Path(event.src_path)
        if is_ignored_file(src_path) or is_ignored_dir_for_course(src_path):
            return
        self.loop.create_task(
            self.handle_event(self.on_file_created, "on_created", src_path)
        )

    def on_moved(self, event):
        src_path = Path(event.src_path)
        dest_path = Path(event.dest_path)
        if is_ignored_file(src_path) or is_ignored_dir_for_course(src_path):
            return
        self.loop.create_task(
            self.handle_event(self.on_file_moved, "on_moved", src_path, dest_path)
        )

    def on_deleted(self, event):
        src_path = Path(event.src_path)
        if is_ignored_file(src_path) or is_ignored_dir_for_course(src_path):
            return
        self.loop.create_task(
            self.handle_event(self.on_file_deleted, "on_deleted", src_path)
        )

    def on_modified(self, event):
        src_path = Path(event.src_path)
        if is_ignored_file(src_path) or  is_ignored_dir_for_course(src_path):
            return
        self.loop.create_task(
            self.handle_event(self.on_file_modified, "on_modified", src_path)
        )

    async def on_file_moved(
        self, course: Course, backend: Backend, src_path: Path, dest_path: Path
    ):
        logger.debug(f"On file moved: {src_path} -> {dest_path}")
        await self.on_file_deleted(course, backend, src_path)
        await self.on_file_created(course, backend, dest_path)

    @staticmethod
    async def on_file_deleted(course: Course, backend: Backend, file_to_delete: Path):
        logger.info(f"On file deleted: {file_to_delete}")
        file = course.find_course_file(file_to_delete)
        if not file:
            logger.debug(f"File not / no longer in course: {file_to_delete}")
            return
        await backend.delete_dependencies(file)

    @staticmethod
    async def on_file_created(course: Course, backend: Backend, path: Path):
        logger.debug(f"On file created: {path}")
        topic = course.add_file(path, warn_if_no_topic=False)
        if topic is not None:
            await course.process_file(backend, path)
        else:
            logger.debug(f"File not in course: {path}")

    @staticmethod
    async def on_file_modified(course: Course, backend: Backend, path: Path):
        logger.info(f"On file modified: {path}")
        if course.find_course_file(path):
            await course.process_file(backend, path)

    async def handle_event(self, method, name, *args):
        try:
            await method(self.course, self.backend, *args)
        except Exception as e:
            logging.error(f"{name}: Error handling event: {e}")
