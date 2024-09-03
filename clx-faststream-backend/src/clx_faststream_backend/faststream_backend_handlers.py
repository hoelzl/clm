import asyncio
import base64
import logging
from pathlib import Path
from typing import Annotated

from faststream.rabbit import RabbitMessage, RabbitRouter
from pydantic import Field

from clx_common.messaging.base_classes import ImageResult, ImageResultOrError, \
    ProcessingError
from clx_common.messaging.correlation_ids import active_correlation_ids, \
    note_correlation_id_dependency, remove_correlation_id
from clx_common.messaging.notebook_classes import NotebookResult, NotebookResultOrError
from clx_common.messaging.routing_keys import IMG_RESULT_ROUTING_KEY, \
    NB_RESULT_ROUTING_KEY

logger = logging.getLogger(__name__)

router = RabbitRouter()

handler_error_lock = asyncio.Lock()
handler_errors: list[ProcessingError] = []


async def clear_handler_errors():
    async with handler_error_lock:
        handler_errors.clear()


async def report_handler_error(error: ProcessingError):
    logger.info(f"{error.correlation_id}: Reporting handler error! {error.output_file}")
    async with handler_error_lock:
        handler_errors.append(error)


@router.subscriber(IMG_RESULT_ROUTING_KEY)
async def handle_image(
    data: Annotated[ImageResultOrError, Field(discriminator="result_type")],
    message: RabbitMessage,
):
    try:
        if isinstance(data, ImageResult):
            logger.debug(
                f"{data.correlation_id}:img.result:received image:{data.result[:60]}"
            )
            decoded_result = base64.b64decode(data.result)
            logger.debug(
                f"{data.correlation_id}:img.result:decoded image:{decoded_result[:60]}"
            )
            output_file = Path(data.output_file)
            logger.debug(
                f"{data.correlation_id}:img.result:writing result:{output_file}"
            )
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_bytes(decoded_result)
        else:
            logger.debug(
                f"{data.correlation_id}:img.result:received error:{data.error}"
            )
            await report_handler_error(data)
    finally:
        logger.debug(
            f"{data.correlation_id}:img.result:removing correlation-id:"
            f"{message.correlation_id}"
        )
        try:
            await remove_correlation_id(message.correlation_id, force=True)
        except Exception as e:
            logger.error(f"{data.correlation_id}:img.result:error when removing "
                         f"correlation-id:{e}")



@router.subscriber(NB_RESULT_ROUTING_KEY)
async def handle_notebook(
    data: Annotated[NotebookResultOrError, Field(discriminator="result_type")],
    message: RabbitMessage,
):
    cid = data.correlation_id
    try:
        if isinstance(data, NotebookResult):
            await note_correlation_id_dependency(cid, data)
            logger.debug(
                f"{cid}:notebook.result:received notebook:" f"{data.result[:60]}"
            )
            output_file = Path(data.output_file)
            logger.debug(
                f"{cid}:notebook.result:writing result:" f"{output_file}: {data.result}"
            )
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(data.result)
        else:
            await note_correlation_id_dependency(cid, data)
            logger.debug(f"{cid}:notebook.result:received error:{data.error}")
            await report_handler_error(data)
    finally:
        logger.debug(f"{cid}:removing corellation ID:{message.correlation_id}")
        await remove_correlation_id(message.correlation_id)
