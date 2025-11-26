import logging

# Execution stages for controlling processing order.
#
# Stage 1 (FIRST_EXECUTION_STAGE):
#   - DrawIO/PlantUML conversions (generate PNG files in source img/ folder)
#   - Non-HTML notebook operations (notebook, code formats)
#   - Simple file copy operations (DataFile)
#   - Copy existing image files (SharedImageFile for pre-existing images)
#
# Stage 2 (COPY_GENERATED_IMAGES_STAGE):
#   - Copy generated images to shared output folder
#   - This runs AFTER conversions so the PNG files exist
#   - SharedImageFile uses this stage when source doesn't exist at load time
#
# Stage 3 (HTML_SPEAKER_STAGE):
#   - Speaker HTML runs first, caching executed notebooks
#
# Stage 4 (HTML_COMPLETED_STAGE):
#   - Completed HTML runs second, reusing cached executed notebooks
#
FIRST_EXECUTION_STAGE = 1
COPY_GENERATED_IMAGES_STAGE = 2
HTML_SPEAKER_STAGE = 3
HTML_COMPLETED_STAGE = 4
LAST_EXECUTION_STAGE = 4
NUM_EXECUTION_STAGES = LAST_EXECUTION_STAGE - FIRST_EXECUTION_STAGE + 1

logger = logging.getLogger(__name__)


def execution_stages() -> list[int]:
    return list(range(FIRST_EXECUTION_STAGE, LAST_EXECUTION_STAGE + 1))
