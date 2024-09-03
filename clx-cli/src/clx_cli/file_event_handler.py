import logging
from pathlib import Path

from clx_common.backend import Backend
from watchdog.events import PatternMatchingEventHandler

from clx.course import Course
from clx_common.utils.path_utils import is_ignored_dir_for_course

logger = logging.getLogger(__name__)


class FileEventHandler(PatternMatchingEventHandler):
    def __init__(self, course, data_dir, loop, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.course = course
        self.data_dir = data_dir
        self.loop = loop

    def on_created(self, event):
        src_path = Path(event.src_path)
        if not is_ignored_dir_for_course(src_path):
            self.loop.create_task(
                self.handle_event(self.on_file_created, "on_created", src_path)
            )

    def on_moved(self, event):
        src_path = Path(event.src_path)
        dest_path = Path(event.dest_path)
        if not is_ignored_dir_for_course(src_path):
            self.loop.create_task(
                self.handle_event(
                    self.on_file_moved, "on_moved", src_path, dest_path
                )
            )

    def on_deleted(self, event):
        src_path = Path(event.src_path)
        if not is_ignored_dir_for_course(src_path):
            self.loop.create_task(
                self.handle_event(self.on_file_deleted, "on_deleted", src_path)
            )

    def on_modified(self, event):
        src_path = Path(event.src_path)
        if not is_ignored_dir_for_course(src_path):
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

    @staticmethod
    async def handle_event(method, name, *args):
        try:
            await method(*args)
        except Exception as e:
            logging.error(f"{name}: Error handling event: {e}")
