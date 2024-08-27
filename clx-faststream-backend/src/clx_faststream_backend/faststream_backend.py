import asyncio
import base64
import logging
from asyncio import CancelledError, Task
from typing import Annotated

from attrs import define, field
from faststream import FastStream
from faststream.rabbit import RabbitBroker, RabbitMessage, RabbitRouter
from faststream.rabbit.publisher.asyncapi import AsyncAPIPublisher
from pydantic import Field

from clx_common.backends.local_ops_backend import LocalOpsBackend
from clx_common.messaging.base_classes import ImageResult, ImageResultOrError, Payload
from clx_common.messaging.notebook_classes import NotebookResult, NotebookResultOrError
from clx_common.messaging.routing_keys import (
    DRAWIO_PROCESS_ROUTING_KEY,
    IMG_RESULT_ROUTING_KEY,
    NB_PROCESS_ROUTING_KEY,
    NB_RESULT_ROUTING_KEY,
    PLANTUML_PROCESS_ROUTING_KEY,
)
from clx_common.operation import Operation
from clx_faststream_backend.correlation_ids import (
    correlation_ids,
    new_correlation_id,
    remove_correlation_id,
)

NUM_SEND_RETRIES = 5

logger = logging.getLogger(__name__)

router = RabbitRouter()
handler_errors = []


def clear_handler_errors():
    handler_errors.clear()


@router.subscriber(IMG_RESULT_ROUTING_KEY)
async def handle_image(
    data: Annotated[ImageResultOrError, Field(discriminator="result_type")],
    message: RabbitMessage,
):
    try:
        if isinstance(data, ImageResult):
            logger.debug(f"img.result:received image:{data.result[:60]}")
            decoded_result = base64.b64decode(data.result)
            logger.debug(f"img.result:decoded image:{decoded_result[:60]}")
            logger.debug(f"img.result:writing result:{data.output_file}")
            data.output_file.parent.mkdir(parents=True, exist_ok=True)
            data.output_file.write_bytes(decoded_result)
        else:
            logger.debug(f"img.result:received error:{data.error}")
            handler_errors.append(data.error)
    finally:
        logger.debug(f"img.result:removing correlation-id:{message.correlation_id}")
        remove_correlation_id(message.correlation_id)


@router.subscriber(NB_RESULT_ROUTING_KEY)
async def handle_notebook(
    data: Annotated[NotebookResultOrError, Field(discriminator="result_type")],
    message: RabbitMessage,
):
    try:
        if isinstance(data, NotebookResult):
            logger.debug(f"notebook.result:received notebook: {data.result[:60]}")
            logger.debug(f"notebook.result:writing result:{data.output_file}")
            data.output_file.write_text(data.result)
        else:
            logger.debug(f"notebook.result:received error: {data.error}")
            handler_errors.append(data.error)
    finally:
        logger.debug(f"  Correlation-id:  {message.correlation_id}")
        remove_correlation_id(message.correlation_id)


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
        correlation_ids.clear()
        self.task = asyncio.create_task(self.app.run())
        await self.broker.start()

    async def execute_operation(self, operation: "Operation", payload: Payload) -> None:
        service_name = operation.service_name
        if service_name is None:
            raise ValueError("Cannot execute operation without service name")
        await self.send_message(service_name, payload)

    async def send_message(self, service_name: str, payload: Payload):
        service = self.services.get(service_name)
        if service is None:
            raise ValueError(f"Unknown service name: {service_name}")
        correlation_id = new_correlation_id(service_name=service_name, payload=payload)
        for i in range(NUM_SEND_RETRIES):
            try:
                if i == 0:
                    logger.debug(
                        f"FastStreamBackend: Publishing {payload.data[:60]} "
                        f"with correlation_id: {correlation_id}"
                    )
                else:
                    logger.debug(
                        f"REPUBLISHING TRY {i}: Publishing {payload.data[:60]} "
                        f"with correlation_id: {correlation_id}"
                    )
                await service.publish(payload, correlation_id=correlation_id)
                break
            except CancelledError:
                await asyncio.sleep(1 + i)
                continue
            except Exception as e:
                logger.debug(f"ERROR in send_message(): {e}")

    async def wait_for_completion(self):
        for i in range(self.max_wait_for_completion_duration):
            if len(correlation_ids) == 0:
                break
            else:
                if i % 20 == 0:
                    logger.debug("INFO: Waiting for tasks to finish")
                    logger.debug(
                        f"{len(correlation_ids)} correlation_id(s) outstanding"
                    )
                await asyncio.sleep(i)
        if len(correlation_ids) != 0:
            logger.debug("ERROR: Correlation_ids not empty")
            logger.debug("  Correlation-ids:", correlation_ids)

    async def shutdown(self):
        await self.wait_for_completion()
        self.app.should_exit = True
        logger.debug("Exiting backend")
        await self.task
        logger.debug("Exited backend")
