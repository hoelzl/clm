import asyncio
import logging
from pathlib import Path

import click
# from watchdog.observers import Observer

from clx.course import Course
from clx.course_spec import CourseSpec
from clx_faststream_backend.faststream_backend import FastStreamBackend, \
    clear_handler_errors, handler_errors

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def setup_logging(log_level):
    logging.getLogger().setLevel(log_level)
    logging.getLogger("clx").setLevel(log_level)
    logging.getLogger(__name__).setLevel(log_level)


async def error_cb(e):
    if isinstance(e, TimeoutError):
        print(f"Timeout while connecting to NATS: {e!r}")
    else:
        print(f'Error connecting to NATS: {type(e)}, {e}')


def print_handler_errors():
    if handler_errors:
        print("\nThere were errors during processing:")
        for error_msg, traceback in handler_errors:
            print("=" * 72)
            print(error_msg)
            prefix = "-- traceback "
            print(f"{prefix}{'-' * (72 - len(prefix))}")
            print(traceback)
    else:
        print("\nNo errors were detected during processing")


def print_and_clear_handler_errors():
    print_handler_errors()
    clear_handler_errors()


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
        print_and_clear_handler_errors()


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
