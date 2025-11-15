import logging
import os
from pathlib import Path

from clx.infrastructure.services.subprocess_tools import run_subprocess

# Configuration
LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG").upper()
DRAWIO_EXECUTABLE = os.environ.get("DRAWIO_EXECUTABLE", "drawio")

# Set up logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
)
logger = logging.getLogger(__name__)


async def convert_drawio(
    input_path: Path, output_path: Path, output_format: str, correlation_id
):
    """Convert a DrawIO file to the specified output format.

    Args:
        input_path: Path to input DrawIO file
        output_path: Path where output file should be written
        output_format: Output format (png, svg, pdf, etc.)
        correlation_id: Correlation ID for logging

    Raises:
        RuntimeError: If conversion fails
    """
    logger.debug(f"{correlation_id}:Converting {input_path} to {output_path}")
    # Base command
    cmd = [
        DRAWIO_EXECUTABLE,
        "--no-sandbox",
        "--export",
        input_path.as_posix(),
        "--format",
        output_format,
        "--output",
        output_path.as_posix(),
        "--border",
        "20",
    ]

    # Format-specific options
    if output_format == "png":
        cmd.extend(["--scale", "3"])  # Increase resolution (roughly 300 DPI)
    elif output_format == "svg":
        cmd.append("--embed-svg-images")  # Embed fonts in SVG

    env = os.environ.copy()
    env["DISPLAY"] = ":99"

    logger.debug(f"{correlation_id}:Creating subprocess...")
    process, stdout, stderr = await run_subprocess(cmd, correlation_id)

    logger.debug(f"{correlation_id}:Return code: {process.returncode}")
    logger.debug(f"{correlation_id}:stdout:{stdout.decode()}")
    logger.debug(f"{correlation_id}:stderr:{stderr.decode()}")

    if process.returncode == 0:
        logger.info(f"{correlation_id}:Converted {input_path} to {output_path}")
    else:
        logger.error(
            f"{correlation_id}:Error converting {input_path}:{stderr.decode()}"
        )
        raise RuntimeError(
            f"{correlation_id}:Error converting DrawIO file:{stderr.decode()}"
        )
