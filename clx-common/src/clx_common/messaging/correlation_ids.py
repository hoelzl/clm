import asyncio
import logging
import uuid

logger = logging.getLogger(__name__)

cid_lock = asyncio.Lock()
active_correlation_ids: set[str] = set()
all_correlation_ids: dict[str, list] = {}


async def clear_correlation_ids():
    async with cid_lock:
        active_correlation_ids.clear()
        all_correlation_ids.clear()


async def new_correlation_id():
    correlation_id = str(uuid.uuid4())
    async with cid_lock:
        active_correlation_ids.add(correlation_id)
        all_correlation_ids[correlation_id] = []
    return correlation_id


async def note_correlation_id_dependency(correlation_id, dependency):
    dependencies = all_correlation_ids.get(correlation_id)
    if dependencies is None:
        logger.error(
            f"{correlation_id}: Trying to register dependency on "
            f"non-existent correlation_id. Skipping"
        )
        return
    if correlation_id not in active_correlation_ids:
        logger.warning(
            f"{correlation_id}: Registering dependency on inactive " f"correlation ID"
        )
    async with cid_lock:
        if dependency not in dependencies:
            all_correlation_ids[correlation_id].append(dependency)


async def remove_correlation_id(correlation_id: str | None):
    if correlation_id is None:
        logger.error("Missing correlation ID.")
        return
    try:
        async with cid_lock:
            active_correlation_ids.remove(correlation_id)
        logger.debug(f"Removed correlation_id: {correlation_id}")
    except KeyError:
        logger.debug(f"WARNING: correlation_id {correlation_id} does not exist")
