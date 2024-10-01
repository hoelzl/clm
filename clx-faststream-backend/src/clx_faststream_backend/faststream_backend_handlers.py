import asyncio
import base64
import logging
from pathlib import Path
from typing import Annotated

from faststream.rabbit import RabbitMessage, RabbitRouter
from pydantic import Field

from clx_common.database.db_operations import DatabaseManager
from clx_common.messaging.base_classes import (
    ImageResult,
    ImageResultOrError,
    ProcessingError,
    Result,
)
from clx_common.messaging.correlation_ids import (
    clear_correlation_ids,
    note_correlation_id_dependency,
    remove_correlation_id,
)
from clx_common.messaging.notebook_classes import NotebookResult, NotebookResultOrError
from clx_common.messaging.routing_keys import (
    IMG_RESULT_ROUTING_KEY,
    NB_RESULT_ROUTING_KEY,
)

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


database_manager: DatabaseManager | None = None


def set_database_manager(db_manager: DatabaseManager):
    global database_manager
    if database_manager is None:
        database_manager = db_manager
    else:
        raise ValueError("Trying to set database manager multiple times")


def clear_database_manager():
    global database_manager
    database_manager = None


def get_database_manager() -> DatabaseManager:
    if not database_manager:
        raise ValueError("Trying to retrieve database manager before setting it")
    return database_manager


def write_result_to_database(result: Result):
    metadata = f"Correlation ID: {result.correlation_id}"
    database_manager = get_database_manager()
    database_manager.store_result(
        result.input_file, result.content_hash, metadata, result
    )


async def write_result_data(result: Result) -> None:
    if isinstance(result, ImageResultOrError):
        await write_image_data(result)
    elif isinstance(result, NotebookResultOrError):
        await write_notebook_data(result)
    else:
        raise ValueError(f"Not a supported result: {result}")

@router.subscriber(IMG_RESULT_ROUTING_KEY)
async def handle_image(
    data: Annotated[ImageResultOrError, Field(discriminator="result_type")],
    message: RabbitMessage,
):
    try:
        await write_image_data(data)
    finally:
        logger.debug(
            f"{data.correlation_id}:img.result:Removing correlation-id:"
            f"{message.correlation_id}"
        )
        try:
            await remove_correlation_id(message.correlation_id)
        except Exception as e:
            logger.error(
                f"{data.correlation_id}:img.result:Error when removing "
                f"correlation-id:{type(e)}:{e}"
            )


async def write_image_data(data):
    if isinstance(data, ImageResult):
        logger.debug(
            f"{data.correlation_id}:img.result:received image:{data.result[:60]}")
        decoded_result = base64.b64decode(data.result)
        logger.debug(
            f"{data.correlation_id}:img.result:decoded image:{decoded_result[:60]}")
        output_file = Path(data.output_file)
        logger.debug(f"{data.correlation_id}:img.result:writing result:{output_file}")
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_bytes(decoded_result)
        write_result_to_database(data)
    else:
        logger.debug(f"{data.correlation_id}:img.result:received error:{data.error}")
        await report_handler_error(data)


@router.subscriber(NB_RESULT_ROUTING_KEY)
async def handle_notebook(
    data: Annotated[NotebookResultOrError, Field(discriminator="result_type")],
    message: RabbitMessage,
):
    try:
        await write_notebook_data(data)
    finally:
        cid = data.correlation_id
        logger.debug(f"{cid}:removing corellation ID:{message.correlation_id}")
        await remove_correlation_id(message.correlation_id)


async def write_notebook_data(data):
    cid = data.correlation_id
    if isinstance(data, NotebookResult):
        await note_correlation_id_dependency(cid, data)
        logger.debug(f"{cid}:notebook.result:received notebook:" f"{data.result[:60]}")
        output_file = Path(data.output_file)
        logger.debug(
            f"{cid}:notebook.result:writing result:" f"{output_file}: {data.result}")
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(data.result)
        write_result_to_database(data)
    else:
        await note_correlation_id_dependency(cid, data)
        logger.debug(f"{cid}:notebook.result:received error:{data.error}")
        await report_handler_error(data)
