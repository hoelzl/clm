import asyncio
import logging
import os
from base64 import b64encode
from pathlib import Path
from tempfile import TemporaryDirectory

from faststream import FastStream
from faststream.rabbit import RabbitBroker

from clx_common.messaging.base_classes import ImageResult, ImageResultOrError, ProcessingError
from clx_common.messaging.drawio_classes import (
    DrawioPayload,
)
from clx_common.messaging.routing_keys import DRAWIO_PROCESS_ROUTING_KEY, IMG_RESULT_ROUTING_KEY

# Configuration
RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost/")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG").upper()

# Set up logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s - drawio-converter - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Set up RabbitMQ broker
broker = RabbitBroker(RABBITMQ_URL)
app = FastStream(broker)


@broker.subscriber(DRAWIO_PROCESS_ROUTING_KEY)
@broker.publisher(IMG_RESULT_ROUTING_KEY)
async def process_drawio(msg: DrawioPayload) -> ImageResultOrError:
    try:
        result = await process_drawio_file(msg)
        logger.debug(f"Raw result: {len(result)} bytes")
        encoded_result = b64encode(result)
        logger.debug(f"Result: {len(result)} bytes: {encoded_result[:20]}")
        return ImageResult(result=encoded_result)
    except Exception as e:
        logger.exception(f"Error while processing DrawIO file: {e}", exc_info=e)
        return ProcessingError(error=str(e))


async def process_drawio_file(data: DrawioPayload) -> bytes:
    with TemporaryDirectory() as tmp_dir:
        input_path = Path(tmp_dir) / "input.drawio"
        output_path = Path(tmp_dir) / f"output.{data.output_format}"
        with open(input_path, "w") as f:
            f.write(data.data)
        with open(output_path, "wb") as f:
            f.write(b"")
        await convert_drawio(input_path, output_path, data.output_format)
        return output_path.read_bytes()


async def convert_drawio(input_path: Path, output_path: Path, output_format: str):
    logger.debug(f"Converting {input_path} to {output_path}")
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

    logger.debug("Creating subprocess...")
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    logger.debug("Waiting for conversion to complete...")
    stdout, stderr = await process.communicate()

    logger.debug(f"Return code: {process.returncode}")
    logger.debug(f"stdout: {stdout.decode()}")
    logger.debug(f"stderr: {stderr.decode()}")
    if process.returncode == 0:
        logger.info(f"Converted {input_path} to {output_path}")
    else:
        logger.error(f"Error converting {input_path}: {stderr.decode()}")
        raise RuntimeError(f"Error converting DrawIO file: {stderr.decode()}")


if __name__ == "__main__":
    asyncio.run(app.run())
