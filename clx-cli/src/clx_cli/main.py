import asyncio
import logging
from pathlib import Path

import click
from watchdog.events import PatternMatchingEventHandler
from watchdog.observers import Observer

from clx.course import Course
from clx.course_spec import CourseSpec
from clx.utils.path_utils import is_ignored_dir_for_course
from clx_faststream_backend.fast_stream_backend import FastStreamBackend

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
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
                self.handle_event(
                    self.course.on_file_created, src_path
                )
            )

    def on_moved(self, event):
        src_path = Path(event.src_path)
        dest_path = Path(event.dest_path)
        if not is_ignored_dir_for_course(src_path):
            self.loop.create_task(
                self.handle_event(
                    self.course.on_file_moved, src_path, dest_path
                )
            )

    def on_deleted(self, event):
        src_path = Path(event.src_path)
        if not is_ignored_dir_for_course(src_path):
            self.loop.create_task(
                self.handle_event(
                    self.course.on_file_deleted, src_path
                )
            )

    def on_modified(self, event):
        src_path = Path(event.src_path)
        if not is_ignored_dir_for_course(src_path):
            self.loop.create_task(
                self.handle_event(
                    self.course.on_file_modified, src_path
                )
            )

    @staticmethod
    async def handle_event(method, *args):
        try:
            await method(*args)
        except Exception as e:
            logging.error(f"Error handling event: {e}")


def setup_logging(log_level):
    logging.getLogger().setLevel(log_level)
    logging.getLogger("clx").setLevel(log_level)
    logging.getLogger(__name__).setLevel(log_level)


async def error_cb(e):
    if isinstance(e, TimeoutError):
        print(f"Timeout while connecting to NATS: {e!r}")
    else:
        print(f'Error connecting to NATS: {type(e)}, {e}')

async def main(spec_file, data_dir, output_dir, watch):
    spec_file = spec_file.absolute()
    setup_logging(logging.INFO)
    if data_dir is None:
        data_dir = spec_file.parents[1]
        logger.debug(f"Data directory set to {data_dir}")
        assert data_dir.exists(), f"Data directory {data_dir} does not exist."
    if output_dir is None:
        output_dir = data_dir / "output"
        output_dir.mkdir(exist_ok=True)
        logger.debug(f"Output directory set to {output_dir}")
    logger.info(
        f"Processing course from {spec_file.name} " f"in {data_dir} to {output_dir}"
    )
    spec = CourseSpec.from_file(spec_file)
    course = Course.from_spec(spec, data_dir, output_dir)
    async with FastStreamBackend() as backend:
        await course.process_all(backend)

    if watch:
        print("Watching is currently disabled!")
        return
        # logger.info("Watching for file changes")
        # loop = asyncio.get_event_loop()
        # event_handler = FileEventHandler(course, data_dir, loop, patterns=["*"])
        # observer = Observer()
        # observer.schedule(event_handler, str(data_dir), recursive=True)
        # observer.start()
        # try:
        #     while True:
        #         await asyncio.sleep(1)
        # except KeyboardInterrupt:
        #     observer.stop()
        # observer.join()


@click.command()
@click.argument(
    "spec-file",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--data-dir",
    "-d",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
)
@click.option(
    "--output-dir",
    "-o",
    type=click.Path(exists=False, file_okay=False, dir_okay=True, path_type=Path),
)
@click.option(
    "--watch",
    "-w",
    is_flag=True,
    help="Watch for file changes and automatically process them.",
)
def run_main(spec_file, data_dir, output_dir, watch):
    asyncio.run(main(spec_file, data_dir, output_dir, watch))


if __name__ == "__main__":
    run_main()
