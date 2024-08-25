import asyncio
import logging
import os

from faststream import FastStream
from faststream.rabbit import RabbitBroker

from clx_common.notebook_classes import NotebookError, NotebookPayload, NotebookResult, \
    NotebookResultOrError
from clx_common.routing_keys import NB_PROCESS_ROUTING_KEY, NB_RESULT_ROUTING_KEY
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
async def process_notebook(msg: NotebookPayload) -> NotebookResultOrError:
    try:
        logger.debug(f"Processing notebook payload for '{msg.reply_routing_key}'")
        output_spec = create_output_spec(
            output_type=msg.output_type,
            prog_lang=msg.prog_lang,
            lang=msg.language,
            notebook_format=msg.notebook_format,
        )
        logger.debug("Output spec created")
        processor = NotebookProcessor(output_spec)
        processed_notebook = await processor.process_notebook(msg)
        logger.debug(f"Processed notebook: {processed_notebook[:60]}")
        return NotebookResult(result=processed_notebook)
    except Exception as e:
        logger.exception(f"Error while processing notebook: {e}", exc_info=e)
        return NotebookError(error=str(e))

if __name__ == "__main__":
    asyncio.run(app.run())
