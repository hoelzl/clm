import logging
import os
import sys
from pathlib import Path

from clm.infrastructure.services.subprocess_tools import (
    RetryConfig,
    SubprocessCrashError,
    run_subprocess,
)

# Configuration
LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG").upper()
DRAWIO_EXECUTABLE = os.environ.get("DRAWIO_EXECUTABLE", "drawio")

# Retry configuration for DrawIO
# DrawIO/Electron can crash transiently due to V8/GC race conditions,
# so we enable retry on crash with a short delay between attempts.
DRAWIO_RETRY_CONFIG = RetryConfig(
    max_retries=3,
    base_timeout=60,
    retry_on_crash=True,  # Enable retry on non-zero exit codes
    retry_delay=2.0,  # Wait 2 seconds between retries to let resources settle
)

# Set up logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
)
logger = logging.getLogger(__name__)


async def convert_drawio(input_path: Path, output_path: Path, output_format: str, correlation_id):
    """Convert a DrawIO file to the specified output format.

    Args:
        input_path: Path to input DrawIO file
        output_path: Path where output file should be written
        output_format: Output format (png, svg, pdf, etc.)
        correlation_id: Correlation ID for logging

    Raises:
        RuntimeError: If conversion fails after all retry attempts
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

    # Set up environment
    env = os.environ.copy()
    # DISPLAY is only needed on Linux/Unix for X11
    # On Windows, DrawIO uses native GUI and ignores DISPLAY
    if sys.platform != "win32":
        env["DISPLAY"] = ":99"

    logger.debug(f"{correlation_id}:Creating subprocess...")

    try:
        process, stdout, stderr = await run_subprocess(
            cmd, correlation_id, retry_config=DRAWIO_RETRY_CONFIG, env=env
        )
    except SubprocessCrashError as e:
        # All retries exhausted - DrawIO crashed repeatedly
        logger.error(
            f"{correlation_id}:DrawIO crashed after {DRAWIO_RETRY_CONFIG.max_retries} attempts. "
            f"Exit code: {e.return_code}"
        )
        raise RuntimeError(
            f"{correlation_id}:Error converting DrawIO file (crashed after retries):"
            f"{e.stderr.decode(errors='replace')}"
        ) from e

    logger.debug(f"{correlation_id}:Return code: {process.returncode}")
    logger.debug(f"{correlation_id}:stdout:{stdout.decode(errors='replace')}")
    logger.debug(f"{correlation_id}:stderr:{stderr.decode(errors='replace')}")

    if process.returncode == 0:
        logger.info(f"{correlation_id}:Converted {input_path} to {output_path}")
    else:
        # This shouldn't happen with retry_on_crash=True, but handle it just in case
        logger.error(
            f"{correlation_id}:Error converting {input_path}:{stderr.decode(errors='replace')}"
        )
        raise RuntimeError(
            f"{correlation_id}:Error converting DrawIO file:{stderr.decode(errors='replace')}"
        )
