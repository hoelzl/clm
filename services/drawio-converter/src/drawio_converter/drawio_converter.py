import asyncio
import logging
import os
from base64 import b64encode
from pathlib import Path
from tempfile import TemporaryDirectory

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
        result = None
        for i in range(NUM_RETRIES):
            result = await process_drawio_file(payload)
            logger.debug(f"{cid}:Raw result:iteration {i}:{len(result)} bytes")
            if len(result) > 0:
                continue
        if result is None or len(result) == 0:
            raise ValueError(f"Empty result for {cid}")
        encoded_result = b64encode(result)
        logger.debug(f"{cid}:Result: {len(result)} bytes: {encoded_result[:20]}")
        correlation_id = payload.correlation_id
        return ImageResult(
            result=encoded_result,
            correlation_id=correlation_id,
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
        with open(input_path, "w") as f:
            f.write(payload.data)
        with open(output_path, "wb") as f:
            f.write(b"")
        await convert_drawio(
            input_path, output_path, payload.output_format, payload.correlation_id
        )
        return output_path.read_bytes()


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


if __name__ == "__main__":
    asyncio.run(app.run())
