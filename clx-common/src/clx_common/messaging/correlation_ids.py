import asyncio
import logging
import uuid
from pathlib import Path
from time import time

from attrs import define, field

from clx_common.messaging.notebook_classes import NotebookPayload, NotebookResult

logger = logging.getLogger(__name__)


def format_dependency(dep):
    output_file = Path(dep.output_file)
    if isinstance(dep, NotebookResult):
        return f"NR({output_file.name})"
    elif isinstance(dep, NotebookPayload):
        return f"NP({output_file.name}: {dep.kind}, {dep.prog_lang})"
    return type(dep).__name__


@define
class CorrelationData:
    correlation_id: str
    task: asyncio.Task | None = None
    start_time: float = field(factory=time)
    dependencies: list = field(factory=list)

    def format_dependencies(self):
        return ", ".join(format_dependency(dep) for dep in self.dependencies)


cid_lock = asyncio.Lock()
active_correlation_ids: dict[str, CorrelationData] = {}
all_correlation_ids: dict[str, CorrelationData] = {}


async def clear_correlation_ids():
    async with cid_lock:
        active_correlation_ids.clear()
        all_correlation_ids.clear()


async def new_correlation_id(task=None):
    if task is None:
        loop = asyncio.get_event_loop()
        task = asyncio.current_task(loop)
    correlation_id = str(uuid.uuid4())
    data = CorrelationData(correlation_id=correlation_id, task=task)
    async with cid_lock:
        active_correlation_ids[correlation_id] = data
        all_correlation_ids[correlation_id] = data
    return correlation_id


async def note_correlation_id_dependency(correlation_id, dependency):
    data = all_correlation_ids.get(correlation_id)
    if data is None:
        logger.error(
            f"{correlation_id}: Trying to register dependency on "
            f"non-existent correlation_id. Skipping"
        )
        return
    if active_correlation_ids.get(correlation_id) is None:
        logger.warning(
            f"{correlation_id}: Registering dependency on inactive " f"correlation ID"
        )
    async with cid_lock:
        if dependency not in data.dependencies:
            data.dependencies.append(dependency)


async def remove_correlation_id(
    correlation_id: str | None, lock_correlation_ids: bool = True
):
    if correlation_id is None:
        logger.error("Missing correlation ID.")
        return
    try:
        if lock_correlation_ids:
            async with cid_lock:
                active_correlation_ids.pop(correlation_id)
        else:
            active_correlation_ids.pop(correlation_id)
        logger.debug(f"Removed correlation_id: {correlation_id}")
    except KeyError:
        logger.debug(f"WARNING: correlation_id {correlation_id} does not exist")


async def remove_stale_correlation_ids(max_lifetime=1200.0):
    try:
        current_time = time()
        cids_to_remove = set()
        for cid, data in active_correlation_ids.items():
            if current_time - data.start_time > max_lifetime:
                logger.debug(f"{cid}: marking stale correlation id for removal")
                cids_to_remove.add(cid)
        async with cid_lock:
            for cid in cids_to_remove:
                logger.warning(f"{cid}: Removing stale correlation id")
                active_correlation_ids.pop(cid)
    except Exception as e:
        logger.error(f"Error while removing stale correlation ids: {e}")
