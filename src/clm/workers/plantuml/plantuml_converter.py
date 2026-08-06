import logging
import os
import re
from pathlib import Path

from clm.infrastructure.services.subprocess_tools import run_subprocess

# Configuration
LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG").upper()

# PlantUML JAR path - configurable via environment variable. Resolution
# lives in clm.infrastructure.utils.diagram_tools (issue #747: the host-side cache-key
# identity must fingerprint the SAME binary this worker runs).
from clm.infrastructure.utils.diagram_tools import PLANTUML_DEFAULT_JAR_PATHS, locate_plantuml_jar

_located_jar = locate_plantuml_jar()
if _located_jar is None:
    raise FileNotFoundError(
        f"PlantUML JAR not found. Please install PlantUML and set the PLANTUML_JAR environment variable.\n"
        f"Searched paths: {PLANTUML_DEFAULT_JAR_PATHS}"
    )
if not Path(_located_jar).exists():
    raise FileNotFoundError(
        f"PlantUML JAR not found at path specified in PLANTUML_JAR environment variable: {_located_jar}"
    )
PLANTUML_JAR = _located_jar

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


async def convert_plantuml(input_file: Path, correlation_id: str, output_format: str = "png"):
    """Convert a PlantUML file to the specified output format.

    Args:
        input_file: Path to input PlantUML file
        correlation_id: Correlation ID for logging
        output_format: Output format ("png" or "svg")

    Raises:
        RuntimeError: If conversion fails
    """
    logger.debug(f"{correlation_id}:Converting PlantUML file: {input_file} to {output_format}")
    cmd = [
        "java",
        "-DPLANTUML_LIMIT_SIZE=8192",
        "-jar",
        PLANTUML_JAR,
        f"-t{output_format}",
    ]

    # DPI setting is only meaningful for raster formats
    if output_format == "png":
        cmd.append("-Sdpi=200")

    cmd.extend(
        [
            "-o",
            str(input_file.parent),
            str(input_file),
        ]
    )

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
