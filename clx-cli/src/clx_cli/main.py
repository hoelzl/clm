import asyncio
import logging
from pathlib import Path
from time import time

import click

from clx.course import Course
from clx.course_spec import CourseSpec
from clx_common.messaging.correlation_ids import all_correlation_ids
from clx_faststream_backend.faststream_backend import (
    FastStreamBackend,
)
from clx_faststream_backend.faststream_backend_handlers import (
    clear_handler_errors,
    handler_error_lock,
    handler_errors,
)

# from watchdog.observers import Observer

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
        print(f"Error connecting to NATS: {type(e)}, {e}")


async def print_handler_errors(print_tracebacks=False):
    async with handler_error_lock:
        if handler_errors:
            print("\nThere were errors during processing:")
            for error in handler_errors:
                print_handler_error(error, print_traceback=print_tracebacks)
            print_error_summary()
        else:
            print("\nNo errors were detected during processing")


def print_handler_error(error, print_traceback=False):
    print_separator()
    print(f"{error.correlation_id}: {error.input_file_name} -> {error.output_file}")
    print(error.error)
    if print_traceback:
        print_separator("traceback", "-")
        print(error.traceback)


def print_error_summary():
    print_separator("Summary")
    error_plural = "error" if len(handler_errors) == 1 else "errors"
    print(f"{len(handler_errors)} {error_plural} occurred during processing")


async def print_all_correlation_ids():
    print_separator(char="-", section="Correlation IDs")
    print(f"Created {len(all_correlation_ids)} Correlation IDs")
    for cid, data in all_correlation_ids.items():
        print(f"  {cid}: {data.format_dependencies()}")


def print_separator(section: str = "", char: str = "="):
    if section:
        prefix = f"{char * 2} {section} "
    else:
        prefix = ""
    print(f"{prefix}{char * (72 - len(prefix))}")


async def print_and_clear_handler_errors(print_correlation_ids, print_tracebacks=False):
    await print_handler_errors(print_tracebacks=print_tracebacks)
    if print_correlation_ids:
        await print_all_correlation_ids()
    await clear_handler_errors()


async def main(
    spec_file, data_dir, output_dir, watch, print_tracebacks, print_correlation_ids
):
    start_time = time()
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
        end_time = time()
        await print_and_clear_handler_errors(
            print_correlation_ids=print_correlation_ids,
            print_tracebacks=print_tracebacks,
        )
        print_separator(char="-", section="Timing")
        print(f"Total time: {round(end_time - start_time, 2)} seconds")

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
@click.option(
    "--print-tracebacks",
    is_flag=True,
    help="Include tracebacks in the error summary.",
)
@click.option(
    "--print-correlation-ids",
    is_flag=True,
    help="Print all correlation IDs that were generated.",
)
def run_main(
    spec_file, data_dir, output_dir, watch, print_tracebacks, print_correlation_ids
):
    asyncio.run(
        main(
            spec_file,
            data_dir,
            output_dir,
            watch,
            print_tracebacks,
            print_correlation_ids,
        )
    )


if __name__ == "__main__":
    run_main()
