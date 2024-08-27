import asyncio
import base64
import logging
import time
from asyncio import CancelledError, Task
from typing import Annotated

from attrs import define, field
from faststream import FastStream
from faststream.rabbit import RabbitBroker, RabbitMessage, RabbitRouter
from faststream.rabbit.publisher.asyncapi import AsyncAPIPublisher
from pydantic import Field

from clx_common.backends.local_ops_backend import LocalOpsBackend
from clx_common.messaging.base_classes import (
    ImageResult,
    ImageResultOrError,
    Payload,
    ProcessingError,
)
from clx_common.messaging.correlation_ids import (active_correlation_ids,
                                                  note_correlation_id_dependency,
                                                  remove_correlation_id, )
from clx_common.messaging.notebook_classes import NotebookResult, NotebookResultOrError
from clx_common.messaging.routing_keys import (
    DRAWIO_PROCESS_ROUTING_KEY,
    IMG_RESULT_ROUTING_KEY,
    NB_PROCESS_ROUTING_KEY,
    NB_RESULT_ROUTING_KEY,
    PLANTUML_PROCESS_ROUTING_KEY,
)
from clx_common.operation import Operation

NUM_SEND_RETRIES = 5

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
            logger.debug(
                f"{data.correlation_id}:img.result:writing result:{data.output_file}"
            )
            data.output_file.parent.mkdir(parents=True, exist_ok=True)
            data.output_file.write_bytes(decoded_result)
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
        await remove_correlation_id(message.correlation_id)


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
                f"{cid}:notebook.result:received notebook:"
                f"{data.result[:60]}"
            )
            logger.debug(
                f"{cid}:notebook.result:writing result:"
                f"{data.output_file}: {data.result}"
            )
            data.output_file.parent.mkdir(parents=True, exist_ok=True)
            data.output_file.write_text(data.result)
        else:
            await note_correlation_id_dependency(cid, data)
            logger.debug(
                f"{cid}:notebook.result:received error:{data.error}"
            )
            await report_handler_error(data)
    finally:
        logger.debug(
            f"{cid}:removing corellation ID:{message.correlation_id}"
        )
        await remove_correlation_id(message.correlation_id)


@define
class FastStreamBackend(LocalOpsBackend):
    url: str = "amqp://guest:guest@localhost:5672/"
    broker: RabbitBroker = field(init=False)
    app: FastStream = field(init=False)
    services: dict[str, AsyncAPIPublisher] = field(init=False)

    # Maximal number of seconds we wait for all processes to complete
    # Set to a relatively high value, since courses training ML notebooks
    # may run a long time.
    max_wait_for_completion_duration: int = 1200

    def __attrs_post_init__(self):
        self.broker = RabbitBroker(self.url)
        self.broker.include_router(router)
        self.app = FastStream(self.broker)
        self.services = {
            "notebook-processor": self.broker.publisher(NB_PROCESS_ROUTING_KEY),
            "drawio-converter": self.broker.publisher(DRAWIO_PROCESS_ROUTING_KEY),
            "plantuml-converter": self.broker.publisher(PLANTUML_PROCESS_ROUTING_KEY),
        }

    task: Task | None = None

    async def __aenter__(self) -> "FastStreamBackend":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.shutdown()
        return None

    async def start(self):
        self.task = asyncio.create_task(self.app.run())
        await self.broker.start()

    async def execute_operation(self, operation: "Operation", payload: Payload) -> None:
        service_name = operation.service_name
        if service_name is None:
            raise ValueError(
                f"{payload.correlation_id}:executing operation without service name"
            )
        await self.send_message(service_name, payload)

    async def send_message(self, service_name: str, payload: Payload):
        service = self.services.get(service_name)
        if service is None:
            raise ValueError(
                f"{payload.correlation_id}:unknown service name:{service_name}"
            )
        correlation_id = payload.correlation_id
        for i in range(NUM_SEND_RETRIES):
            try:
                if i == 0:
                    logger.debug(
                        f"{correlation_id}:FastStreamBackend:publishing "
                        f"{payload.data[:60]}"
                    )
                else:
                    logger.debug(
                        f"{correlation_id}:republishing try {i}:{payload.data[:60]}"
                    )
                await service.publish(payload, correlation_id=correlation_id)
                break
            except CancelledError:
                await asyncio.sleep(1 + i)
                continue
            except Exception as e:
                logger.error(f"{correlation_id}:send_message() failed: {e}")

    async def wait_for_completion(self, max_wait_time: float | None = None) -> bool:
        if max_wait_time is None:
            max_wait_time = self.max_wait_for_completion_duration
        start_time = time.time()
        while True:
            if len(active_correlation_ids) == 0:
                break
            if time.time() - start_time > max_wait_time:
                logger.info("Timed out while waiting for tasks to finish")
                break
            else:
                await asyncio.sleep(1.0)
                logger.debug("Waiting for tasks to finish")
                logger.debug(
                    f"{len(active_correlation_ids)} correlation_id(s) outstanding"
                )
        if len(active_correlation_ids) != 0:
            logger.debug("ERROR: Correlation_ids not empty")
            logger.debug("  Correlation-ids:", active_correlation_ids)
            return False
        return True

    async def shutdown(self):
        await self.wait_for_completion()
        self.app.exit()
        await self.task
        logger.debug("Exited backend")
