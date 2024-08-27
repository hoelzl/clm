import asyncio
import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

cid_lock = asyncio.Lock()

correlation_ids: dict[str, Any] = {}


def clear_correlation_ids():
    correlation_ids.clear()


def new_correlation_id(**kwargs):
    correlation_id = str(uuid.uuid4())
    assert correlation_ids.get(correlation_id) is None
    correlation_ids[correlation_id] = {
        "correlation_id": correlation_id,
        **kwargs,
    }
    return correlation_id


def remove_correlation_id(correlation_id: str | None):
    if correlation_id is None:
        logger.error("Missing correlation ID.")
        return
    try:
        correlation_ids.pop(correlation_id, None)
        logger.debug(f"Removed correlation_id: {correlation_id}")
    except KeyError:
        logger.debug(f"WARNING: correlation_id {correlation_id} does not exist")
