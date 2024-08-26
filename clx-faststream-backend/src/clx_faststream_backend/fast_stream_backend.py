import asyncio
import logging
import uuid
from asyncio import CancelledError, Task
from typing import Annotated

from attrs import define, field
from faststream import FastStream
from faststream.rabbit import RabbitBroker, RabbitMessage
from faststream.rabbit.publisher.asyncapi import AsyncAPIPublisher
from pydantic import Field

from clx_common.backend import Backend
from clx_common.base_classes import ImageResult, ImageResultOrError, Payload
from clx_common.notebook_classes import NotebookResult, NotebookResultOrError
from clx_common.operation import Operation
from clx_common.routing_keys import (
    DRAWIO_PROCESS_ROUTING_KEY,
    NB_PROCESS_ROUTING_KEY,
    PLANTUML_PROCESS_ROUTING_KEY,
)

NUM_SEND_RETRIES = 5

logger = logging.getLogger(__name__)

# TODO: How do we make the broker configurable?
broker: RabbitBroker = RabbitBroker("amqp://guest:guest@localhost:5672/")

services: dict[str, AsyncAPIPublisher] = {
    "notebook-processor": broker.publisher(NB_PROCESS_ROUTING_KEY),
    "drawio-converter": broker.publisher(DRAWIO_PROCESS_ROUTING_KEY),
    "plantuml-converter": broker.publisher(PLANTUML_PROCESS_ROUTING_KEY),
}

correlation_ids: set[str] = set()


def new_correlation_id():
    correlation_id = str(uuid.uuid4())
    correlation_ids.add(correlation_id)
    return correlation_id


def remove_correlation_id(correlation_id: str | None):
    if correlation_id is None:
        logger.error("Missing correlation ID.")
        return
    try:
        correlation_ids.remove(correlation_id)
        logger.debug(f"Removed correlation_id: {correlation_id}")
    except KeyError:
        logger.debug(f"WARNING: correlation_id {correlation_id} does not exist")


@broker.subscriber("img.result")
async def handle_image(
    data: Annotated[ImageResultOrError, Field(discriminator="result_type")],
    message: RabbitMessage,
):
    if isinstance(data, ImageResult):
        logger.debug(f"img.result:received image: {data.result}")
    else:
        logger.debug(f"img.result:received error: {data.error}")
    logger.debug(f"  Correlation-id: {message.correlation_id}")
    remove_correlation_id(message.correlation_id)


@broker.subscriber("notebook.result")
async def handle_notebook(
    data: Annotated[NotebookResultOrError, Field(discriminator="result_type")],
    message: RabbitMessage,
):
    if isinstance(data, NotebookResult):
        logger.debug(f"notebook.result:received notebook: {data.result}")
    else:
        logger.debug(f"notebook.result:received error: {data.error}")
    logger.debug(f"  Correlation-id:  {message.correlation_id}")
    remove_correlation_id(message.correlation_id)


@define
class FastStreamBackend(Backend):
    app: FastStream = field(factory=lambda: FastStream(broker))

    # Maximal number of seconds we wait for all processes to complete
    # Set to a relatively high value, since courses training ML notebooks
    # may run a long time.
    max_wait_for_completion_duration: int = 1200

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
        await broker.start()

    async def execute_operation(self, operation: "Operation", payload: Payload) -> None:
        service_name = operation.service_name
        if service_name is None:
            raise ValueError("Cannot execute operation without service name")
        await self.send_message(service_name, payload)

    @staticmethod
    async def send_message(service_name: str, payload: Payload):
        service = services.get(service_name)
        if service is None:
            raise ValueError(f"Unknown service name: {service_name}")
        correlation_id = new_correlation_id()
        for i in range(NUM_SEND_RETRIES):
            try:
                if i == 0:
                    logger.debug(
                        f"FastStreamBackend: Publishing {payload.data} with correlation_id: "
                        f"{correlation_id}"
                    )
                else:
                    logger.debug(
                        f"REPUBLISHING TRY {i}: Publishing {payload.data} with "
                        f"correlation_id: {correlation_id}"
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
                    logger.debug(f"{len(correlation_ids)} correlation_id(s) outstanding")
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
