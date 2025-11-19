import asyncio
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
    def __init__(
        self, backend, course, data_dir, loop, debounce_delay: float = 0.3, *args, **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.course = course
        self.backend = backend
        self.data_dir = data_dir
        self.loop = loop
        self.error_count = 0
        self.max_errors = 10  # Stop watch mode after 10 errors
        self.debounce_delay = debounce_delay  # Debounce delay in seconds
        self._pending_tasks: dict[tuple, asyncio.Task] = {}  # (method, args) -> task

    def on_created(self, event):
        src_path = Path(event.src_path)
        if is_ignored_file(src_path) or is_ignored_dir_for_course(src_path):
            return
        self._schedule_debounced_task(self.on_file_created, "on_created", src_path)

    def on_moved(self, event):
        src_path = Path(event.src_path)
        dest_path = Path(event.dest_path)
        if is_ignored_file(src_path) or is_ignored_dir_for_course(src_path):
            return
        self._schedule_debounced_task(self.on_file_moved, "on_moved", src_path, dest_path)

    def on_deleted(self, event):
        src_path = Path(event.src_path)
        if is_ignored_file(src_path) or is_ignored_dir_for_course(src_path):
            return
        self._schedule_debounced_task(self.on_file_deleted, "on_deleted", src_path)

    def on_modified(self, event):
        src_path = Path(event.src_path)
        if is_ignored_file(src_path) or is_ignored_dir_for_course(src_path):
            return
        self._schedule_debounced_task(self.on_file_modified, "on_modified", src_path)

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
        logger.info(f"⟳ File modified: {path.name}")
        if course.find_course_file(path):
            await course.process_file(backend, path)

    def _schedule_debounced_task(self, method, event_name: str, *args):
        """Schedule a debounced task for file processing.

        This method implements event debouncing to avoid processing the same file
        multiple times when rapid file changes occur (e.g., text editor auto-save).

        Args:
            method: The handler method to call
            event_name: Event name for logging
            *args: Arguments to pass to the handler method
        """
        # Create a unique key for this event (method + first arg which is the path)
        key = (method.__name__, args[0] if args else None)

        # Cancel any existing pending task for this file/event
        if key in self._pending_tasks:
            prev_task = self._pending_tasks[key]
            if not prev_task.done():
                prev_task.cancel()
                logger.debug(
                    f"Cancelled previous {event_name} task for {args[0] if args else 'unknown'}"
                )

        # Schedule new debounced task
        async def debounced_execution():
            try:
                # Wait for the debounce delay
                await asyncio.sleep(self.debounce_delay)

                # Remove from pending tasks before executing
                if key in self._pending_tasks:
                    del self._pending_tasks[key]

                # Execute the actual handler
                await self.handle_event(method, event_name, *args)

            except asyncio.CancelledError:
                # Task was cancelled by a newer event
                logger.debug(
                    f"Debounced {event_name} task cancelled for {args[0] if args else 'unknown'}"
                )
                raise

        # Create and store the task
        task = self.loop.create_task(debounced_execution())
        self._pending_tasks[key] = task

    async def handle_event(self, method, name, *args):
        """Handle a file system event with error tracking.

        Args:
            method: The handler method to call
            name: Event name for logging
            *args: Arguments to pass to the handler method

        Raises:
            Exception: Re-raises after max_errors threshold is reached
        """
        try:
            await method(self.course, self.backend, *args)
        except Exception as e:
            self.error_count += 1
            logger.error(
                f"{name}: Error handling event ({self.error_count}/{self.max_errors}): {e}",
                exc_info=True,
            )

            if self.error_count >= self.max_errors:
                logger.error(f"Too many errors in watch mode ({self.error_count}), stopping")
                raise  # Propagate exception to stop watch mode
