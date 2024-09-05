import asyncio
import logging
import os
from base64 import b64encode
from pathlib import Path
from tempfile import TemporaryDirectory

import aiofiles
from aio_pika import RobustConnection
from aio_pika.abc import AbstractRobustChannel
from aiormq.abc import AbstractChannel
from faststream import FastStream
from faststream.rabbit import RabbitBroker

from clx_common.messaging.base_classes import (
    ImageResult,
    ImageResultOrError,
    ProcessingError,
)
from clx_common.messaging.drawio_classes import (
    DrawioPayload,
)
from clx_common.messaging.routing_keys import (
    DRAWIO_PROCESS_ROUTING_KEY,
    IMG_RESULT_ROUTING_KEY,
)
from clx_common.services.subprocess_tools import NUM_RETRIES, run_subprocess

# Configuration
RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost/")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG").upper()

# Set up logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    # format="%(asctime)s - %(levelname)s - drawio-converter - %(message)s",
)
logger = logging.getLogger(__name__)

# Set up RabbitMQ broker
broker = RabbitBroker(RABBITMQ_URL)
app = FastStream(broker)


@broker.subscriber(DRAWIO_PROCESS_ROUTING_KEY)
@broker.publisher(IMG_RESULT_ROUTING_KEY)
async def process_drawio(payload: DrawioPayload) -> ImageResultOrError:
    cid = payload.correlation_id
    try:
        for i in range(NUM_RETRIES):
            result = await process_drawio_file(payload)
            logger.debug(f"{cid}:Raw result:iteration {i}:{len(result)} bytes")
            if len(result) > 0:
                break
        else:
            # This block is executed if the loop completes without breaking
            raise ValueError(f"Empty result for {cid} after {NUM_RETRIES} attempts")

        encoded_result = b64encode(result)
        logger.debug(f"{cid}:Result: {len(result)} bytes: {encoded_result[:20]}")
        return ImageResult(
            result=encoded_result,
            correlation_id=payload.correlation_id,
            output_file=payload.output_file,
        )
    except Exception as e:
        file_name = payload.output_file_name
        logger.error(f"{cid}:Error while processing DrawIO file '{file_name}': {e}")
        logger.debug(f"{cid}:Error traceback for '{file_name}'", exc_info=e)
        correlation_id = payload.correlation_id
        return ProcessingError(
            error=str(e),
            correlation_id=correlation_id,
            input_file=payload.input_file,
            input_file_name=payload.input_file_name,
            output_file=payload.output_file,
        )


async def process_drawio_file(payload: DrawioPayload) -> bytes:
    with TemporaryDirectory() as tmp_dir:
        input_path = Path(tmp_dir) / "input.drawio"
        output_path = Path(tmp_dir) / f"output.{payload.output_format}"
        async with aiofiles.open(input_path, "w") as f:
            await f.write(payload.data)
        async with aiofiles.open(output_path, "wb") as f:
            await f.write(b"")
        await convert_drawio(
            input_path, output_path, payload.output_format, payload.correlation_id
        )
        async with aiofiles.open(output_path, "rb") as f:
            return await f.read()


async def convert_drawio(
    input_path: Path, output_path: Path, output_format: str, correlation_id
):
    logger.debug(f"{correlation_id}:Converting {input_path} to {output_path}")
    # Base command
    cmd = [
        "drawio",
        "--no-sandbox",
        "--export",
        input_path.as_posix(),
        "--format",
        output_format,
        "--output",
        output_path.as_posix(),
        "--border",
        "20",
    ]

    # Format-specific options
    if output_format == "png":
        cmd.extend(["--scale", "3"])  # Increase resolution (roughly 300 DPI)
    elif output_format == "svg":
        cmd.append("--embed-svg-images")  # Embed fonts in SVG

    env = os.environ.copy()
    env["DISPLAY"] = ":99"

    logger.debug(f"{correlation_id}:Creating subprocess...")
    process, stdout, stderr = await run_subprocess(cmd, correlation_id)

    logger.debug(f"{correlation_id}:Return code: {process.returncode}")
    logger.debug(f"{correlation_id}:stdout:{stdout.decode()}")
    logger.debug(f"{correlation_id}:stderr:{stderr.decode()}")

    if process.returncode == 0:
        logger.info(f"{correlation_id}:Converted {input_path} to {output_path}")
    else:
        logger.error(
            f"{correlation_id}:Error converting {input_path}:{stderr.decode()}"
        )
        raise RuntimeError(
            f"{correlation_id}:Error converting DrawIO file:{stderr.decode()}"
        )


@app.after_startup
async def configure_channels():
    logger.info("Configuring channels")
    connection: RobustConnection = await app.broker.connect()
    robust_channel: AbstractRobustChannel = await connection.channel()
    channel: AbstractChannel = await robust_channel.get_underlay_channel()
    logger.debug("Obtained channel")
    await channel.basic_qos(prefetch_count=1)


if __name__ == "__main__":
    asyncio.run(app.run())
