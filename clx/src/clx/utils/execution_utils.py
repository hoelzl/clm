import logging
from typing import TYPE_CHECKING

FIRST_EXECUTION_STAGE = 1
LAST_EXECUTION_STAGE = 2
NUM_EXECUTION_STAGES = LAST_EXECUTION_STAGE - FIRST_EXECUTION_STAGE + 1

logger = logging.getLogger(__name__)


def execution_stages() -> list[int]:
    return list(range(FIRST_EXECUTION_STAGE, LAST_EXECUTION_STAGE + 1))
