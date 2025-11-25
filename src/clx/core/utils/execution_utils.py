import logging

# Execution stages for controlling processing order.
# Non-HTML operations (notebook, code) and simple file operations run in stage 1.
# HTML generation is split into two stages to enable caching:
# - Speaker HTML runs first (stage 2), caching executed notebooks
# - Completed HTML runs second (stage 3), reusing cached executed notebooks
FIRST_EXECUTION_STAGE = 1
HTML_SPEAKER_STAGE = 2
HTML_COMPLETED_STAGE = 3
LAST_EXECUTION_STAGE = 3
NUM_EXECUTION_STAGES = LAST_EXECUTION_STAGE - FIRST_EXECUTION_STAGE + 1

logger = logging.getLogger(__name__)


def execution_stages() -> list[int]:
    return list(range(FIRST_EXECUTION_STAGE, LAST_EXECUTION_STAGE + 1))
