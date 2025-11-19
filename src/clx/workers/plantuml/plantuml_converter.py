import logging
import os
import re
from pathlib import Path

from clx.infrastructure.services.subprocess_tools import run_subprocess

# Configuration
LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG").upper()

# PlantUML JAR path - configurable via environment variable
# Default order: PLANTUML_JAR env var -> /app/plantuml.jar (Docker) -> local repo jar
_default_jar_paths = [
    "/app/plantuml.jar",  # Docker container path
    str(Path(__file__).parents[3] / "plantuml-1.2024.6.jar"),  # Local repo path
]
_plantuml_jar_from_env = os.environ.get("PLANTUML_JAR")
if _plantuml_jar_from_env:
    PLANTUML_JAR = _plantuml_jar_from_env
    if not Path(PLANTUML_JAR).exists():
        raise FileNotFoundError(
            f"PlantUML JAR not found at path specified in PLANTUML_JAR environment variable: {PLANTUML_JAR}"
        )
else:
    # Try default paths in order
    PLANTUML_JAR = next((p for p in _default_jar_paths if Path(p).exists()), None)
    if PLANTUML_JAR is None:
        raise FileNotFoundError(
            f"PlantUML JAR not found. Please install PlantUML and set the PLANTUML_JAR environment variable.\n"
            f"Searched paths: {_default_jar_paths}"
        )

PLANTUML_NAME_REGEX = re.compile(r'@startuml[ \t]+(?:"([^"]+)"|(\S+))')

# Set up logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
)
logger = logging.getLogger(__name__)

# Log the PlantUML JAR path being used
logger.info(f"Using PlantUML JAR: {PLANTUML_JAR}")


def get_plantuml_output_name(content, default="plantuml"):
    """Extract output name from PlantUML content.

    Args:
        content: PlantUML file content
        default: Default name if not found in content

    Returns:
        Output file name (without extension)
    """
    match = PLANTUML_NAME_REGEX.search(content)
    if match:
        name = match.group(1) or match.group(2)
        # Output name most likely commented out
        # This is not entirely accurate, but good enough for our purposes
        if "'" in name:
            return default
        return name
    return default


async def convert_plantuml(input_file: Path, correlation_id: str):
    """Convert a PlantUML file to PNG format.

    Args:
        input_file: Path to input PlantUML file
        correlation_id: Correlation ID for logging

    Raises:
        RuntimeError: If conversion fails
    """
    logger.debug(f"{correlation_id}:Converting PlantUML file: {input_file}")
    cmd = [
        "java",
        "-DPLANTUML_LIMIT_SIZE=8192",
        "-jar",
        PLANTUML_JAR,
        "-tpng",
        "-Sdpi=200",
        "-o",
        str(input_file.parent),
        str(input_file),
    ]

    logger.debug(f"{correlation_id}:Creating subprocess...")
    process, stdout, stderr = await run_subprocess(cmd, correlation_id)

    logger.debug(f"{correlation_id}:Return code: {process.returncode}")
    logger.debug(f"{correlation_id}:stdout:{stdout.decode()}")
    logger.debug(f"{correlation_id}:stderr:{stderr.decode()}")

    if process.returncode == 0:
        logger.info(f"{correlation_id}:Converted {input_file}")
    else:
        logger.error(f"{correlation_id}:Error converting {input_file}: {stderr.decode()}")
        raise RuntimeError(f"{correlation_id}:Error converting PlantUML file: {stderr.decode()}")
