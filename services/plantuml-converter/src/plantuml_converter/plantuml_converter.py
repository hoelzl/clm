import asyncio
import logging
import os
import re
import traceback
from base64 import b64encode
from pathlib import Path
from tempfile import TemporaryDirectory

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
from clx_common.messaging.plantuml_classes import (
    PlantUmlPayload,
)
from clx_common.messaging.routing_keys import (
    IMG_RESULT_ROUTING_KEY,
    PLANTUML_PROCESS_ROUTING_KEY,
)
from clx_common.services.subprocess_tools import run_subprocess

# Configuration
RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost/")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG").upper()

PLANTUML_NAME_REGEX = re.compile(r'@startuml[ \t]+(?:"([^"]+)"|(\S+))')

# Set up logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    # format="%(asctime)s - %(levelname)s - plantuml-converter - %(message)s",
)
logger = logging.getLogger(__name__)

# Set up RabbitMQ broker
broker = RabbitBroker(RABBITMQ_URL)
app = FastStream(broker)


def get_plantuml_output_name(content, default="plantuml"):
    match = PLANTUML_NAME_REGEX.search(content)
    if match:
        name = match.group(1) or match.group(2)
        # Output name most likely commented out
        # This is not entirely accurate, but good enough for our purposes
        if "'" in name:
            return default
        return name
    return default


@broker.subscriber(PLANTUML_PROCESS_ROUTING_KEY)
@broker.publisher(IMG_RESULT_ROUTING_KEY)
async def process_plantuml(payload: PlantUmlPayload) -> ImageResultOrError:
    cid = payload.correlation_id
    try:
        result = await process_plantuml_file(payload)
        logger.debug(f"{cid}:Raw result: {len(result)} bytes")
        encoded_result = b64encode(result)
        logger.debug(f"{cid}:Result: {len(result)} bytes: {encoded_result[:20]}")
        return ImageResult(
            result=encoded_result,
            correlation_id=cid,
            output_file=payload.output_file,
            input_file=payload.input_file,
            content_hash=payload.content_hash(),
        )
    except Exception as e:
        logger.error(
            f"{cid}:Error while processing PlantUML file '{payload.input_file}': {e}"
        )
        logger.debug(f"{cid}:Error traceback", exc_info=e)
        return ProcessingError(
            error=str(e),
            correlation_id=cid,
            input_file=payload.input_file,
            input_file_name=payload.input_file_name,
            output_file=payload.output_file,
            traceback=traceback.format_exc(),
        )


async def process_plantuml_file(data: PlantUmlPayload) -> bytes:
    cid = data.correlation_id
    logger.debug(f"{cid}:Processing PlantUML file: {data}")
    with TemporaryDirectory() as tmp_dir:
        input_path = Path(tmp_dir) / "plantuml.pu"
        output_name = get_plantuml_output_name(data.data, default="plantuml")
        output_path = (Path(tmp_dir) / output_name).with_suffix(
            f".{data.output_format}"
        )
        logger.debug(f"{cid}:Input path: {input_path}, output path: {output_path}")
        input_path.write_text(data.data, encoding="utf-8")
        await convert_plantuml(input_path, cid)
        for file in output_path.parent.iterdir():
            logger.debug(f"{cid}:Found file: {file}")
        return output_path.read_bytes()


async def convert_plantuml(input_file: Path, correlation_id: str):
    logger.debug(f"{correlation_id}:Converting PlantUML file: {input_file}")
    cmd = [
        "java",
        "-DPLANTUML_LIMIT_SIZE=8192",
        "-jar",
        "/app/plantuml.jar",
        "-tpng",
        "-Sdpi=200",
        "-o",
        str(input_file.parent),
        str(input_file),
    ]

    logger.debug(f"{correlation_id}:Creating subprocess...")
    process, stdout, stderr = await run_subprocess(cmd, correlation_id)

    logger.debug(f"{correlation_id}:Return code: {process.returncode}")
    logger.debug(f"{correlation_id}:stdout:{stdout.decode()}")
    logger.debug(f"{correlation_id}:stderr:{stderr.decode()}")

    if process.returncode == 0:
        logger.info(f"{correlation_id}:Converted {input_file}")
    else:
        logger.error(
            f"{correlation_id}:Error converting {input_file}: {stderr.decode()}"
        )
        raise RuntimeError(
            f"{correlation_id}:Error converting PlantUML file: {stderr.decode()}"
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
