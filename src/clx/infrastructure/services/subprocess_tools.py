import asyncio
import logging

CONVERSION_TIMEOUT = 60
NUM_RETRIES = 3

logger = logging.getLogger(__name__)


class SubprocessError(Exception):
    """Exception raised when subprocess execution fails."""

    pass


async def run_subprocess(cmd, correlation_id):
    """Run a subprocess command with retry logic for transient errors.

    Args:
        cmd: Command and arguments to execute
        correlation_id: ID for tracking this operation in logs

    Returns:
        Tuple of (process, stdout, stderr)

    Raises:
        SubprocessError: If subprocess fails after all retries
        FileNotFoundError: If the command executable is not found (not retriable)
        PermissionError: If permission is denied (not retriable)
    """
    logger.debug(f"{correlation_id}:Waiting for conversion to complete...")

    current_iteration = 0
    last_error = None

    while current_iteration < NUM_RETRIES:
        current_iteration += 1

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            logger.debug(
                f"{correlation_id}:Communicating with subprocess:"
                f"Iteration {current_iteration}: {process.pid}"
            )

            # Exponential timeout backoff
            timeout = CONVERSION_TIMEOUT * 2 ** (current_iteration - 1)
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)

            return process, stdout, stderr

        except asyncio.TimeoutError as e:
            # Timeout is retriable - kill process and retry
            last_error = e
            logger.warning(
                f"{correlation_id}:Subprocess timeout on iteration {current_iteration}/{NUM_RETRIES}. "
                f"Retrying..."
            )
            await try_to_terminate_process(correlation_id, process)

        except (FileNotFoundError, PermissionError) as e:
            # Non-retriable errors - fail immediately with context
            raise SubprocessError(
                f"{correlation_id}:Command failed with non-retriable error: {e}\n"
                f"Command: {' '.join(cmd)}"
            ) from e

        except Exception as e:
            # Unexpected errors - log and fail immediately
            logger.error(
                f"{correlation_id}:Unexpected error in subprocess: {e}",
                exc_info=True,
            )
            await try_to_terminate_process(correlation_id, process)
            raise SubprocessError(
                f"{correlation_id}:Unexpected error while running subprocess: {e}\n"
                f"Command: {' '.join(cmd)}"
            ) from e

    # All retries exhausted
    raise SubprocessError(
        f"{correlation_id}:Subprocess failed after {NUM_RETRIES} attempts\n"
        f"Command: {' '.join(cmd)}\n"
        f"Last error: {last_error}"
    )


async def try_to_terminate_process(correlation_id, process):
    """Attempt to gracefully terminate a subprocess, then force kill if needed.

    Args:
        correlation_id: ID for tracking this operation in logs
        process: The asyncio subprocess to terminate
    """
    try:
        # Try graceful termination first
        process.terminate()
        await asyncio.sleep(2.0)

        # Force kill if still running
        if process.returncode is None:
            process.kill()
            logger.debug(f"{correlation_id}:Process force killed")

    except ProcessLookupError:
        # Process already terminated - this is fine
        logger.debug(f"{correlation_id}:Process already terminated")

    except Exception as e:
        # Log unexpected errors but don't fail
        logger.warning(
            f"{correlation_id}:Error while terminating subprocess: {e}",
            exc_info=True,
        )
