import asyncio
import logging
import os

from faststream import FastStream
from faststream.rabbit import RabbitBroker

from clx_common.messaging.base_classes import ProcessingError
from clx_common.messaging.notebook_classes import (
    NotebookPayload,
    NotebookResult,
    NotebookResultOrError,
)
from clx_common.messaging.routing_keys import (
    NB_PROCESS_ROUTING_KEY,
    NB_RESULT_ROUTING_KEY,
)
from .notebook_processor import NotebookProcessor
from .output_spec import create_output_spec

# Configuration
RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost/")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
LOG_CELL_PROCESSING = os.environ.get("LOG_CELL_PROCESSING", "False") == "True"

# Logging setup
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s - notebook-processor - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Set up RabbitMQ broker
broker = RabbitBroker(RABBITMQ_URL)
app = FastStream(broker)


@broker.subscriber(NB_PROCESS_ROUTING_KEY)
@broker.publisher(NB_RESULT_ROUTING_KEY)
async def process_notebook(payload: NotebookPayload) -> NotebookResultOrError:
    try:
        logger.debug(f"Processing notebook payload for '{payload.notebook_path}'")
        output_spec = create_output_spec(
            kind=payload.kind,
            prog_lang=payload.prog_lang,
            language=payload.language,
            format=payload.format,
        )
        logger.debug("Output spec created")
        processor = NotebookProcessor(output_spec)
        processed_notebook = await processor.process_notebook(payload)
        logger.debug(f"Processed notebook: {processed_notebook[:60]}")
        return NotebookResult(
            result=processed_notebook, output_file=payload.notebook_path
        )
    except Exception as e:
        logger.exception(f"Error while processing notebook: {e}", exc_info=e)
        return ProcessingError(error=str(e))


if __name__ == "__main__":
    asyncio.run(app.run())
